import json
import logging
import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_LOG_DIR = Path("logs")


class TradeLogger:
    def __init__(self, mode: str):
        self._mode = mode
        _LOG_DIR.mkdir(exist_ok=True)
        self._log_file = _LOG_DIR / f"trades_{mode}.jsonl"

    def log(self, action: str, stock_code: str, quantity: int, result: dict) -> None:
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "mode": self._mode,
            "action": action,
            "stock_code": stock_code,
            "quantity": quantity,
            "order_no": result.get("output", {}).get("ODNO", ""),
            "rt_cd": result.get("rt_cd"),
            "message": result.get("msg1"),
        }
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info(f"[감사로그] {action} | {stock_code} {quantity}주 기록 완료")
