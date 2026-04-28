import datetime


def traded_today(ctx: dict) -> set:
    """당일 거래된 종목 코드 셋 반환. 날짜 바뀌면 자동 초기화."""
    today = datetime.date.today()
    if ctx.get("traded_codes_date") != today:
        ctx["traded_codes"] = set()
        ctx["traded_codes_date"] = today
    return ctx["traded_codes"]
