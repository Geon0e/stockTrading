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
from notifications.kakao_notifier import from_env as kakao_from_env, notify_buy, notify_sell, notify_scan_result

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
        holdings = ctx["order_client"].get_holdings(token)

        bot = ctx.get("kakao_bot")

        # 1. 보유 종목 데드크로스 체크 → 매도
        for stock_code, qty in list(holdings.items()):
            prices = ctx["price_client"].fetch_closing_prices(
                stock_code, ctx["strategy"].required_data_points, token
            )
            if ctx["strategy"].should_sell(prices):
                result = ctx["order_client"].sell(stock_code, qty, token)
                ctx["trade_logger"].log("SELL", stock_code, qty, result)
                del holdings[stock_code]
                logger.info(f"매도 완료: {stock_code}")
                if bot:
                    notify_sell(bot, stock_code, qty, prices[-1])

        # 2. 최대 보유 종목 수 미만이면 골든크로스 종목 스캔 → 매수
        if len(holdings) < config.max_positions:
            buy_slots = config.max_positions - len(holdings)
            candidates = ctx["screener"].scan(
                token, all_stocks=config.scan_all_stocks
            )
            if candidates and bot:
                notify_scan_result(bot, candidates)

            bought = 0
            for candidate in candidates:
                if bought >= buy_slots:
                    break
                code = candidate["code"]
                if code in holdings:
                    continue
                result = ctx["order_client"].buy(code, config.order_quantity, token)
                ctx["trade_logger"].log("BUY", code, config.order_quantity, result)
                holdings[code] = config.order_quantity
                bought += 1
                if bot:
                    notify_buy(bot, code, config.order_quantity, candidate["price"])

            if bought == 0 and len(candidates) == 0:
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

    strategy = MaCrossStrategy(config.ma_short_period, config.ma_long_period)
    price_client = PriceClient(config)
    kakao_bot = kakao_from_env()
    if kakao_bot:
        logger.info("카카오톡 알림 활성화")
    else:
        logger.info("카카오톡 알림 비활성화 (KAKAO_REST_API_KEY 미설정)")

    ctx = {
        "config": config,
        "token_manager": TokenManager(config),
        "price_client": price_client,
        "order_client": OrderClient(config),
        "strategy": strategy,
        "screener": StockScreener(config, price_client, strategy),
        "trade_logger": TradeLogger(config.mode),
        "kakao_bot": kakao_bot,
    }

    schedule.every(config.check_interval_minutes).minutes.do(run_cycle, ctx)
    run_cycle(ctx)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
