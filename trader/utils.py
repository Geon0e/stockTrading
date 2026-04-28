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


def _rebuild_daily_from_trades(mode: str, today: datetime.date, budget: int) -> dict:
    """trades_{mode}.jsonl에서 당일 매수/매도 내역을 읽어 예산 현황 재계산."""
    today_str = str(today)
    buy_count = buy_amount = sell_amount = profit_amount = tp_count = tp_amount = 0
    path = _LOG_DIR / f"trades_{mode}.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if not str(r.get("timestamp", "")).startswith(today_str):
                    continue
                qty = int(r.get("quantity", 0))
                price = float(r.get("exec_price") or 0)
                amount = int(price * qty)
                action = r.get("action", "")
                if action == "BUY":
                    buy_count += 1
                    buy_amount += amount
                elif action == "SELL":
                    sell_amount += amount
                    if r.get("profit_amount") is not None:
                        profit_amount += int(r["profit_amount"])
                    elif r.get("profit_rate_pct") is not None and amount > 0:
                        rate = float(r["profit_rate_pct"])
                        profit_amount += int(amount * rate / (100 + rate))
                    if "익절" in str(r.get("signal_type", "")):
                        tp_count += 1
                        tp_amount += amount
            except Exception:
                pass
    return {
        "daily_budget_total":       budget,
        "daily_budget_remaining":   max(0, budget - buy_amount + sell_amount),
        "daily_buy_count":          buy_count,
        "daily_buy_amount":         buy_amount,
        "daily_sell_amount":        sell_amount,
        "daily_profit_amount":      profit_amount,
        "daily_take_profit_count":  tp_count,
        "daily_take_profit_amount": tp_amount,
    }


def init_daily_from_api(ctx: dict, executions: list) -> None:
    """KIS API 체결 내역으로 당일 예산 현황 초기화 (real 모드 전용).

    sll_buy_dvsn_cd: '02'=매수, '01'=매도
    pchs_avg_pric: 매입평균가 (수익금액 계산용)
    """
    import logging
    _logger = logging.getLogger(__name__)
    config = ctx["config"]
    budget = config.real_budget if config.mode == "real" else config.mock_budget
    today = datetime.date.today()
    buy_count = buy_amount = sell_count = sell_amount = 0
    for item in executions:
        side  = item.get("sll_buy_dvsn_cd", "")
        qty   = int(item.get("tot_ccld_qty") or "0")
        price = float(item.get("avg_prvs") or item.get("ccld_avg_pric") or "0")
        amount = int(price * qty)
        if side == "02":    # 매수
            buy_count  += 1
            buy_amount += amount
        elif side == "01":  # 매도
            sell_count  += 1
            sell_amount += amount
    # 수익금액은 로컬 트레이드 로그에서 계산 (profit_amount 필드 또는 profit_rate_pct 역산)
    local = _rebuild_daily_from_trades(config.mode, today, budget)
    ctx["daily_budget_total"]       = budget
    ctx["daily_budget_remaining"]   = max(0, budget - buy_amount + sell_amount)
    ctx["daily_buy_count"]          = buy_count
    ctx["daily_buy_amount"]         = buy_amount
    ctx["daily_sell_amount"]        = sell_amount
    ctx["daily_profit_amount"]      = local["daily_profit_amount"]
    ctx["daily_take_profit_count"]  = local["daily_take_profit_count"]
    ctx["daily_take_profit_amount"] = local["daily_take_profit_amount"]
    ctx["daily_budget_date"]        = today
    _save_daily_status(ctx)
    _logger.info(
        f"[금일현황] KIS API 기준 초기화 | "
        f"매수 {buy_count}건 {buy_amount:,}원 | 매도 {sell_count}건 {sell_amount:,}원 | "
        f"수익 {local['daily_profit_amount']:+,}원 | 잔여예산 {ctx['daily_budget_remaining']:,}원"
    )


def _ensure_daily_budget(ctx: dict) -> None:
    today = datetime.date.today()
    if ctx.get("daily_budget_date") != today:
        config = ctx["config"]
        budget = config.real_budget if config.mode == "real" else config.mock_budget
        stats = _rebuild_daily_from_trades(config.mode, today, budget)
        ctx.update(stats)
        ctx["daily_budget_date"] = today
        _save_daily_status(ctx)


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


def add_daily_budget(ctx: dict, amount: int, is_take_profit: bool = False,
                     profit_amount: int = 0) -> None:
    """매도 시 예산 환원. profit_amount는 당일 실현수익에 가산."""
    lock = ctx.get("budget_lock")
    with (lock if lock else _NullLock()):
        _ensure_daily_budget(ctx)
        amount = int(amount)
        ctx["daily_budget_remaining"]            += amount
        ctx["daily_sell_amount"]                 = ctx.get("daily_sell_amount", 0) + amount
        ctx["daily_profit_amount"]               = ctx.get("daily_profit_amount", 0) + int(profit_amount)
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
            "sell_amount":           ctx.get("daily_sell_amount", 0),
            "profit_amount":         ctx.get("daily_profit_amount", 0),
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
