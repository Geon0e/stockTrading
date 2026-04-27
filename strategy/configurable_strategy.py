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
        and_conds = [(n, c) for n, c in active if str(c.get("조건", "AND")).upper() != "OR"]
        or_conds  = [(n, c) for n, c in active if str(c.get("조건", "AND")).upper() == "OR"]
        for name, cfg in and_conds:
            ok = self._eval_buy(name, cfg, prices)
            logger.debug(f"매수 AND [{name}]: {'✓' if ok else '✗'}")
            if not ok:
                return False
        if or_conds:
            any_ok = any(self._eval_buy(n, c, prices) for n, c in or_conds)
            logger.debug(f"매수 OR 풀: {'✓' if any_ok else '✗'}")
            return any_ok
        return len(and_conds) > 0

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
        active = [(name, cfg) for name, cfg in self._sell.items() if cfg.get("활성화")]
        if not active:
            return False
        and_conds = [(n, c) for n, c in active if str(c.get("조건", "OR")).upper() == "AND"]
        or_conds  = [(n, c) for n, c in active if str(c.get("조건", "OR")).upper() != "AND"]
        for name, cfg in and_conds:
            ok = self._eval_sell(name, cfg, prices)
            logger.debug(f"매도 AND [{name}]: {'✓' if ok else '✗'}")
            if not ok:
                return False
        if or_conds:
            return any(self._eval_sell(n, c, prices) for n, c in or_conds)
        return len(and_conds) > 0

    def _eval_sell(self, name: str, cfg: dict, prices: List[Decimal]) -> bool:
        if "데드크로스" in name:
            short = cfg.get("단기")
            long_ = cfg.get("장기")
            if short is None or long_ is None:
                prefix = "단기" if "단기" in name else "장기"
                buy_cfg = self._buy.get(f"{prefix} 골든크로스", {})
                short = short or buy_cfg.get("단기")
                long_ = long_ or buy_cfg.get("장기")
            if short is None or long_ is None:
                return False
            s1, l1 = sma(prices, short), sma(prices, long_)
            s0, l0 = sma(prices[:-1], short), sma(prices[:-1], long_)
            return s0 >= l0 and s1 < l1

        if "RSI" in name:
            threshold = Decimal(str(cfg.get("매도 기준 이상", 70)))
            period = cfg.get("기간") or self._buy.get("RSI", {}).get("기간", 14)
            return rsi(prices, period) >= threshold

        if "MACD" in name:
            fast = cfg.get("단기") or self._buy.get("MACD", {}).get("단기", 12)
            slow = cfg.get("장기") or self._buy.get("MACD", {}).get("장기", 26)
            sig  = cfg.get("시그널") or self._buy.get("MACD", {}).get("시그널", 9)
            m1, s1 = macd(prices, fast, slow, sig)
            m0, s0 = macd(prices[:-1], fast, slow, sig)
            return m0 >= s0 and m1 < s1

        if "볼린저밴드" in name:
            period = cfg.get("기간") or self._buy.get("볼린저밴드", {}).get("기간", 20)
            std    = cfg.get("표준편차") or self._buy.get("볼린저밴드", {}).get("표준편차", 2.0)
            _, _, upper = bollinger_bands(prices, period, std)
            return prices[-1] >= upper

        return False
