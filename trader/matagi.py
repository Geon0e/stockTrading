import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_DEFAULT_STAGE_MULTIPLIERS = (1.0, 2.5, 4.0)


def check_matagi_conditions(
    price_client,
    stock_code: str,
    token: str,
    avg_price: float,
    current_price: float,
    drop_pct: float = 2.0,
    matagi_stage: int = 0,
    ma_period: int = 20,
    use_ma_filter: bool = True,
    vol_lookback: int = 5,
    use_rebound_filter: bool = True,
    stage_multipliers: tuple = _DEFAULT_STAGE_MULTIPLIERS,
) -> Tuple[bool, str]:
    """단계형 물타기 조건 확인.

    matagi_stage: 현재까지 물타기 횟수 (0 = 첫 번째 물타기 시도)
    drop_pct: 1차 기준 하락률 (이후 단계는 stage_multipliers 적용)
    """
    needed = ma_period + vol_lookback
    try:
        candles = price_client.fetch_ohlcv(stock_code, needed, token)
    except Exception as e:
        logger.warning(f"[물타기] OHLCV 조회 실패 [{stock_code}]: {e}")
        return False, "OHLCV 데이터 조회 실패"

    if len(candles) < ma_period:
        return False, f"데이터 부족 ({len(candles)}개, {ma_period}개 필요)"

    latest     = candles[-1]
    last_close = latest["close"]
    last_open  = latest["open"]
    last_vol   = latest["volume"]

    # 조건 1: 현재가 > MA (추세 지지)
    if use_ma_filter:
        ma = sum(c["close"] for c in candles[-ma_period:]) / ma_period
        if current_price <= ma:
            return False, f"현재가({current_price:,}) ≤ MA{ma_period}({ma:,.0f}) — 추세 약세"

    # 조건 2: 단계별 하락률
    if avg_price <= 0:
        return False, "매입가 없음"
    multiplier    = stage_multipliers[min(matagi_stage, len(stage_multipliers) - 1)]
    required_drop = drop_pct * multiplier
    stage_label   = f"{matagi_stage + 1}차"
    drop          = (current_price - avg_price) / avg_price * 100
    if drop > -required_drop:
        return False, (
            f"{stage_label} 물타기 하락 부족 ({drop:+.2f}%, 기준 -{required_drop:.1f}%)"
        )

    # 조건 3: 반등 신호 (양봉 OR 거래량 증가)
    if use_rebound_filter:
        is_bullish = last_close > last_open
        prev_vols  = [c["volume"] for c in candles[-(vol_lookback + 1):-1]]
        avg_vol    = sum(prev_vols) / len(prev_vols) if prev_vols else 0
        vol_up     = avg_vol > 0 and last_vol > avg_vol

        if not (is_bullish or vol_up):
            return False, (
                f"반등 신호 없음 ({stage_label}, 음봉, 거래량 {last_vol:,} ≤ 평균 {avg_vol:,.0f})"
            )

        reasons = [stage_label]
        if is_bullish:
            reasons.append("양봉")
        if vol_up:
            reasons.append(f"거래량↑({last_vol:,} > 평균 {avg_vol:,.0f})")
    else:
        reasons = [stage_label, f"하락 {drop:+.2f}%"]

    return True, " + ".join(reasons)
