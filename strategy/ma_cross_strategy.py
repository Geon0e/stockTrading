from decimal import Decimal
from typing import List
from .base_strategy import BaseStrategy
from .indicators.moving_average import sma


class MaCrossStrategy(BaseStrategy):
    """이동평균 골든크로스/데드크로스 전략.

    골든크로스 (단기선 > 장기선 상향돌파) → 매수
    데드크로스 (단기선 < 장기선 하향돌파) → 매도
    """

    def __init__(self, short_period: int = 5, long_period: int = 20):
        if short_period >= long_period:
            raise ValueError("단기 이동평균 기간이 장기보다 짧아야 합니다")
        self.short_period = short_period
        self.long_period = long_period

    @property
    def required_data_points(self) -> int:
        return self.long_period + 1  # 교차 감지를 위해 이전봉 비교용 +1

    def should_buy(self, prices: List[Decimal]) -> bool:
        short_now, long_now = self._compute(prices)
        short_prev, long_prev = self._compute(prices[:-1])
        return short_prev <= long_prev and short_now > long_now  # 골든크로스

    def should_sell(self, prices: List[Decimal]) -> bool:
        short_now, long_now = self._compute(prices)
        short_prev, long_prev = self._compute(prices[:-1])
        return short_prev >= long_prev and short_now < long_now  # 데드크로스

    def _compute(self, prices: List[Decimal]):
        return sma(prices, self.short_period), sma(prices, self.long_period)
