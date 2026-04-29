from abc import ABC, abstractmethod
from decimal import Decimal
from typing import List


class BaseStrategy(ABC):
    """모든 매매 전략의 기반 클래스.

    새 전략 추가 시 이 클래스를 상속하고 3개 메서드를 구현하세요.
    """

    @abstractmethod
    def should_buy(self, prices: List[Decimal]) -> bool:
        """매수 신호 여부"""

    @abstractmethod
    def should_sell(self, prices: List[Decimal]) -> bool:
        """매도 신호 여부"""

    @property
    @abstractmethod
    def required_data_points(self) -> int:
        """전략 실행에 필요한 최소 가격 데이터 수"""

    @property
    def volume_filter(self) -> tuple:
        """(enabled: bool, short_period: int, long_period: int) — 거래량 증가 추세 필터 설정."""
        return False, 5, 20

    def get_signal_type(self, prices: List[Decimal]) -> str:
        """should_buy가 True일 때 어떤 트리거가 발동됐는지 반환."""
        return "골든크로스"
