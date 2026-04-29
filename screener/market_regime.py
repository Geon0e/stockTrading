import logging
import time
from typing import Tuple, List

logger = logging.getLogger(__name__)

# 5분 캐시 — 스캔 주기가 짧아도 지수 API 중복 호출 방지
_cache: dict = {"ts": 0.0, "result": (True, "캐시 없음")}
_CACHE_TTL = 300


def _calc_rsi(prices: List[float], period: int) -> float:
    """Wilder 방식 RSI 계산."""
    if len(prices) < period + 1:
        return 50.0  # 데이터 부족 → 중립값
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def check_market_regime(price_client, token: str, config) -> Tuple[bool, str]:
    """시장 국면 필터.

    True  = 매수 허용
    False = 매수 차단 (하락장 / RSI 과매도)

    실패 시 매수 허용으로 폴백하여 봇이 멈추지 않도록 한다.
    """
    if not config.market_regime_enabled:
        return True, "시장필터 비활성"

    # 5분 캐시
    now = time.monotonic()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["result"]

    ma_period  = config.market_regime_ma_period
    rsi_period = config.market_regime_rsi_period
    rsi_min    = config.market_regime_rsi_min
    index_code = config.market_regime_index_code

    needed = max(ma_period if ma_period > 0 else 0,
                 rsi_period + 1 if rsi_period > 0 else 0) + 5
    if needed <= 5:
        return True, "MA/RSI 기간 미설정"

    try:
        raw = price_client.fetch_closing_prices(index_code, needed, token)
    except Exception as e:
        logger.warning(f"[시장필터] 지수({index_code}) 조회 실패: {e} — 매수 허용으로 폴백")
        return True, f"지수 조회 실패(폴백): {e}"

    prices = [float(p) for p in raw]
    current = prices[-1]
    blocked = False
    parts: List[str] = []

    # ── MA 필터 ────────────────────────────────────────────────
    if ma_period > 0 and len(prices) >= ma_period:
        ma = sum(prices[-ma_period:]) / ma_period
        if current > ma:
            parts.append(f"지수({current:,.0f}) > MA{ma_period}({ma:,.0f}) ✓")
        else:
            parts.append(f"지수({current:,.0f}) ≤ MA{ma_period}({ma:,.0f}) ✗")
            blocked = True

    # ── RSI 필터 ───────────────────────────────────────────────
    if rsi_period > 0 and rsi_min > 0 and len(prices) >= rsi_period + 1:
        rsi = _calc_rsi(prices, rsi_period)
        if rsi >= rsi_min:
            parts.append(f"RSI{rsi_period}({rsi:.1f}) ≥ {rsi_min} ✓")
        else:
            parts.append(f"RSI{rsi_period}({rsi:.1f}) < {rsi_min} ✗")
            blocked = True

    reason = " | ".join(parts) if parts else "조건 없음"
    result = (not blocked, reason)
    _cache["ts"] = now
    _cache["result"] = result

    if blocked:
        logger.info(f"[시장필터] 매수 차단: {reason}")
    else:
        logger.debug(f"[시장필터] 매수 허용: {reason}")

    return result
