import logging
import datetime
import schedule
import time
from config import load_config
from screener.name_lookup import get_stock_name
from auth.token_manager import TokenManager
from market.price_client import PriceClient
from order.order_client import OrderClient
from strategy.ma_cross_strategy import MaCrossStrategy
from strategy.configurable_strategy import ConfigurableStrategy
from strategy.strategy_loader import load_strategy_config
from screener.stock_screener import StockScreener
from audit.trade_logger import TradeLogger
from trader.real_domestic import run_real_domestic_cycle
from trader.real_nasdaq import run_real_nasdaq_cycle
from notifications.telegram_notifier import (
    from_env as telegram_from_env,
    notify_signal as tg_notify_signal,
    notify_order_placed as tg_notify_order_placed,
    notify_buy as tg_notify_buy,
    notify_sell as tg_notify_sell,
    notify_scan_result as tg_notify_scan,
    notify_take_profit_sell as tg_notify_take_profit_sell,
)

import os
import json
from pathlib import Path
from logging.handlers import RotatingFileHandler

_LOG_DIR = "logs"
os.makedirs(_LOG_DIR, exist_ok=True)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_mode = os.getenv("TRADING_MODE", "mock")

_file_handler = RotatingFileHandler(
    f"{_LOG_DIR}/trading_{_mode}.log",
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler], force=True)
logger = logging.getLogger(__name__)

KST = datetime.timezone(datetime.timedelta(hours=9))


def is_market_open() -> bool:
    """한국 장 (KST 09:00~15:30, 평일)"""
    now = datetime.datetime.now(KST)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_time <= now <= close_time


def is_nasdaq_open() -> bool:
    """나스닥 장 (KST 23:30~06:00, 미국 평일 기준)"""
    now = datetime.datetime.now(KST)
    hour = now.hour
    minute = now.minute
    # KST 23:30 이후 또는 06:00 이전
    after_open  = (hour == 23 and minute >= 30) or (hour < 6)
    before_close = hour < 6
    in_session = (hour == 23 and minute >= 30) or (0 <= hour < 6)
    # 미국 기준 평일: KST 23:30은 전날 기준이므로 월~금 커버
    if not in_session:
        return False
    # 미국 평일 체크 (KST 자정 전후 처리)
    us_day = now.weekday() if hour >= 23 else (now.weekday() - 1) % 7
    return us_day < 5  # 월~금


def _tg(ctx):
    return ctx.get("telegram_bot")


def _notify_take_profit_sell(ctx, code, quantity, profit_rate):
    if ctx.get("telegram_bot"):
        tg_notify_take_profit_sell(ctx["telegram_bot"], code, quantity, profit_rate)


def _notify_scan(ctx, results):
    if _tg(ctx):
        tg_notify_scan(_tg(ctx), results)


def run_take_profit_cycle(ctx: dict) -> None:
    """1분마다 실행: 수익률 기준 이상 보유 종목 익절 매도"""
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
                ctx["trade_logger"].log("SELL", code, qty, result, signal_type="익절", profit_rate=profit_rate)
                _notify_take_profit_sell(ctx, code, qty, profit_rate)
                logger.info(f"익절 매도: {code} | 수익률 {profit_rate}%")

    except Exception as e:
        logger.error(f"익절 사이클 오류: {e}", exc_info=True)


def _notify_sell(ctx, code, qty, price, signal_type: str = "", market: str = "KR"):
    if _tg(ctx):
        tg_notify_sell(_tg(ctx), code, qty, price, signal_type=signal_type, market=market)


def _run_domestic_cycle(ctx: dict, token: str, skip_buy: bool = False) -> int:
    """국내 매매 사이클. 매수한 종목 수 반환"""
    config = ctx["config"]
    holdings = ctx["order_client"].get_holdings(token)

    for stock_code, info in list(holdings.items()):
        qty       = info["qty"]
        avg_price = float(info.get("avg_price") or 0)
        prices = ctx["price_client"].fetch_closing_prices(
            stock_code, ctx["strategy"].required_data_points, token
        )
        current_price = float(prices[-1])
        name  = get_stock_name(stock_code)
        label = f"{stock_code}({name})" if name else stock_code

        # 손절: 매입가 대비 stop_loss_pct% 이상 하락 시 매도 (+20% 초과 또는 -20% 초과 손실 종목 제외)
        stop_loss_pct = config.stop_loss_pct
        if stop_loss_pct > 0 and avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price * 100
            if profit_pct > 20:
                logger.debug(f"손절 스킵 (수익률 {profit_pct:.1f}% > 20%): {label}")
            elif profit_pct <= -20:
                logger.info(f"손절 스킵 (손실 {profit_pct:.1f}% > 20%): {label}")
            elif profit_pct <= -stop_loss_pct:
                result = ctx["order_client"].sell(stock_code, qty, token)
                ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="손절")
                del holdings[stock_code]
                _notify_sell(ctx, stock_code, qty, prices[-1], signal_type="손절")
                logger.info(f"국내 손절 매도: {label} | 매입가: {avg_price:,.0f} | 현재가: {current_price:,.0f} | 수익률: {profit_pct:.2f}%")
                continue

        if ctx["strategy"].should_sell(prices):
            result = ctx["order_client"].sell(stock_code, qty, token)
            ctx["trade_logger"].log("SELL", stock_code, qty, result)
            del holdings[stock_code]
            _notify_sell(ctx, stock_code, qty, prices[-1])
            logger.info(f"국내 매도 완료: {label}")

    bought = 0
    capacity = config.max_positions - len(holdings)
    per_position = config.mock_budget // config.max_positions
    if skip_buy:
        return 0
    if capacity > 0:
        candidates = ctx["screener"].scan(token, all_stocks=config.scan_all_stocks)
        if candidates:
            _notify_scan(ctx, candidates)
        for candidate in candidates:
            if bought >= capacity:
                break
            code          = candidate["code"]
            signal_type   = candidate.get("signal_type", "골든크로스")
            signal_time   = candidate.get("signal_detected_at", datetime.datetime.now().isoformat())
            price         = int(candidate["price"])
            if code in holdings:
                continue

            # 1단계: 신호 감지 알림
            if _tg(ctx):
                tg_notify_signal(_tg(ctx), code, price, signal_type)

            # 2단계: mock_budget 기반 수량 계산
            quantity = per_position // price if price > 0 else 0
            if quantity < 1:
                name = get_stock_name(code)
                label = f"{code}({name})" if name else code
                logger.debug(f"예산 초과 스킵: {label} | {price:,.0f}원 > 포지션예산 {per_position:,}원")
                continue

            # 3단계: 매수 주문 (시장가 or 지정가)
            limit_price = None
            if config.order_type == "limit":
                limit_price = round(price * (1 + config.limit_order_pct / 100))
                logger.info(f"지정가 주문: {code} | 신호가 {price:,}원 × (1+{config.limit_order_pct}%) = {limit_price:,}원")
            result    = ctx["order_client"].buy(code, quantity, token, limit_price=limit_price)
            order_no  = result.get("output", {}).get("ODNO", "")
            if _tg(ctx):
                tg_notify_order_placed(_tg(ctx), code, quantity, limit_price or price, order_no)

            # 4단계: 체결 확인 후 알림 + 저장
            exec_info = ctx["order_client"].get_execution(code, order_no, token)
            exec_price = exec_info["exec_price"] if exec_info else str(limit_price or price)
            exec_time  = exec_info["exec_time"]  if exec_info else ""
            if _tg(ctx):
                tg_notify_buy(_tg(ctx), code, quantity, limit_price or price,
                              signal_type=signal_type, signal_time=signal_time,
                              exec_price=exec_price)
            ctx["trade_logger"].log(
                "BUY", code, quantity, result,
                signal_type=signal_type,
                signal_detected_at=signal_time,
                exec_price=exec_price,
                exec_confirmed_at=exec_time,
            )
            holdings[code] = {"qty": quantity, "avg_price": exec_price}
            bought += 1
        if not candidates:
            logger.info(f"국내 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought


def _run_nasdaq_cycle(ctx: dict, token: str) -> int:
    """나스닥100 매매 사이클. 매수한 종목 수 반환"""
    config = ctx["config"]
    holdings = ctx["order_client"].get_overseas_holdings(token)

    for symbol, info in list(holdings.items()):
        avg_price = float(info.get("avg_price") or 0)
        prices = ctx["price_client"].fetch_overseas_closing_prices(
            symbol, info["exchange"], ctx["strategy"].required_data_points, token
        )
        current_price = float(prices[-1])
        name  = get_stock_name(symbol)
        label = f"{symbol}({name})" if name else symbol

        # 손절: 매입가 대비 stop_loss_pct% 이상 하락 시 매도 (+20% 초과 또는 -20% 초과 손실 종목 제외)
        stop_loss_pct = config.stop_loss_pct
        if stop_loss_pct > 0 and avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price * 100
            if profit_pct > 20:
                logger.debug(f"손절 스킵 (수익률 {profit_pct:.1f}% > 20%): {label}")
            elif profit_pct <= -20:
                logger.info(f"손절 스킵 (손실 {profit_pct:.1f}% > 20%): {label}")
            elif profit_pct <= -stop_loss_pct:
                result = ctx["order_client"].sell_overseas(symbol, info["exchange"], info["qty"], token)
                ctx["trade_logger"].log("SELL", symbol, info["qty"], result, signal_type="손절")
                del holdings[symbol]
                _notify_sell(ctx, symbol, info["qty"], prices[-1], signal_type="손절", market="US")
                logger.info(f"해외 손절 매도: {label} | 매입가: ${avg_price:.2f} | 현재가: ${current_price:.2f} | 수익률: {profit_pct:.2f}%")
                continue

        if ctx["strategy"].should_sell(prices):
            result = ctx["order_client"].sell_overseas(symbol, info["exchange"], info["qty"], token)
            ctx["trade_logger"].log("SELL", symbol, info["qty"], result)
            del holdings[symbol]
            _notify_sell(ctx, symbol, info["qty"], prices[-1])
            logger.info(f"해외 매도 완료: {label}")

    bought = 0
    capacity = config.max_positions - len(holdings)
    per_position_usd = config.real_usd_budget / config.max_positions
    if capacity > 0:
        candidates = ctx["screener"].scan_us(token, mode=config.us_scan_mode)
        if candidates:
            _notify_scan(ctx, candidates)
        for candidate in candidates:
            if bought >= capacity:
                break
            symbol      = candidate["code"]
            exchange    = candidate["exchange"]
            signal_type = candidate.get("signal_type", "골든크로스")
            signal_time = candidate.get("signal_detected_at", datetime.datetime.now().isoformat())
            price       = float(candidate["price"])
            if symbol in holdings:
                continue

            # 1단계: 신호 감지 알림
            if _tg(ctx):
                tg_notify_signal(_tg(ctx), symbol, price, signal_type, market="US")

            # 2단계: USD 예산 기반 수량 계산
            quantity = int(per_position_usd // price) if price > 0 else 0
            if quantity < 1:
                name = get_stock_name(symbol)
                label = f"{symbol}({name})" if name else symbol
                logger.debug(f"예산 초과 스킵: {label} | ${price:.2f} > 포지션예산 ${per_position_usd:.2f}")
                continue

            # 3단계: 매수 주문 (시장가 or 지정가)
            if config.order_type == "limit":
                order_price = round(price * (1 + config.limit_order_pct / 100), 2)
                logger.info(f"지정가 주문: {symbol} | 신호가 ${price:.2f} × (1+{config.limit_order_pct}%) = ${order_price:.2f}")
            else:
                order_price = price
            result   = ctx["order_client"].buy_overseas(symbol, exchange, quantity, token, limit_price=order_price)
            order_no = result.get("output", {}).get("ODNO", "")
            if _tg(ctx):
                tg_notify_order_placed(_tg(ctx), symbol, quantity, order_price, order_no, market="US")

            # 4단계: 체결 알림 + 저장 (해외는 체결조회 미지원, 주문가로 대체)
            if _tg(ctx):
                tg_notify_buy(_tg(ctx), symbol, quantity, order_price,
                              signal_type=signal_type, signal_time=signal_time,
                              exec_price=str(order_price), market="US")
            ctx["trade_logger"].log(
                "BUY", symbol, quantity, result,
                signal_type=signal_type,
                signal_detected_at=signal_time,
                exec_price=str(order_price),
            )
            holdings[symbol] = {"qty": quantity, "exchange": exchange, "avg_price": str(order_price)}
            bought += 1
        if not candidates:
            logger.info(f"나스닥 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought


def _save_holdings_snapshot(mode: str, items: list) -> None:
    path = Path(_LOG_DIR) / f"holdings_{mode}.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def run_stop_loss_check(ctx: dict) -> None:
    """장중 손절 체크 — 보유 국내주식 실시간 현재가 기준으로 매분 확인"""
    if not is_market_open():
        return
    config = ctx["config"]
    if config.stop_loss_pct <= 0:
        return
    try:
        token    = ctx["token_manager"].get_valid_token()
        holdings = ctx["order_client"].get_holdings(token)
        snapshot = []
        for stock_code, info in list(holdings.items()):
            avg_price = float(info.get("avg_price") or 0)
            item = {
                "code": stock_code,
                "name": get_stock_name(stock_code) or "",
                "qty": info["qty"],
                "avg_price": avg_price,
                "current_price": None,
                "profit_pct": None,
            }
            if avg_price <= 0:
                snapshot.append(item)
                continue
            try:
                current_price = float(ctx["price_client"].fetch_current_price(stock_code, token))
                item["current_price"] = current_price
                item["profit_pct"] = round((current_price - avg_price) / avg_price * 100, 2)
            except Exception as e:
                logger.debug(f"현재가 조회 실패 [{stock_code}]: {e}")
                snapshot.append(item)
                continue
            snapshot.append(item)
            profit_pct = item["profit_pct"]
            if profit_pct > 20:
                logger.debug(f"손절 스킵 (수익률 {profit_pct:.1f}% > 20%): {stock_code}")
                continue
            if profit_pct <= -20:
                logger.debug(f"손절 스킵 (손실 {profit_pct:.1f}% > 20%): {stock_code}")
                continue
            if profit_pct <= -config.stop_loss_pct:
                qty   = info["qty"]
                name  = get_stock_name(stock_code)
                label = f"{stock_code}({name})" if name else stock_code
                result = ctx["order_client"].sell(stock_code, qty, token)
                ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="손절")
                _notify_sell(ctx, stock_code, qty, current_price, signal_type="손절")
                logger.info(
                    f"손절 매도: {label} | 매입가: {avg_price:,.0f} | "
                    f"현재가: {current_price:,.0f} | 수익률: {profit_pct:.2f}%"
                )
        _save_holdings_snapshot(config.mode, snapshot)

    except Exception as e:
        logger.error(f"손절 체크 오류: {e}", exc_info=True)


def run_domestic_cycle(ctx: dict) -> None:
    """국내 장 스캔 — 매일 09:05 실행"""
    if not is_market_open():
        logger.info("국내 장 시간 외 — 건너뜀")
        return
    today = datetime.date.today()
    skip_buy = (ctx.get("domestic_buy_date") == today)
    if skip_buy:
        logger.info("오늘 이미 매수 완료 — 매수 단계 건너뜀 (매도 체크만 실행)")
    try:
        token = ctx["token_manager"].get_valid_token()
        if ctx["config"].mode == "real":
            bought = run_real_domestic_cycle(ctx, token, skip_buy=skip_buy)
        else:
            bought = _run_domestic_cycle(ctx, token, skip_buy=skip_buy)
        if bought > 0:
            ctx["domestic_buy_date"] = today
    except Exception as e:
        logger.error(f"국내 사이클 오류: {e}", exc_info=True)


def run_nasdaq_cycle(ctx: dict) -> None:
    """나스닥 장 스캔 — 매일 23:35 실행"""
    if not is_nasdaq_open():
        logger.info("나스닥 장 시간 외 — 건너뜀")
        return
    try:
        token = ctx["token_manager"].get_valid_token()
        if ctx["config"].mode == "real":
            run_real_nasdaq_cycle(ctx, token)
        else:
            _run_nasdaq_cycle(ctx, token)
    except Exception as e:
        logger.error(f"나스닥 사이클 오류: {e}", exc_info=True)


def main() -> None:
    config = load_config()

    # 실행 중인 모드를 대시보드가 읽을 수 있도록 기록
    from pathlib import Path
    Path(".bot.mode").write_text(config.mode)

    scan_mode = "전종목" if config.scan_all_stocks else "거래량 상위"
    budget = config.real_budget if config.mode == "real" else config.mock_budget
    per_position = budget // config.max_positions
    logger.info(
        f"모드: {config.mode.upper()} | 국내 스캔: {scan_mode} | "
        f"나스닥100: {'활성화' if config.scan_nasdaq else '비활성화'} | "
        f"최대보유: {config.max_positions}개 | "
        f"예산: {budget:,}원 (포지션당 {per_position:,}원) | "
        f"스케줄: 국내 09:05 / 나스닥 23:35"
    )

    mode_strategy_path = f"STRATEGY_{config.mode.upper()}.md"
    strategy_path = mode_strategy_path if os.path.exists(mode_strategy_path) else "STRATEGY.md"
    if os.path.exists(strategy_path):
        strategy = ConfigurableStrategy(load_strategy_config(strategy_path))
        logger.info(f"전략: {strategy_path} 로드")
    else:
        strategy = MaCrossStrategy(config.ma_short_period, config.ma_long_period)
        logger.info(f"전략: MA 골든크로스 ({config.ma_short_period}/{config.ma_long_period})")
    price_client = PriceClient(config)
    telegram_bot = telegram_from_env() if config.mode == "real" else None

    logger.info(f"텔레그램 알림: {'활성화' if telegram_bot else '비활성화 (mock)'}")

    ctx = {
        "config":              config,
        "token_manager":       TokenManager(config),
        "price_client":        price_client,
        "order_client":        OrderClient(config),
        "strategy":            strategy,
        "screener":            StockScreener(config, price_client, strategy),
        "trade_logger":        TradeLogger(config.mode),
        "telegram_bot":        telegram_bot,
        "domestic_buy_date":   None,  # 당일 매수 완료 날짜 (중복 매수 방지)
    }

    interval = config.scan_interval_minutes
    if interval > 0:
        schedule.every(interval).minutes.do(run_domestic_cycle, ctx)
        if config.scan_nasdaq:
            schedule.every(interval).minutes.do(run_nasdaq_cycle, ctx)
        logger.info(f"스캔 주기: {interval}분 간격")
    else:
        schedule.every().day.at("09:05").do(run_domestic_cycle, ctx)
        if config.scan_nasdaq:
            schedule.every().day.at("23:35").do(run_nasdaq_cycle, ctx)
        logger.info("스캔 주기: 국내 09:05 / 나스닥 23:35 고정")
    if config.stop_loss_pct > 0:
        schedule.every(1).minutes.do(run_stop_loss_check, ctx)
        logger.info(f"손절 모니터링 활성화: -{config.stop_loss_pct}% | 1분 주기 체크")
    if config.take_profit_rate > 0:
        schedule.every(1).minutes.do(run_take_profit_cycle, ctx)
        logger.info(f"익절 모니터링 활성화: +{config.take_profit_rate}% | 1분 주기 체크")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
