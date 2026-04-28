import datetime


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
        ctx["daily_budget_remaining"] = budget
        ctx["daily_budget_date"] = today


def get_daily_budget(ctx: dict) -> int:
    """당일 남은 예산 반환."""
    _ensure_daily_budget(ctx)
    return ctx["daily_budget_remaining"]


def deduct_daily_budget(ctx: dict, amount: int) -> None:
    """매수 시 예산 차감. budget_lock이 있으면 사용."""
    lock = ctx.get("budget_lock")
    with (lock if lock else _NullLock()):
        _ensure_daily_budget(ctx)
        ctx["daily_budget_remaining"] = max(0, ctx["daily_budget_remaining"] - int(amount))


def add_daily_budget(ctx: dict, amount: int) -> None:
    """익절 매도 시 예산 환원. budget_lock이 있으면 사용."""
    lock = ctx.get("budget_lock")
    with (lock if lock else _NullLock()):
        _ensure_daily_budget(ctx)
        ctx["daily_budget_remaining"] += int(amount)


class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *_): pass
