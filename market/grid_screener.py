"""평행채널 그리드 스크리너 (대시보드/CLI 공용).

KOSPI + KOSDAQ 전종목(캐시 목록)을 대상으로, 각 종목의 주봉 평행채널 그리드
(9선) 중 현재가가 그리드선에 근접(±tol)한 종목을 찾는다.
"""
import os
import json
import time
import fcntl
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

# KIS 초당 호출 한도(~20건/초)는 앱키(계좌) 단위라 봇·대시보드 프로세스가 공유한다.
# 따라서 파일 락 기반 '프로세스 간' 페이서로 모든 KIS 호출을 ~15건/초로 균일하게 흘려보낸다.
# (주문·잔고 등 비조회 호출 여유분으로 15건/초. 한도 초과 500·재시도가 사라져 스캔이 빨라진다.)
_RL_MIN_INTERVAL = 1.0 / 15
_RL_FILE = os.path.join(_ROOT, ".token_cache", "kis_rate.lock")


def _rate_limit():
    """파일 락으로 프로세스 간 공유되는 호출 페이서. 마지막 호출 후 최소간격을 보장."""
    try:
        os.makedirs(os.path.dirname(_RL_FILE), exist_ok=True)
        with open(_RL_FILE, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)  # 락 보유 중 sleep → 프로세스 간 직렬화
            try:
                fh.seek(0)
                s = fh.read().strip()
                last = float(s) if s else 0.0
                now = time.time()
                wait = last + _RL_MIN_INTERVAL - now
                if wait > 0:
                    time.sleep(wait)
                    now = time.time()
                fh.seek(0)
                fh.truncate()
                fh.write(repr(now))
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:
        time.sleep(_RL_MIN_INTERVAL)  # 파일 문제 시 최소 간격만이라도 적용


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
        _rate_limit()
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


def _prev_trading_end():
    """스크리닝 기준 종료일 = 전날 (당일 변동 데이터 제외 → 같은 날 결과 안정)."""
    import datetime as _dt
    return _dt.date.today() - _dt.timedelta(days=1)


def grid_match(code, creds, token, tol, weeks):
    """주봉 그리드 9선 중 현재가가 ±tol 근접하면 매칭 dict, 아니면 None."""
    _rate_limit()
    bars = _fetch_candles(code, weeks, "W", creds, token, end_date=_prev_trading_end())
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


def screen(mode="real", tol_pct=0.1, weeks=100, **_ignored):
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
    _rate_limit()
    bars = _fetch_candles(code, weeks, "W", creds, token, end_date=_prev_trading_end())
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
            "rsi": rsi([b["close"] for b in bars])[-1], "volume": bars[-1]["volume"],
            "close": bars[-1]["close"]}


def build_daily_anchors(mode="real", tol_pct=0.1, weeks=100, watch_band_pct=2.0):
    """08:00 1회 호출: 전종목 작도 → 적격(상승채널+RSI 40~65+거래량≥5만) 종목의
    그리드 레벨을 '오늘 고정'으로 저장한다. 장중엔 이 레벨로 근접만 판정.

    watch_band_pct>0이면 '지지선(0~2/8)에서 ±band% 이내'인 종목만 저장한다.
    (장중 봇이 폴링할 후보를 좁혀 KIS 한도 부하·지연을 줄인다. 0이면 전체 저장.)
    """
    import datetime as _dt
    weeks = max(30, min(int(weeks), 120))
    band = max(0.0, float(watch_band_pct)) / 100
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
            # 관심 밴드: 현재가가 지지선(0~2/8)에서 band% 이내인 종목만 (오늘 닿을 가능성)
            if band > 0:
                support = [L["level"] for L in g["levels"] if L["ln"] <= 2 and L["level"] > 0]
                close = g.get("close") or 0
                if not support or not close:
                    return
                if min(abs(close - lv) / lv for lv in support) > band:
                    return
            with lock:
                eligible[code] = {"name": names.get(code, ""), "levels": g["levels"],
                                  "rsi": g["rsi"], "volume": g["volume"], "close": g.get("close")}
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ex.map(scan, codes)

    today = _dt.date.today().isoformat()
    payload = {"date": today, "mode": mode, "tol_pct": float(tol_pct),
               "band_pct": float(watch_band_pct), "built_at": _dt.datetime.now().isoformat(),
               "stocks": eligible}
    path = _anchors_file(mode)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(json.dumps(payload, ensure_ascii=False))

    # 봇 감시목록 = 스크리너 표시목록. 같은 결과를 날짜별 표시용으로도 저장.
    rows = _anchor_rows(eligible)
    screen_payload = {"ok": True, "mode": mode, "tol_pct": float(tol_pct),
                      "band_pct": float(watch_band_pct), "date": today,
                      "saved_at": payload["built_at"], "scanned": len(codes),
                      "count": len(rows), "recommended": len(rows), "results": rows}
    sdir = os.path.join(_ROOT, ".token_cache", "grid_screen")
    os.makedirs(sdir, exist_ok=True)
    open(os.path.join(sdir, f"{mode}_{today}.json"), "w").write(
        json.dumps(screen_payload, ensure_ascii=False))

    return {"ok": True, "count": len(rows), "scanned": len(codes), "date": today,
            "results": rows, "recommended": len(rows),
            "tol_pct": float(tol_pct), "band_pct": float(watch_band_pct)}


def _anchor_rows(stocks: dict) -> list:
    """작도 적격(봇 감시목록) → 스크리너 표시용 행. 지지선(0~2/8) 중 가장 가까운 선 기준."""
    rows = []
    for code, v in stocks.items():
        sup = [L for L in v["levels"] if L["ln"] <= 2 and L["level"] > 0]
        close = v.get("close") or 0
        if not sup or not close:
            continue
        best = min(sup, key=lambda L: abs(close - L["level"]) / L["level"])
        rows.append({
            "code": code, "name": v.get("name", ""), "close": close,
            "volume": v.get("volume"), "line": best["label"], "level": best["level"],
            "dist_pct": round(abs(close - best["level"]) / best["level"] * 100, 2),
            "asc": True, "rsi": v.get("rsi"), "recommended": True, "theme": "",
        })
    rows.sort(key=lambda x: x["dist_pct"])  # 지지선에 가까운 순
    return rows


def _fetch_current_price(code, creds, token):
    """현재가(체결가) 조회. 실패 시 None."""
    _rate_limit()
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


def recommended_candidates(mode="real", tol_pct=0.1, weeks=100, exclude=()):
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
                              "signal_detected_at": stamp, "market": "KR",
                              "grid_ln": best["ln"], "grid_levels": valid})
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=WORKERS) as ex2:
        ex2.map(check, stocks)
    return cands
