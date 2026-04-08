from decimal import Decimal
from typing import List


def sma(prices: List[Decimal], period: int) -> Decimal:
    """단순 이동평균 (Simple Moving Average)"""
    if len(prices) < period:
        raise ValueError(f"SMA: {period}개 필요, {len(prices)}개 제공")
    return sum(prices[-period:]) / Decimal(period)


def ema(prices: List[Decimal], period: int) -> Decimal:
    """지수 이동평균 (Exponential Moving Average)"""
    if len(prices) < period:
        raise ValueError(f"EMA: {period}개 필요, {len(prices)}개 제공")
    multiplier = Decimal(2) / Decimal(period + 1)
    val = sum(prices[:period]) / Decimal(period)  # 초기값: 첫 period개의 SMA
    for price in prices[period:]:
        val = (price - val) * multiplier + val
    return val
