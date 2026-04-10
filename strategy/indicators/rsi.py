from decimal import Decimal
from typing import List


def rsi(prices: List[Decimal], period: int = 14) -> Decimal:
    """RSI (Relative Strength Index)"""
    if len(prices) < period + 1:
        raise ValueError(f"RSI: {period + 1}개 필요, {len(prices)}개 제공")

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else Decimal(0) for d in deltas]
    losses = [-d if d < 0 else Decimal(0) for d in deltas]

    avg_gain = sum(gains[-period:]) / Decimal(period)
    avg_loss = sum(losses[-period:]) / Decimal(period)

    if avg_loss == 0:
        return Decimal(100)

    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))
