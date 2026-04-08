import logging
import datetime
import schedule
import time
from config import load_config
from auth.token_manager import TokenManager
from market.price_client import PriceClient
from order.order_client import OrderClient
from strategy.ma_cross_strategy import MaCrossStrategy
from audit.trade_logger import TradeLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

KST = datetime.timezone(datetime.timedelta(hours=9))


def is_market_open() -> bool:
    now = datetime.datetime.now(KST)
    if now.weekday() >= 5:  # 토/일
        return False
    open_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_time <= now <= close_time


def run_cycle(ctx: dict) -> None:
    if not is_market_open():
        logger.info("장 시간 외 — 건너뜀")
        return

    config = ctx["config"]
    try:
        token = ctx["token_manager"].get_valid_token()
        prices = ctx["price_client"].fetch_closing_prices(
            config.target_stock, ctx["strategy"].required_data_points, token
        )
        holdings = ctx["order_client"].get_holdings(token)
        holding_qty = holdings.get(config.target_stock, 0)

        if ctx["strategy"].should_buy(prices) and holding_qty == 0:
            result = ctx["order_client"].buy(config.target_stock, config.order_quantity, token)
            ctx["trade_logger"].log("BUY", config.target_stock, config.order_quantity, result)

        elif ctx["strategy"].should_sell(prices) and holding_qty > 0:
            result = ctx["order_client"].sell(config.target_stock, holding_qty, token)
            ctx["trade_logger"].log("SELL", config.target_stock, holding_qty, result)

        else:
            logger.info(
                f"[{config.mode}] {config.target_stock} 매매 신호 없음 | 보유: {holding_qty}주"
            )

    except Exception as e:
        logger.error(f"사이클 오류: {e}", exc_info=True)


def main() -> None:
    config = load_config()
    logger.info(
        f"모드: {config.mode.upper()} | 종목: {config.target_stock} | "
        f"계좌: {config.account_no} | 주기: {config.check_interval_minutes}분"
    )

    ctx = {
        "config": config,
        "token_manager": TokenManager(config),
        "price_client": PriceClient(config),
        "order_client": OrderClient(config),
        "strategy": MaCrossStrategy(config.ma_short_period, config.ma_long_period),
        "trade_logger": TradeLogger(config.mode),
    }

    schedule.every(config.check_interval_minutes).minutes.do(run_cycle, ctx)
    run_cycle(ctx)  # 시작 즉시 1회 실행

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
