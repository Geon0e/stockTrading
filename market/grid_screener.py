"""평행채널 그리드 스크리너 (대시보드/CLI 공용).

KOSPI + KOSDAQ 전종목(캐시 목록)을 대상으로, 각 종목의 주봉 평행채널 그리드
(9선) 중 현재가가 그리드선에 근접(±tol)한 종목을 찾는다.
"""
import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from auth.token_manager import TokenManager
from market.weekly_chart import _creds, _headers, _fetch_candles, compute_drawing, rsi

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CODES_FILE = os.path.join(_ROOT, ".token_cache", "stock_list.json")
_NAMES_FILE = os.path.join(_ROOT, ".token_cache", "stock_names.json")
_SECTORS_FILE = os.path.join(_ROOT, ".token_cache", "stock_sectors.json")

MIN_BARS = 30
WORKERS = 8


def _load_sectors() -> dict:
    if os.path.exists(_SECTORS_FILE):
        try:
            return json.loads(open(_SECTORS_FILE).read())
        except Exception:
            return {}
    return {}


def _fetch_sector(code, creds, token) -> str:
    """업종명(bstp_kor_isnm) 조회. KIS는 투기 테마가 아닌 업종 분류를 제공."""
    try:
        url = f"{creds.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        r = requests.get(url, headers=_headers(creds, token, "FHKST01010100"),
                         params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}, timeout=10)
        return ((r.json().get("output", {}) or {}).get("bstp_kor_isnm", "") or "").strip()
    except Exception:
        return ""


def _enrich_sectors(results, creds, token) -> None:
    """매칭 종목에 업종(테마) 채움. 캐시 미스만 조회 후 파일 캐시 갱신."""
    cache = _load_sectors()
    missing = [x["code"] for x in results if not cache.get(x["code"])]
    if missing:
        lock = threading.Lock()

        def f(code):
            s = _fetch_sector(code, creds, token)
            time.sleep(0.03)
            if s:
                with lock:
                    cache[code] = s

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            ex.map(f, missing)
        try:
            open(_SECTORS_FILE, "w").write(json.dumps(cache, ensure_ascii=False))
        except Exception:
            pass
    for x in results:
        x["theme"] = cache.get(x["code"], "")


def _line_num(line: str) -> int:
    """'채널 3/8' → 3 (그리드선 번호; 낮을수록 채널 바닥/지지)."""
    try:
        return int(line.split()[1].split("/")[0])
    except Exception:
        return 9


def _all_codes() -> list:
    """캐시된 KOSPI + KOSDAQ 전종목 코드 (.token_cache/stock_list.json)."""
    if not os.path.exists(_CODES_FILE):
        return []
    try:
        return json.loads(open(_CODES_FILE).read()).get("codes", [])
    except Exception:
        return []


def grid_match(code, creds, token, tol, weeks):
    """주봉 그리드 9선 중 현재가가 ±tol 근접하면 매칭 dict, 아니면 None."""
    bars = _fetch_candles(code, weeks, "W", creds, token)
    if len(bars) < MIN_BARS:
        return None
    grid = [l for l in compute_drawing(bars)["lines"] if l["group"] == "grid"]
    if not grid:
        return None
    n = len(bars)
    close = bars[-1]["close"]
    levels = [{"label": l["label"], "value": l["start_val"] + l["slope"] * (n - 1 - l["start_i"]),
               "slope": l["slope"]} for l in grid]
    best = min(levels, key=lambda L: abs(close - L["value"]) / L["value"] if L["value"] > 0 else 9)
    if best["value"] <= 0:
        return None
    dist = abs(close - best["value"]) / best["value"]
    if dist > tol:
        return None
    return {
        "code": code, "close": close, "line": best["label"],
        "level": round(best["value"]), "dist_pct": round(dist * 100, 2),
        "asc": best["slope"] > 0, "rsi": rsi([b["close"] for b in bars])[-1],
        "volume": bars[-1]["volume"],  # 최근 주봉 거래량
    }


def screen(mode="real", tol_pct=0.5, weeks=100, **_ignored):
    """KOSPI+KOSDAQ 전종목 그리드 근접 스크리닝. 결과 dict 반환."""
    tol = float(tol_pct) / 100
    weeks = max(30, min(int(weeks), 120))
    creds = _creds(mode)
    token = TokenManager(creds).get_valid_token()

    codes = _all_codes()
    if not codes:
        return {"ok": False, "error": "종목 목록 캐시(.token_cache/stock_list.json)가 없습니다"}

    names = json.loads(open(_NAMES_FILE).read())
    results = []
    lock = threading.Lock()

    def scan(code):
        try:
            m = grid_match(code, creds, token, tol, weeks)
            time.sleep(0.03)  # 스레드별 rate limit 완화
            if m:
                m["name"] = names.get(code, "")
                with lock:
                    results.append(m)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ex.map(scan, codes)

    _enrich_sectors(results, creds, token)  # 업종(테마) 보강 (매칭 종목만)

    # 추천(별표) 판정: 상승채널 + 지지권(0~2/8) + RSI 40~65 + 거래량 5만+
    for x in results:
        ln = _line_num(x["line"])
        x["recommended"] = (
            x["asc"] and ln <= 2
            and x["rsi"] is not None and 40 <= x["rsi"] <= 65
            and (x.get("volume") or 0) >= 50000
        )
        x["_ln"] = ln

    # 추천 우선 → 상승채널 우선 → 그리드 낮은(지지)순 → 근접순
    results.sort(key=lambda x: (not x["recommended"], not x["asc"], x["_ln"], x["dist_pct"]))
    for x in results:
        x.pop("_ln", None)

    return {
        "ok": True, "mode": mode, "tol_pct": tol_pct, "weeks": weeks,
        "scanned": len(codes), "count": len(results),
        "recommended": sum(1 for x in results if x["recommended"]), "results": results,
    }


# ── 매수 후보 (봇 연동) ───────────────────────────────────────────────────
# 흐름:
#  1) 08:00 build_daily_anchors(): 전종목 주봉 그리드를 한 번만 계산해, 적격
#     (상승채널 + RSI 40~65 + 거래량≥5만) 종목의 그리드 레벨을 '오늘 고정'으로 저장.
#  2) 장중 recommended_candidates(): 저장된 적격종목만 현재가를 조회해, 고정된
#     상승 빗각(지지권 0~2/8선)에 ±tol 근접하면 매수 후보로 반환.
# → 빗각(작도)은 하루 한 번만 그리고, 가격 체크만 주기적으로 반복한다.

def _anchors_file(mode: str) -> str:
    return os.path.join(_ROOT, ".token_cache", f"grid_anchors_{mode}.json")


def _load_anchors(mode: str):
    path = _anchors_file(mode)
    if not os.path.exists(path):
        return None
    try:
        return json.loads(open(path).read())
    except Exception:
        return None


def _compute_grid_levels(code, creds, token, weeks):
    """종목의 주봉 그리드 9선 '오늘 위치 레벨' + asc + rsi + 최근주봉 거래량. 없으면 None."""
    bars = _fetch_candles(code, weeks, "W", creds, token)
    if len(bars) < MIN_BARS:
        return None
    grid = [l for l in compute_drawing(bars)["lines"] if l["group"] == "grid"]
    if not grid:
        return None
    n = len(bars)
    levels = [{"ln": _line_num(l["label"]), "label": l["label"],
               "level": round(l["start_val"] + l["slope"] * (n - 1 - l["start_i"]))}
              for l in grid]
    return {"levels": levels, "asc": grid[0]["slope"] > 0,
            "rsi": rsi([b["close"] for b in bars])[-1], "volume": bars[-1]["volume"]}


def build_daily_anchors(mode="real", tol_pct=0.5, weeks=100):
    """08:00 1회 호출: 전종목 작도 → 적격(상승채널+RSI 40~65+거래량≥5만) 종목의
    그리드 레벨을 '오늘 고정'으로 저장한다. 장중엔 이 레벨로 근접만 판정."""
    import datetime as _dt
    weeks = max(30, min(int(weeks), 120))
    creds = _creds(mode)
    token = TokenManager(creds).get_valid_token()
    codes = _all_codes()
    if not codes:
        return {"ok": False, "error": "종목 목록 캐시(.token_cache/stock_list.json)가 없습니다"}
    names = json.loads(open(_NAMES_FILE).read())

    eligible = {}
    lock = threading.Lock()

    def scan(code):
        try:
            g = _compute_grid_levels(code, creds, token, weeks)
            time.sleep(0.03)
            if not g or not g["asc"]:
                return
            if g["rsi"] is None or not (40 <= g["rsi"] <= 65) or (g["volume"] or 0) < 50000:
                return
            with lock:
                eligible[code] = {"name": names.get(code, ""), "levels": g["levels"],
                                  "rsi": g["rsi"], "volume": g["volume"]}
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ex.map(scan, codes)

    payload = {"date": _dt.date.today().isoformat(), "mode": mode,
               "tol_pct": float(tol_pct), "built_at": _dt.datetime.now().isoformat(),
               "stocks": eligible}
    path = _anchors_file(mode)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(json.dumps(payload, ensure_ascii=False))
    return {"ok": True, "count": len(eligible), "scanned": len(codes), "date": payload["date"]}


def _fetch_current_price(code, creds, token):
    """현재가(체결가) 조회. 실패 시 None."""
    url = f"{creds.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
    r = requests.get(url, headers=_headers(creds, token, "FHKST01010100"),
                     params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}, timeout=10)
    p = (r.json().get("output", {}) or {}).get("stck_prpr")
    return int(p) if p and str(p).isdigit() else None


def anchors_status(mode="real"):
    """08:00 작도 상태 요약 (대시보드/로그용)."""
    import datetime as _dt
    a = _load_anchors(mode)
    if not a:
        return {"ready": False}
    return {"ready": a.get("date") == _dt.date.today().isoformat(),
            "date": a.get("date"), "built_at": a.get("built_at"),
            "count": len(a.get("stocks", {})), "tol_pct": a.get("tol_pct")}


def recommended_candidates(mode="real", tol_pct=0.5, weeks=100, exclude=()):
    """08:00 고정 그리드 레벨에 현재가가 근접한 상승채널·지지권(0~2/8) 적격종목을
    매수 후보로 반환. 08:00 작도가 없거나 날짜가 다르면 빈 리스트(매수 안 함)."""
    import datetime as _dt
    anchors = _load_anchors(mode)
    today = _dt.date.today().isoformat()
    if not anchors or anchors.get("date") != today or not anchors.get("stocks"):
        return []
    tol = float(anchors.get("tol_pct", tol_pct)) / 100
    ex = set(exclude or ())
    stocks = [(c, v) for c, v in anchors["stocks"].items() if c not in ex]
    if not stocks:
        return []

    creds = _creds(mode)
    token = TokenManager(creds).get_valid_token()
    stamp = _dt.datetime.now().isoformat()
    cands = []
    lock = threading.Lock()

    def check(item):
        code, v = item
        try:
            price = _fetch_current_price(code, creds, token)
            time.sleep(0.02)
            if not price:
                return
            levels = v.get("levels") or []
            valid = [L for L in levels if L["level"] > 0]
            if not valid:
                return
            best = min(valid, key=lambda L: abs(price - L["level"]) / L["level"])
            if best["ln"] > 2:  # 지지권(0~2/8)만
                return
            if abs(price - best["level"]) / best["level"] > tol:  # ±tol 근접
                return
            with lock:
                cands.append({"code": code, "name": v.get("name", ""), "price": price,
                              "signal_type": f"그리드 {best['label']}",
                              "signal_detected_at": stamp, "market": "KR"})
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=WORKERS) as ex2:
        ex2.map(check, stocks)
    return cands
