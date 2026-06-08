"""주봉/일봉 작도 차트 데이터 빌더.

작도선(피치포크·추세선·채널)은 **주봉을 기준으로 한 번만** 계산하고, 각 선을
(날짜, 가격) 두 앵커로 표현한다. 프론트엔드는 일봉/주봉 어느 타임프레임을 보든
같은 앵커에 맞춰 직선을 다시 그리므로, 주봉으로 그은 작도가 일봉에서도
부합하는지 곧바로 검증할 수 있다. 피보나치는 가격 수평선이라 두 타임프레임 공통.

`analysis` 패키지는 누락 모듈을 import하는 __init__ 때문에 import 불가하므로
정상 import되는 `market` 패키지에 둔다.
"""
import os
import datetime
from types import SimpleNamespace

import requests
from dotenv import load_dotenv

from auth.token_manager import TokenManager

_CHART_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_CHART_TR_ID = "FHKST03010100"  # 모의/실제 공통 (기간별 시세, 호출당 최대 ~100행)
_INFO_ENDPOINT = "/uapi/domestic-stock/v1/quotations/search-stock-info"
_INFO_TR_ID = "CTPF1604R"

_MODE_BASE = {
    "mock": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}


def _creds(mode: str):
    """요청 모드에 맞는 자격증명 묶음. Config 전체를 만들지 않고 필요한 4개만."""
    if mode not in _MODE_BASE:
        raise ValueError(f"mode는 mock 또는 real이어야 합니다: {mode}")
    load_dotenv(override=False)
    prefix = "MOCK" if mode == "mock" else "REAL"
    app_key = os.getenv(f"{prefix}_APP_KEY", "")
    app_secret = os.getenv(f"{prefix}_APP_SECRET", "")
    if not app_key or not app_secret:
        raise RuntimeError(f"{prefix}_APP_KEY / {prefix}_APP_SECRET 환경변수가 설정되지 않았습니다")
    return SimpleNamespace(
        mode=mode, base_url=_MODE_BASE[mode], app_key=app_key, app_secret=app_secret
    )


def _headers(creds, token: str, tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": creds.app_key,
        "appsecret": creds.app_secret,
        "tr_id": tr_id,
    }


def _fetch_candles(code: str, count: int, period: str, creds, token: str) -> list[dict]:
    """OHLCV를 오래된 순으로 반환. period: 'D'(일봉) | 'W'(주봉).

    KIS 호출당 최대 ~100행 → count가 크면 종료일을 앞당겨 가며 페이지네이션한다.
    """
    url = f"{creds.base_url}{_CHART_ENDPOINT}"
    end = datetime.date.today()
    safety = datetime.date.today() - datetime.timedelta(days=365 * 4)
    acc: dict[str, dict] = {}  # date -> bar (페이지 겹침 중복 제거)
    is_first = True

    while len(acc) < count:
        window = (count + 10) * 7 if period == "W" else 150  # 주봉: 한 번에 충분 / 일봉: ~100거래일
        start = end - datetime.timedelta(days=window)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = requests.get(url, headers=_headers(creds, token, _CHART_TR_ID), params=params, timeout=10)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            if is_first:
                raise
            break  # 페이지네이션 중 실패 = 상장 이전 소급 → 종료
        data = resp.json()
        if data.get("rt_cd") != "0":
            if is_first:
                raise RuntimeError(f"{period}봉 조회 실패 [{code}]: {data.get('msg1')} ({data.get('msg_cd')})")
            break

        batch = []
        for row in data.get("output2", []):
            c = row.get("stck_clpr")
            d = row.get("stck_bsop_date")
            if not c or not d or c == "0":
                continue
            batch.append((d, {
                "open": int(row["stck_oprc"]),
                "high": int(row["stck_hgpr"]),
                "low": int(row["stck_lwpr"]),
                "close": int(c),
                "volume": int(row.get("acml_vol") or 0),
            }))
        if not batch:
            break

        new = 0
        for d, bar in batch:
            if d not in acc:
                acc[d] = bar
                new += 1
        is_first = False

        if period == "W":
            break  # 주봉은 한 번 호출로 충분
        oldest = min(d for d, _ in batch)
        end = datetime.datetime.strptime(oldest, "%Y%m%d").date() - datetime.timedelta(days=1)
        if new == 0 or end < safety:
            break

    bars = [{"date": d, **bar} for d, bar in sorted(acc.items())]
    return bars[-count:]


def fetch_name(stock_code: str, creds, token: str) -> str:
    """종목명 조회. 실패 시 빈 문자열."""
    try:
        params = {"PRDT_TYPE_CD": "300", "PDNO": stock_code}
        url = f"{creds.base_url}{_INFO_ENDPOINT}"
        resp = requests.get(url, headers=_headers(creds, token, _INFO_TR_ID), params=params, timeout=10)
        resp.raise_for_status()
        out = resp.json().get("output", {}) or {}
        return (out.get("prdt_abrv_name") or out.get("prdt_name") or "").strip()
    except Exception:
        return ""


def rsi(closes: list[int], period: int = 14) -> list[float | None]:
    """Wilder RSI. 길이는 closes와 동일, 초기 period개는 None."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
            avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
        if avg_l == 0:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = round(100 - 100 / (1 + rs), 1)
    return out


def find_pivots(bars: list[dict], left: int = 3, right: int = 3) -> dict:
    """좌우 left/right봉 대비 극값인 스윙 고점/저점."""
    highs, lows = [], []
    n = len(bars)
    for i in range(left, n - right):
        win = bars[i - left:i + right + 1]
        if bars[i]["high"] == max(b["high"] for b in win) and bars[i]["high"] > bars[i - 1]["high"]:
            highs.append({"i": i, "price": bars[i]["high"]})
        if bars[i]["low"] == min(b["low"] for b in win) and bars[i]["low"] < bars[i - 1]["low"]:
            lows.append({"i": i, "price": bars[i]["low"]})
    return {"highs": highs, "lows": lows}


def compute_drawing(bars: list[dict]) -> dict:
    """주봉 기준 작도선을 계산. 각 선은 시작 인덱스/시작값/주당 기울기로 표현한다.

    (프론트엔드가 두 앵커를 임의 타임프레임의 봉 인덱스에 매핑해 다시 그릴 수 있도록
     build_chart에서 (날짜, 가격) 앵커로 변환된다.)
    """
    n = len(bars)
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    hi_idx = max(range(n), key=lambda i: highs[i])              # 절대 고점
    p1_idx = min(range(0, hi_idx + 1), key=lambda i: lows[i])   # 고점 이전 최저
    p3_idx = (min(range(hi_idx + 1, n), key=lambda i: lows[i])  # 고점 이후 최저
              if hi_idx < n - 1 else None)

    lines: list[dict] = []

    def add(label, color, dash, start_i, start_val, slope, group="draw"):
        lines.append({"label": label, "color": color, "dash": dash, "group": group,
                      "start_i": start_i, "start_val": start_val, "slope": slope})

    # ── 앤드류스 피치포크 (저-고-저) ──
    if p3_idx is not None and p3_idx != p1_idx:
        i1, p1 = p1_idx, lows[p1_idx]
        i2, p2 = hi_idx, highs[hi_idx]
        i3, p3 = p3_idx, lows[p3_idx]
        i_mid, p_mid = (i2 + i3) / 2, (p2 + p3) / 2
        if i_mid != i1:
            m = (p_mid - p1) / (i_mid - i1)
            median_at = lambda i: p1 + m * (i - i1)
            add("피치포크 상단", "#f59e0b", False, i1, p1 + (p2 - median_at(i2)), m)
            add("피치포크 중심", "#fbbf24", True,  i1, p1, m)
            add("피치포크 하단", "#f59e0b", False, i1, p1 + (p3 - median_at(i3)), m)

    pivots = find_pivots(bars)

    # ── 지지 추세선 + 평행 채널 상단 (최근 스윙 저점 2개) ──
    if len(pivots["lows"]) >= 2:
        a, b = pivots["lows"][-2], pivots["lows"][-1]
        if b["i"] != a["i"]:
            slope = (b["price"] - a["price"]) / (b["i"] - a["i"])
            add("지지 추세선", "#22c55e", False, a["i"], a["price"], slope)
            seg = range(a["i"], n)
            top = max(seg, key=lambda i: highs[i])
            off = highs[top] - (a["price"] + slope * (top - a["i"]))
            if off > 0:
                add("채널 상단(평행)", "#16a34a", True, a["i"], a["price"] + off, slope)
                # ── 등간격 평행선 그리드 (사용자 작도 재현) ──
                # 바닥(지지선)~천장(고점 평행선)을 8등분한 평행선 9개.
                _DIV = 8
                _GRID_COLORS = ["#22c55e", "#84cc16", "#eab308", "#f59e0b", "#ec4899",
                                "#a855f7", "#ef4444", "#3b82f6", "#9ca3af"]
                for k in range(_DIV + 1):
                    add(f"채널 {k}/{_DIV}", _GRID_COLORS[k], False,
                        a["i"], a["price"] + off * k / _DIV, slope, group="grid")

    # ── 저항 추세선 (최근 스윙 고점 2개) ──
    if len(pivots["highs"]) >= 2:
        a, b = pivots["highs"][-2], pivots["highs"][-1]
        if b["i"] != a["i"]:
            slope = (b["price"] - a["price"]) / (b["i"] - a["i"])
            add("저항 추세선", "#ef4444", False, a["i"], a["price"], slope)

    # ── 피보나치 되돌림 (p1 저점 → 절대 고점) ──
    lo_p, hi_p = lows[p1_idx], highs[hi_idx]
    rng = hi_p - lo_p
    fib = [{"label": f"Fib {r:.3f}", "ratio": r, "price": round(hi_p - rng * r)}
           for r in (0.236, 0.382, 0.5, 0.618, 0.786)] if rng > 0 else []

    return {"lines": lines, "fib": fib, "n": n,
            "anchor": {"low_i": p1_idx, "low": lo_p, "high_i": hi_idx, "high": hi_p}}


def _iso(d: str) -> str:
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


def _series(bars: list[dict]) -> dict:
    """봉 리스트 → 프론트엔드 캔들/거래량/RSI 시계열."""
    closes = [b["close"] for b in bars]
    rsis = rsi(closes)
    candles = [{"time": _iso(b["date"]), "open": b["open"], "high": b["high"],
                "low": b["low"], "close": b["close"]} for b in bars]
    # 한국식: 상승=빨강, 하락=파랑
    volumes = [{"time": _iso(b["date"]), "value": b["volume"],
                "color": "#ef444455" if b["close"] >= b["open"] else "#3b82f655"} for b in bars]
    # 캔들과 logical index를 맞추려고 모든 봉의 time 포함(초기 RSI 없는 구간은 whitespace)
    rsi_series = [({"time": _iso(bars[i]["date"]), "value": rsis[i]} if rsis[i] is not None
                   else {"time": _iso(bars[i]["date"])})
                  for i in range(len(bars))]
    return {
        "candles": candles, "volumes": volumes, "rsi": rsi_series,
        "count": len(bars), "first": _iso(bars[0]["date"]), "last": _iso(bars[-1]["date"]),
        "lastClose": bars[-1]["close"], "lastRsi": rsis[-1],
    }


def build_chart(code: str, weeks: int = 100, mode: str = "real") -> dict:
    """주봉+일봉 + 주봉 기준 작도선(앵커)을 일괄 생성."""
    if not (code and code.isdigit() and len(code) == 6):
        raise ValueError("종목코드는 6자리 숫자여야 합니다")
    weeks = max(20, min(int(weeks), 120))
    days = min(weeks * 5, 700)  # 주봉 기간과 같은 달력 범위를 일봉으로 커버

    creds = _creds(mode)
    token = TokenManager(creds).get_valid_token()

    weekly_bars = _fetch_candles(code, weeks, "W", creds, token)
    if not weekly_bars:
        raise RuntimeError("주봉 데이터를 찾을 수 없습니다 (종목코드 확인)")
    daily_bars = _fetch_candles(code, days, "D", creds, token)

    draw = compute_drawing(weekly_bars)

    # 작도선을 (날짜, 가격) 두 앵커로 변환 — 주봉 시작점과 주봉 마지막점.
    n = len(weekly_bars)
    lines = []
    for l in draw["lines"]:
        si = l["start_i"]
        ei = n - 1
        a_date = _iso(weekly_bars[si]["date"])
        b_date = _iso(weekly_bars[ei]["date"])
        a_val = round(l["start_val"])
        b_val = round(l["start_val"] + l["slope"] * (ei - si))
        lines.append({"label": l["label"], "color": l["color"], "dash": l["dash"],
                      "group": l.get("group", "draw"),
                      "a": {"time": a_date, "value": a_val},
                      "b": {"time": b_date, "value": b_val}})

    weekly = _series(weekly_bars)
    daily = _series(daily_bars) if daily_bars else None

    return {
        "code": code,
        "name": fetch_name(code, creds, token),
        "mode": mode,
        "weekly": weekly,
        "daily": daily,
        "lines": lines,
        "fib": draw["fib"],
        "periodHigh": max(b["high"] for b in weekly_bars),
        "periodLow": min(b["low"] for b in weekly_bars),
    }
