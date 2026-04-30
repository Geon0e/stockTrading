import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_DEFAULT_STAGE_MULTIPLIERS = (1.0, 2.0, 3.0)


def check_bultagi_conditions(
    price_client,
    stock_code: str,
    token: str,
    avg_price: float,
    current_price: float,
    profit_pct: float = 3.0,
    bultagi_stage: int = 0,
    ma_period: int = 20,
    use_ma_filter: bool = True,
    stage_multipliers: tuple = _DEFAULT_STAGE_MULTIPLIERS,
) -> Tuple[bool, str]:
    """단계형 불타기 조건 확인.

    bultagi_stage: 현재까지 불타기 횟수 (0 = 첫 번째 불타기 시도)
    profit_pct: 1차 기준 수익률 (이후 단계는 stage_multipliers 적용)
    """
    try:
        candles = price_client.fetch_ohlcv(stock_code, ma_period, token)
    except Exception as e:
        logger.info(f"[불타기] OHLCV 조회 실패 [{stock_code}]: {e}")
        return False, "OHLCV 데이터 조회 실패"

    if len(candles) < ma_period:
        return False, f"데이터 부족 ({len(candles)}개, {ma_period}개 필요)"

    # 조건 1: 현재가 > MA (상승추세 유지)
    if use_ma_filter:
        ma = sum(c["close"] for c in candles[-ma_period:]) / ma_period
        if current_price <= ma:
            return False, f"현재가({current_price:,}) ≤ MA{ma_period}({ma:,.0f}) — 추세 약세"

    # 조건 2: 단계별 수익률
    if avg_price <= 0:
        return False, "매입가 없음"
    multiplier      = stage_multipliers[min(bultagi_stage, len(stage_multipliers) - 1)]
    required_profit = profit_pct * multiplier
    stage_label     = f"{bultagi_stage + 1}차"
    gain            = (current_price - avg_price) / avg_price * 100
    if gain < required_profit:
        return False, (
            f"{stage_label} 불타기 수익 부족 ({gain:+.2f}%, 기준 +{required_profit:.1f}%)"
        )

    return True, f"{stage_label} + 수익 {gain:+.2f}% ≥ +{required_profit:.1f}%"
