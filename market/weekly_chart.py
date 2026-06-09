"""주봉/일봉 작도 차트 데이터 빌더.

작도선(피치포크·추세선·채널)은 **주봉을 기준으로 한 번만** 계산하고, 각 선을
(날짜, 가격) 두 앵커로 표현한다. 프론트엔드는 일봉/주봉 어느 타임프레임을 보든
같은 앵커에 맞춰 직선을 다시 그리므로, 주봉으로 그은 작도가 일봉에서도
부합하는지 곧바로 검증할 수 있다. 피보나치는 가격 수평선이라 두 타임프레임 공통.

`analysis` 패키지는 누락 모듈을 import하는 __init__ 때문에 import 불가하므로
정상 import되는 `market` 패키지에 둔다.
"""
import os
import time
import calendar
import datetime
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import requests
from dotenv import load_dotenv

from auth.token_manager import TokenManager

_CHART_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_CHART_TR_ID = "FHKST03010100"  # 모의/실제 공통 (기간별 시세, 호출당 최대 ~100행)
_INFO_ENDPOINT = "/uapi/domestic-stock/v1/quotations/search-stock-info"
_INFO_TR_ID = "CTPF1604R"
_MINUTE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_TR_ID = "FHKST03010230"  # 일별 분봉 (호출당 ~120행, 종료시각 이전으로 페이지네이션)

_MODE_BASE = {
    "mock": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}

# 작도선은 절대 고/저가에 매달려 새 봉마다 흔들리므로, 한 번 잡은 앵커를
# 프로세스 메모리에 고정해두고 극값이 이 비율을 넘게 갱신될 때만 다시 잡는다.
_PIN_THRESHOLD = 0.01  # 1%
_PINNED: dict[str, dict] = {}


def _pin_key(mode: str, code: str, weeks: int) -> str:
    return f"{mode}:{code}:{weeks}"


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


def _get_retry(url, headers, params, timeout=10, retries=3, backoff=0.6):
    """5xx/연결 오류 시 재시도하는 GET (KIS 간헐적 500 대응).

    재시도 후에도 5xx면 마지막 응답을 그대로 반환 → 호출자가 raise_for_status로 처리.
    연결 자체가 계속 실패하면 마지막 예외를 raise.
    """
    resp = None
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code < 500:
                return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            resp, last_exc = None, e
        if attempt < retries - 1:
            time.sleep(backoff * (2 ** attempt))
    if resp is None:
        raise last_exc if last_exc else requests.ConnectionError(f"KIS 연결 실패 (재시도 {retries}회)")
    return resp


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
        resp = _get_retry(url, _headers(creds, token, _CHART_TR_ID), params)
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


def _anchor_lines(weekly_bars: list[dict], draw_lines: list[dict]) -> list[dict]:
    """compute_drawing의 (시작 인덱스·기울기) 선을 (날짜, 가격) 두 앵커로 변환.

    앵커는 주봉 시작점과 주봉 마지막점. 프론트엔드가 두 앵커를 임의 타임프레임의
    봉 인덱스에 매핑해 직선을 다시 그린다.
    """
    n = len(weekly_bars)
    lines = []
    for l in draw_lines:
        si = l["start_i"]
        ei = n - 1
        lines.append({
            "label": l["label"], "color": l["color"], "dash": l["dash"],
            "group": l.get("group", "draw"),
            "a": {"time": _iso(weekly_bars[si]["date"]), "value": round(l["start_val"])},
            "b": {"time": _iso(weekly_bars[ei]["date"]),
                  "value": round(l["start_val"] + l["slope"] * (ei - si))},
        })
    return lines


def build_chart(code: str, weeks: int = 100, mode: str = "real", refresh: bool = False) -> dict:
    """주봉+일봉 + 주봉 기준 작도선(앵커)을 일괄 생성.

    작도선·피보나치는 절대 고/저가에 매달려 새 봉마다 흔들리므로, 한 번 잡은
    앵커를 프로세스 메모리(_PINNED)에 고정해두고 새 극값이 _PIN_THRESHOLD를
    넘게 갱신될 때만 다시 계산한다. refresh=True면 무조건 다시 잡는다.
    """
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

    period_high = max(b["high"] for b in weekly_bars)
    period_low = min(b["low"] for b in weekly_bars)
    week_first = _iso(weekly_bars[0]["date"])

    # ── 작도 기준점 고정 ──
    # 캐시된 앵커가 (1) 현재 조회 창 안에 있고 (2) 극값이 임계 이내면 그대로 재사용.
    key = _pin_key(mode, code, weeks)
    pin = None if refresh else _PINNED.get(key)
    reuse = bool(pin
                 and pin["minAnchor"] >= week_first
                 and period_high <= pin["anchorHigh"] * (1 + _PIN_THRESHOLD)
                 and period_low >= pin["anchorLow"] * (1 - _PIN_THRESHOLD))
    if reuse:
        lines, fib = pin["lines"], pin["fib"]
    else:
        draw = compute_drawing(weekly_bars)
        lines = _anchor_lines(weekly_bars, draw["lines"])
        fib = draw["fib"]
        _PINNED[key] = {
            "anchorHigh": period_high, "anchorLow": period_low,
            "minAnchor": min((l["a"]["time"] for l in lines), default=week_first),
            "lines": lines, "fib": fib,
        }

    weekly = _series(weekly_bars)
    daily = _series(daily_bars) if daily_bars else None

    return {
        "code": code,
        "name": fetch_name(code, creds, token),
        "mode": mode,
        "weekly": weekly,
        "daily": daily,
        "lines": lines,
        "fib": fib,
        "pinned": reuse,
        "periodHigh": period_high,
        "periodLow": period_low,
    }


# ── 분봉(1시간봉) ──────────────────────────────────────────────────────────
# KIS는 시간봉을 직접 주지 않으므로 일별 분봉(1분)을 받아 시간 단위로 합친다.
# 차트(lightweight-charts) 인트라데이 시계열은 유닉스 타임스탬프(초)를 쓴다.
# KST 시각을 그대로 보여주려고 KST 시계를 UTC인 것처럼 timegm으로 변환한다.

def _kst_ts(yyyymmdd: str, hour: int) -> int:
    y, m, d = int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8])
    return calendar.timegm((y, m, d, hour, 0, 0, 0, 0, 0))


def _fetch_day_minutes(code: str, yyyymmdd: str, creds, token: str) -> list[dict]:
    """하루치 1분봉을 오래된→최신 순으로 반환 (장중 09:00~15:30, 종료시각 이전으로 페이지네이션)."""
    url = f"{creds.base_url}{_MINUTE_ENDPOINT}"
    acc: dict[str, dict] = {}
    end_hour = "153000"
    for _ in range(8):  # 하루 ~390분 / 120행 → 4~5콜 + 여유
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": end_hour, "FID_INPUT_DATE_1": yyyymmdd,
            "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N",
        }
        rows = None
        for attempt in range(4):  # 초당 거래건수 초과/일시 오류 재시도 (조기 종료로 봉 누락 방지)
            resp = requests.get(url, headers=_headers(creds, token, _MINUTE_TR_ID), params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("rt_cd") == "0":
                    rows = data.get("output2") or []
                    break
            time.sleep(0.3 * (attempt + 1))
        if rows is None:
            break  # 재시도 후에도 실패 → 종료
        if not rows:
            break  # 더 이상 데이터 없음
        new = 0
        for row in rows:
            h = row.get("stck_cntg_hour")
            c = row.get("stck_prpr")
            if not h or not c or c == "0":
                continue
            if h not in acc:
                acc[h] = {
                    "hour": h,
                    "open": int(row["stck_oprc"]), "high": int(row["stck_hgpr"]),
                    "low": int(row["stck_lwpr"]), "close": int(c),
                    "volume": int(row.get("cntg_vol") or 0),
                }
                new += 1
        oldest = min(acc)  # HHMMSS 문자열 = 시간순
        if new == 0 or oldest <= "090000":
            break
        t = int(oldest[:2]) * 60 + int(oldest[2:4]) - 1  # 다음 페이지: 1분 전
        if t < 9 * 60:
            break
        end_hour = f"{t // 60:02d}{t % 60:02d}00"
        time.sleep(0.05)  # rate limit 여유
    return [acc[h] for h in sorted(acc)]


def _bucket_hour(hhmmss: str, interval_min: int) -> int:
    """분봉의 체결시각 → 봉이 속한 시간버킷의 시(hour).

    1시간봉: 시각의 시 그대로. 4시간봉: 국내장 6.5h를 09:00~13:00 / 13:00~15:30 두 봉으로.
    """
    if interval_min >= 240:
        return 9 if hhmmss < "130000" else 13
    return int(hhmmss[:2])


def _aggregate(yyyymmdd: str, minutes: list[dict], interval_min: int) -> list[dict]:
    """1분봉 → interval봉 (버킷; open=첫분, close=막분, high/low=구간, vol=합)."""
    buckets: dict[int, dict] = {}
    for m in minutes:  # 오래된→최신 순
        h = _bucket_hour(m["hour"], interval_min)
        b = buckets.get(h)
        if b is None:
            buckets[h] = {k: m[k] for k in ("open", "high", "low", "close", "volume")}
        else:
            b["high"] = max(b["high"], m["high"])
            b["low"] = min(b["low"], m["low"])
            b["close"] = m["close"]
            b["volume"] += m["volume"]
    return [{"ts": _kst_ts(yyyymmdd, h), **buckets[h]} for h in sorted(buckets)]


def _series_intraday(bars: list[dict]) -> dict:
    closes = [b["close"] for b in bars]
    rsis = rsi(closes)
    candles = [{"time": b["ts"], "open": b["open"], "high": b["high"],
                "low": b["low"], "close": b["close"]} for b in bars]
    volumes = [{"time": b["ts"], "value": b["volume"],
                "color": "#ef444455" if b["close"] >= b["open"] else "#3b82f655"} for b in bars]
    rsi_series = [({"time": bars[i]["ts"], "value": rsis[i]} if rsis[i] is not None
                   else {"time": bars[i]["ts"]}) for i in range(len(bars))]
    return {
        "candles": candles, "volumes": volumes, "rsi": rsi_series,
        "count": len(bars),
        "lastClose": bars[-1]["close"] if bars else None,
        "lastRsi": rsis[-1] if rsis else None,
    }


def build_intraday(code: str, mode: str = "real", days: int = 7, interval_min: int = 60) -> dict:
    """최근 `days` 거래일의 시간봉(1h/4h) 시계열. (작도선은 build_chart 응답을 재사용)"""
    if not (code and code.isdigit() and len(code) == 6):
        raise ValueError("종목코드는 6자리 숫자여야 합니다")
    days = max(1, min(int(days), 40))
    interval_min = 240 if int(interval_min) >= 240 else 60

    creds = _creds(mode)
    token = TokenManager(creds).get_valid_token()

    today = datetime.date.today()
    cand, d = [], today
    while len(cand) < days + 4 and (today - d).days < days * 2 + 14:
        if d.weekday() < 5:  # 주말 제외 (공휴일은 빈 응답으로 자연 제외)
            cand.append(d.strftime("%Y%m%d"))
        d -= datetime.timedelta(days=1)

    def fetch(ds):
        return ds, _aggregate(ds, _fetch_day_minutes(code, ds, creds, token), interval_min)

    with ThreadPoolExecutor(max_workers=4) as ex:  # KIS 초당 한도 고려
        results = list(ex.map(fetch, cand))

    have = sorted([(ds, bars) for ds, bars in results if bars], key=lambda x: x[0])[-days:]
    bars = sorted((b for _, bars in have for b in bars), key=lambda b: b["ts"])
    if not bars:
        raise RuntimeError("시간봉 데이터를 찾을 수 없습니다 (장 시작 전이거나 거래 없음)")

    return {
        "code": code, "mode": mode,
        "name": fetch_name(code, creds, token),
        "hourly": _series_intraday(bars),
        "days": len(have),
        "interval": interval_min,
    }
