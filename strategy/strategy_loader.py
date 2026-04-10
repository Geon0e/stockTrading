import re
from pathlib import Path
from typing import Any, Dict


def load_strategy_config(path: str = "STRATEGY.md") -> Dict[str, Any]:
    """STRATEGY.md 파싱 → {buy: {지표명: {설정키: 값}}, sell: {...}}"""
    text = Path(path).read_text(encoding="utf-8")

    config: Dict[str, Any] = {"buy": {}, "sell": {}}
    section: str | None = None
    indicator: str | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(">") or line == "---":
            continue

        if line.startswith("## 매수"):
            section, indicator = "buy", None
        elif line.startswith("## 매도"):
            section, indicator = "sell", None
        elif line.startswith("### ") and section:
            indicator = line[4:].strip()
            config[section][indicator] = {}
        elif line.startswith("- ") and section and indicator:
            m = re.match(r"- (.+?):\s*(.+)", line)
            if m:
                config[section][indicator][m.group(1).strip()] = _cast(m.group(2).strip())

    return config


def _cast(val: str) -> Any:
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
