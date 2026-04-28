import datetime
import json
from pathlib import Path

_LOG_DIR = Path("logs")


def traded_today(ctx: dict) -> set:
    """당일 거래된 종목 코드 셋 반환. 날짜 바뀌면 자동 초기화."""
    today = datetime.date.today()
    if ctx.get("traded_codes_date") != today:
        ctx["traded_codes"] = set()
        ctx["traded_codes_date"] = today
    return ctx["traded_codes"]


def _ensure_daily_budget(ctx: dict) -> None:
    today = datetime.date.today()
    if ctx.get("daily_budget_date") != today:
        config = ctx["config"]
        budget = config.real_budget if config.mode == "real" else config.mock_budget
        ctx["daily_budget_total"]         = budget
        ctx["daily_budget_remaining"]     = budget
        ctx["daily_buy_count"]            = 0
        ctx["daily_buy_amount"]           = 0
        ctx["daily_take_profit_count"]    = 0
        ctx["daily_take_profit_amount"]   = 0
        ctx["daily_budget_date"]          = today


def get_daily_budget(ctx: dict) -> int:
    """당일 남은 예산 반환."""
    _ensure_daily_budget(ctx)
    return ctx["daily_budget_remaining"]


def deduct_daily_budget(ctx: dict, amount: int) -> None:
    """매수 시 예산 차감."""
    lock = ctx.get("budget_lock")
    with (lock if lock else _NullLock()):
        _ensure_daily_budget(ctx)
        amount = int(amount)
        ctx["daily_budget_remaining"] = max(0, ctx["daily_budget_remaining"] - amount)
        ctx["daily_buy_amount"]      += amount
        ctx["daily_buy_count"]       += 1
    _save_daily_status(ctx)


def add_daily_budget(ctx: dict, amount: int, is_take_profit: bool = False) -> None:
    """매도 시 예산 환원. is_take_profit=True면 익절 통계도 업데이트."""
    lock = ctx.get("budget_lock")
    with (lock if lock else _NullLock()):
        _ensure_daily_budget(ctx)
        amount = int(amount)
        ctx["daily_budget_remaining"] += amount
        if is_take_profit:
            ctx["daily_take_profit_amount"] += amount
            ctx["daily_take_profit_count"]  += 1
    _save_daily_status(ctx)


def _save_daily_status(ctx: dict) -> None:
    """당일 현황을 파일로 저장 (대시보드에서 읽기용)."""
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        mode = ctx["config"].mode
        data = {
            "date":                  str(ctx.get("daily_budget_date", datetime.date.today())),
            "mode":                  mode,
            "budget_total":          ctx.get("daily_budget_total", 0),
            "budget_remaining":      ctx.get("daily_budget_remaining", 0),
            "buy_count":             ctx.get("daily_buy_count", 0),
            "buy_amount":            ctx.get("daily_buy_amount", 0),
            "take_profit_count":     ctx.get("daily_take_profit_count", 0),
            "take_profit_amount":    ctx.get("daily_take_profit_amount", 0),
        }
        path = _LOG_DIR / f"daily_status_{mode}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *_): pass
