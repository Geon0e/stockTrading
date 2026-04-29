import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_MA_PERIOD    = 20
_VOL_LOOKBACK = 5
# 단계별 drop_pct 배율: 1차×1.0(2%), 2차×2.5(5%), 3차×4.0(8%) — base=2% 기준
_STAGE_MULTIPLIERS = [1.0, 2.5, 4.0]


def check_matagi_conditions(
    price_client,
    stock_code: str,
    token: str,
    avg_price: float,
    current_price: float,
    drop_pct: float = 2.0,
    matagi_stage: int = 0,
) -> Tuple[bool, str]:
    """단계형 물타기 조건 확인.

    matagi_stage: 현재까지 물타기 횟수 (0 = 첫 번째 물타기 시도)
    drop_pct: 1차 기준 하락률 (2차=×2.5, 3차=×4.0 자동 적용)
    조건 1: 현재가 > MA20 (추세 지지)
    조건 2: 현재 단계 기준 하락률 이상 하락
    조건 3: 양봉 또는 거래량 증가 (반등 신호)
    """
    needed = _MA_PERIOD + _VOL_LOOKBACK
    try:
        candles = price_client.fetch_ohlcv(stock_code, needed, token)
    except Exception as e:
        logger.warning(f"[물타기] OHLCV 조회 실패 [{stock_code}]: {e}")
        return False, "OHLCV 데이터 조회 실패"

    if len(candles) < _MA_PERIOD:
        return False, f"데이터 부족 ({len(candles)}개, {_MA_PERIOD}개 필요)"

    latest     = candles[-1]
    last_close = latest["close"]
    last_open  = latest["open"]
    last_vol   = latest["volume"]

    # 조건 1: 현재가 > MA20
    ma20 = sum(c["close"] for c in candles[-_MA_PERIOD:]) / _MA_PERIOD
    if current_price <= ma20:
        return False, f"현재가({current_price:,}) ≤ MA20({ma20:,.0f}) — 추세 약세"

    # 조건 2: 단계별 하락률
    if avg_price <= 0:
        return False, "매입가 없음"
    multiplier    = _STAGE_MULTIPLIERS[min(matagi_stage, len(_STAGE_MULTIPLIERS) - 1)]
    required_drop = drop_pct * multiplier
    stage_label   = f"{matagi_stage + 1}차"
    drop          = (current_price - avg_price) / avg_price * 100
    if drop > -required_drop:
        return False, (
            f"{stage_label} 물타기 하락 부족 ({drop:+.2f}%, 기준 -{required_drop:.1f}%)"
        )

    # 조건 3: 반등 신호 (양봉 OR 거래량 증가)
    is_bullish = last_close > last_open
    prev_vols  = [c["volume"] for c in candles[-(_VOL_LOOKBACK + 1):-1]]
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

    return True, " + ".join(reasons)
