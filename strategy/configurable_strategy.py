import logging
from decimal import Decimal
from typing import Any, Dict, List

from .base_strategy import BaseStrategy
from .indicators.moving_average import sma
from .indicators.rsi import rsi
from .indicators.macd import macd
from .indicators.bollinger import bollinger_bands

logger = logging.getLogger(__name__)


class ConfigurableStrategy(BaseStrategy):
    """STRATEGY.md 설정을 읽어 동작하는 전략.

    매수: 활성화된 조건 모두 충족 (AND)
    매도: 활성화된 조건 하나라도 충족 (OR)
    """

    def __init__(self, config: Dict[str, Any]):
        self._buy  = config.get("buy", {})
        self._sell = config.get("sell", {})

    @property
    def required_data_points(self) -> int:
        needed = [21]  # 기본값
        for cfg in (self._buy, self._sell):
            for settings in cfg.values():
                if not settings.get("활성화", False):
                    continue
                long_  = settings.get("장기", 0)
                period = settings.get("기간", 0)
                signal = settings.get("시그널", 0)
                if long_ and signal:
                    needed.append(long_ + signal + 2)
                elif long_:
                    needed.append(long_ + 2)
                elif period:
                    needed.append(period + 2)
        return max(needed)

    # ── 매수 ────────────────────────────────────────────
    def should_buy(self, prices: List[Decimal]) -> bool:
        active = [(name, cfg) for name, cfg in self._buy.items() if cfg.get("활성화")]
        if not active:
            return False
        results = []
        for name, cfg in active:
            ok = self._eval_buy(name, cfg, prices)
            logger.debug(f"매수 조건 [{name}]: {'✓' if ok else '✗'}")
            results.append(ok)
        return all(results)

    def _eval_buy(self, name: str, cfg: dict, prices: List[Decimal]) -> bool:
        if "골든크로스" in name:
            short, long_ = cfg.get("단기"), cfg.get("장기")
            if short is None or long_ is None:
                return False
            s1, l1 = sma(prices, short), sma(prices, long_)
            s0, l0 = sma(prices[:-1], short), sma(prices[:-1], long_)
            return s0 <= l0 and s1 > l1

        if name == "RSI":
            threshold = Decimal(str(cfg.get("매수 기준 이하", 30)))
            return rsi(prices, cfg.get("기간", 14)) <= threshold

        if name == "MACD":
            fast, slow, sig = cfg.get("단기", 12), cfg.get("장기", 26), cfg.get("시그널", 9)
            m1, s1 = macd(prices, fast, slow, sig)
            m0, s0 = macd(prices[:-1], fast, slow, sig)
            return m0 <= s0 and m1 > s1  # 골든크로스

        if "볼린저밴드" in name:
            lower, _, _ = bollinger_bands(prices, cfg.get("기간", 20), cfg.get("표준편차", 2.0))
            return prices[-1] <= lower

        return False

    # ── 매도 ────────────────────────────────────────────
    def should_sell(self, prices: List[Decimal]) -> bool:
        for name, cfg in self._sell.items():
            if not cfg.get("활성화"):
                continue
            ok = self._eval_sell(name, cfg, prices)
            if ok:
                logger.debug(f"매도 조건 충족 [{name}]")
                return True
        return False

    def _eval_sell(self, name: str, cfg: dict, prices: List[Decimal]) -> bool:
        if "데드크로스" in name:
            # "단기 데드크로스" → "단기 골든크로스" 설정 참조
            # "장기 데드크로스" → "장기 골든크로스" 설정 참조
            prefix = "단기" if "단기" in name else "장기"
            buy_cfg = self._buy.get(f"{prefix} 골든크로스", {})
            short = buy_cfg.get("단기") or cfg.get("단기")
            long_ = buy_cfg.get("장기") or cfg.get("장기")
            if short is None or long_ is None:
                return False
            s1, l1 = sma(prices, short), sma(prices, long_)
            s0, l0 = sma(prices[:-1], short), sma(prices[:-1], long_)
            return s0 >= l0 and s1 < l1

        if "RSI" in name:
            buy_cfg = self._buy.get("RSI", {})
            period = buy_cfg.get("기간", cfg.get("기간", 14))
            threshold = Decimal(str(cfg.get("매도 기준 이상", 70)))
            return rsi(prices, period) >= threshold

        if "MACD" in name:
            buy_cfg = self._buy.get("MACD", {})
            fast = buy_cfg.get("단기", 12)
            slow = buy_cfg.get("장기", 26)
            sig  = buy_cfg.get("시그널", 9)
            m1, s1 = macd(prices, fast, slow, sig)
            m0, s0 = macd(prices[:-1], fast, slow, sig)
            return m0 >= s0 and m1 < s1  # 데드크로스

        if "볼린저밴드" in name:
            buy_cfg = self._buy.get("볼린저밴드", {})
            period = buy_cfg.get("기간", cfg.get("기간", 20))
            std    = buy_cfg.get("표준편차", cfg.get("표준편차", 2.0))
            _, _, upper = bollinger_bands(prices, period, std)
            return prices[-1] >= upper

        return False
