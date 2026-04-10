from decimal import Decimal
from typing import List, Tuple
from .moving_average import ema


def macd(prices: List[Decimal], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[Decimal, Decimal]:
    """MACD 라인과 시그널 라인 반환 (현재 기준)"""
    required = slow + signal
    if len(prices) < required:
        raise ValueError(f"MACD: {required}개 필요, {len(prices)}개 제공")

    # MACD 시리즈 생성 (slow 번째부터 끝까지)
    macd_series: List[Decimal] = []
    for end in range(slow, len(prices) + 1):
        e_fast = ema(prices[:end], fast)
        e_slow = ema(prices[:end], slow)
        macd_series.append(e_fast - e_slow)

    signal_line = ema(macd_series, signal)
    return macd_series[-1], signal_line
