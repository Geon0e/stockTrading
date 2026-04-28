import json
import logging
import datetime
from pathlib import Path
from typing import Optional
from screener.name_lookup import get_stock_name

logger = logging.getLogger(__name__)
_LOG_DIR = Path("logs")


class TradeLogger:
    def __init__(self, mode: str):
        self._mode = mode
        _LOG_DIR.mkdir(exist_ok=True)
        self._log_file = _LOG_DIR / f"trades_{mode}.jsonl"

    def log(self, action: str, stock_code: str, quantity: int, result: dict,
            signal_type: str = "", signal_detected_at: str = "",
            exec_price: str = "", exec_confirmed_at: str = "",
            profit_rate=None, profit_amount: int = None) -> None:
        now = datetime.datetime.now().isoformat()
        name = get_stock_name(stock_code)
        entry = {
            "timestamp":          now,
            "mode":               self._mode,
            "action":             action,
            "stock_code":         stock_code,
            "stock_name":         name,
            "quantity":           quantity,
            "signal_type":        signal_type,
            "signal_detected_at": signal_detected_at or now,
            "order_no":           result.get("output", {}).get("ODNO", ""),
            "order_placed_at":    now,
            "exec_price":         exec_price,
            "exec_confirmed_at":  exec_confirmed_at,
            "rt_cd":              result.get("rt_cd"),
            "message":            result.get("msg1"),
        }
        if profit_rate is not None:
            entry["profit_rate_pct"] = float(profit_rate)
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        label = f"{stock_code}({name})" if name else stock_code
        logger.info(f"[감사로그] {action} | {label} {quantity}주 | 신호: {signal_type}")
