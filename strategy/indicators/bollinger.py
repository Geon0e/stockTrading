from decimal import Decimal
from typing import List, Tuple


def bollinger_bands(prices: List[Decimal], period: int = 20, std_dev: float = 2.0) -> Tuple[Decimal, Decimal, Decimal]:
    """(하단밴드, 중간선, 상단밴드) 반환"""
    if len(prices) < period:
        raise ValueError(f"볼린저밴드: {period}개 필요, {len(prices)}개 제공")

    recent = prices[-period:]
    mean = sum(recent) / Decimal(period)
    variance = sum((p - mean) ** 2 for p in recent) / Decimal(period)
    std = variance.sqrt()
    multiplier = Decimal(str(std_dev))

    return mean - multiplier * std, mean, mean + multiplier * std
