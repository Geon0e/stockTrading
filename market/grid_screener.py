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


def screen(mode="real", tol_pct=1.5, weeks=100, **_ignored):
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
# 전종목 스캔은 3~5분 걸리므로 결과를 캐시한다. 봇이 매 사이클마다 재스캔하지 않게.
_CAND_CACHE = {"ts": 0.0, "key": None, "candidates": None}
_CAND_TTL = 1800  # 30분 (주봉 기반이라 장중 거의 안 바뀜)


def recommended_candidates(mode="real", tol_pct=1.5, weeks=100, exclude=()):
    """⭐ 추천 종목을 봇 매수 후보 dict 리스트로 반환 (30분 캐시).

    반환 형식: [{code, name, price, signal_type, signal_detected_at, market}, ...]
    """
    import datetime as _dt
    now = time.time()
    key = (mode, float(tol_pct), int(weeks))
    if (_CAND_CACHE["key"] == key and _CAND_CACHE["candidates"] is not None
            and now - _CAND_CACHE["ts"] < _CAND_TTL):
        cands = _CAND_CACHE["candidates"]
    else:
        res = screen(mode=mode, tol_pct=tol_pct, weeks=weeks)
        cands = []
        if res.get("ok"):
            stamp = _dt.datetime.now().isoformat()
            for x in res["results"]:
                if not x.get("recommended"):
                    continue
                cands.append({
                    "code": x["code"], "name": x.get("name", ""),
                    "price": x["close"], "signal_type": f"그리드 {x['line']}",
                    "signal_detected_at": stamp, "market": "KR",
                })
        _CAND_CACHE.update(ts=now, key=key, candidates=cands)

    ex = set(exclude or ())
    return [c for c in cands if c["code"] not in ex]
