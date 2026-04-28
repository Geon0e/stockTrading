import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_MA_PERIOD    = 20   # MA20 기준
_VOL_LOOKBACK = 5    # 거래량 비교 기준 일수


def check_matagi_conditions(
    price_client,
    stock_code: str,
    token: str,
    avg_price: float,
    current_price: float,
    drop_pct: float = 1.5,
) -> Tuple[bool, str]:
    """물타기 추가 조건 3가지를 모두 확인.

    조건 1: 현재가 > MA20 (추세 지지 확인)
    조건 2: 첫 매수 대비 -drop_pct% 이상 하락 (충분한 눌림목)
    조건 3: 반등 캔들(양봉) 또는 거래량 증가 (반등 신호)

    Returns:
        (True, 통과사유) 또는 (False, 실패사유)
    """
    needed = _MA_PERIOD + _VOL_LOOKBACK
    try:
        candles = price_client.fetch_ohlcv(stock_code, needed, token)
    except Exception as e:
        logger.warning(f"[물타기] OHLCV 조회 실패 [{stock_code}]: {e}")
        return False, "OHLCV 데이터 조회 실패"

    if len(candles) < _MA_PERIOD:
        return False, f"데이터 부족 ({len(candles)}개, {_MA_PERIOD}개 필요)"

    latest   = candles[-1]
    last_close = latest["close"]
    last_open  = latest["open"]
    last_vol   = latest["volume"]

    # 조건 1: 현재가 > MA20
    ma20 = sum(c["close"] for c in candles[-_MA_PERIOD:]) / _MA_PERIOD
    if current_price <= ma20:
        return False, f"현재가({current_price:,}) ≤ MA20({ma20:,.0f}) — 추세 약세"

    # 조건 2: 첫 매수 대비 -drop_pct% 이상 하락
    if avg_price <= 0:
        return False, "매입가 없음"
    drop = (current_price - avg_price) / avg_price * 100
    if drop > -drop_pct:
        return False, f"하락 부족 ({drop:+.2f}%, 기준 -{drop_pct}%)"

    # 조건 3: 반등 캔들(양봉) OR 거래량 증가
    is_bullish = last_close > last_open
    prev_vols  = [c["volume"] for c in candles[-(_VOL_LOOKBACK + 1):-1]]
    avg_vol    = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vol_up     = avg_vol > 0 and last_vol > avg_vol

    if not (is_bullish or vol_up):
        return False, (
            f"반등 신호 없음 (음봉, 거래량 {last_vol:,} ≤ {_VOL_LOOKBACK}일 평균 {avg_vol:,.0f})"
        )

    reasons = []
    if is_bullish:
        reasons.append("양봉")
    if vol_up:
        reasons.append(f"거래량↑({last_vol:,} > 평균 {avg_vol:,.0f})")

    return True, " + ".join(reasons)
