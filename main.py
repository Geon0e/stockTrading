import logging
import datetime
import schedule
import time
from config import load_config
from auth.token_manager import TokenManager
from market.price_client import PriceClient
from order.order_client import OrderClient
from strategy.ma_cross_strategy import MaCrossStrategy
from screener.stock_screener import StockScreener
from audit.trade_logger import TradeLogger
from notifications.telegram_notifier import from_env as telegram_from_env
from notifications.telegram_notifier import notify_buy as tg_notify_buy
from notifications.telegram_notifier import notify_sell as tg_notify_sell
from notifications.telegram_notifier import notify_scan_result as tg_notify_scan
from notifications.telegram_notifier import notify_take_profit_sell as tg_notify_take_profit_sell

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

KST = datetime.timezone(datetime.timedelta(hours=9))


def is_market_open() -> bool:
    now = datetime.datetime.now(KST)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_time <= now <= close_time


def _notify_buy(ctx, code, quantity, price):
    if ctx.get("telegram_bot"):
        tg_notify_buy(ctx["telegram_bot"], code, quantity, price)


def _notify_sell(ctx, code, quantity, price):
    if ctx.get("telegram_bot"):
        tg_notify_sell(ctx["telegram_bot"], code, quantity, price)


def _notify_take_profit_sell(ctx, code, quantity, profit_rate):
    if ctx.get("kakao_bot"):
        kakao_notify_take_profit_sell(ctx["kakao_bot"], code, quantity, profit_rate)
    if ctx.get("telegram_bot"):
        tg_notify_take_profit_sell(ctx["telegram_bot"], code, quantity, profit_rate)


def _notify_scan(ctx, results):
    if ctx.get("telegram_bot"):
        tg_notify_scan(ctx["telegram_bot"], results)


def run_take_profit_cycle(ctx: dict) -> None:
    """1분마다 실행: 수익률 5% 이상 보유 종목 익절 매도"""
    if not is_market_open():
        return

    config = ctx["config"]
    try:
        token = ctx["token_manager"].get_valid_token()
        holdings_detail = ctx["order_client"].get_holdings_detail(token)

        for code, detail in list(holdings_detail.items()):
            profit_rate = detail["profit_rate"]
            if profit_rate >= config.take_profit_rate:
                qty = detail["qty"]
                result = ctx["order_client"].sell(code, qty, token)
                ctx["trade_logger"].log("SELL", code, qty, result, profit_rate=profit_rate)
                _notify_take_profit_sell(ctx, code, qty, profit_rate)
                logger.info(f"익절 매도: {code} | 수익률 {profit_rate}%")

    except Exception as e:
        logger.error(f"익절 사이클 오류: {e}", exc_info=True)


def run_cycle(ctx: dict) -> None:
    if not is_market_open():
        logger.info("장 시간 외 — 건너뜀")
        return

    config = ctx["config"]
    try:
        token           = ctx["token_manager"].get_valid_token()
        holdings_detail = ctx["order_client"].get_holdings_detail(token)
        holdings        = {code: d["qty"] for code, d in holdings_detail.items()}

        # 1. 보유 종목 데드크로스 체크 → 매도
        for stock_code, qty in list(holdings.items()):
            prices = ctx["price_client"].fetch_closing_prices(
                stock_code, ctx["strategy"].required_data_points, token
            )
            if ctx["strategy"].should_sell(prices):
                profit_rate = holdings_detail[stock_code]["profit_rate"]
                result = ctx["order_client"].sell(stock_code, qty, token)
                ctx["trade_logger"].log("SELL", stock_code, qty, result, profit_rate=profit_rate)
                del holdings[stock_code]
                _notify_sell(ctx, stock_code, qty, prices[-1])
                logger.info(f"매도 완료: {stock_code} | 수익률 {profit_rate:+}%")

        # 2. 빈 슬롯 있으면 골든크로스 스캔 → 매수
        if len(holdings) < config.max_positions:
            candidates = ctx["screener"].scan(token, all_stocks=config.scan_all_stocks)

            if candidates:
                _notify_scan(ctx, candidates)

            bought = 0
            for candidate in candidates:
                if bought >= config.max_positions - len(holdings):
                    break
                code = candidate["code"]
                if code in holdings:
                    continue
                result = ctx["order_client"].buy(code, config.order_quantity, token)
                ctx["trade_logger"].log("BUY", code, config.order_quantity, result)
                holdings[code] = config.order_quantity
                _notify_buy(ctx, code, config.order_quantity, candidate["price"])
                bought += 1

            if not candidates:
                logger.info(f"[{config.mode}] 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    except Exception as e:
        logger.error(f"사이클 오류: {e}", exc_info=True)


def main() -> None:
    config = load_config()
    scan_mode = "전종목" if config.scan_all_stocks else "거래량 상위"
    logger.info(
        f"모드: {config.mode.upper()} | 스캔: {scan_mode} | "
        f"최대보유: {config.max_positions}개 | 주기: {config.check_interval_minutes}분"
    )

    strategy     = MaCrossStrategy(config.ma_short_period, config.ma_long_period)
    price_client = PriceClient(config)
    telegram_bot = telegram_from_env()

    logger.info(f"텔레그램 알림: {'활성화' if telegram_bot else '비활성화'}")

    ctx = {
        "config":        config,
        "token_manager": TokenManager(config),
        "price_client":  price_client,
        "order_client":  OrderClient(config),
        "strategy":      strategy,
        "screener":      StockScreener(config, price_client, strategy),
        "trade_logger":  TradeLogger(config.mode),
        "telegram_bot":  telegram_bot,
    }

    schedule.every(config.check_interval_minutes).minutes.do(run_cycle, ctx)
    schedule.every(1).minutes.do(run_take_profit_cycle, ctx)
    run_cycle(ctx)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
