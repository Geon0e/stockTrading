import logging
import datetime
import signal
import threading
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
from trader.matagi import check_matagi_conditions
from trader.real_nasdaq import run_real_nasdaq_cycle
from trader.utils import traded_today as _traded_today, get_daily_budget, deduct_daily_budget, add_daily_budget, init_daily_from_api
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

from contextlib import contextmanager

@contextmanager
def _null_ctx():
    yield



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
    """설정 주기마다 실행: 수익률 기준 이상 보유 종목 익절 지정가 매도"""
    if not is_market_open():
        return

    config = ctx["config"]
    lock = ctx.get("order_lock")
    try:
        token = ctx["token_manager"].get_valid_token()
        holdings_detail = ctx["order_client"].get_holdings_detail(token)

        for code, detail in list(holdings_detail.items()):
            profit_rate = detail["profit_rate"]
            if profit_rate >= config.take_profit_rate:
                qty = detail["qty"]
                avg_price = detail["avg_price"]
                limit_pct = config.take_profit_limit_pct or config.take_profit_rate
                limit_price = OrderClient._round_to_tick(int(avg_price * (1 + limit_pct / 100)))
                with lock if lock else _null_ctx():
                    result = ctx["order_client"].sell(code, qty, token, limit_price=limit_price)
                order_no = result.get("output", {}).get("ODNO", "")
                exec_info = ctx["order_client"].get_execution(code, order_no, token, side="sell")
                exec_price_str = exec_info["exec_price"] if exec_info else str(limit_price)
                exec_time = exec_info["exec_time"] if exec_info else ""
                exec_price_f = float(exec_price_str)
                actual_profit_pct = round((exec_price_f - avg_price) / avg_price * 100, 2)
                ctx["trade_logger"].log("SELL", code, qty, result, signal_type="익절",
                                        exec_price=exec_price_str, exec_confirmed_at=exec_time,
                                        profit_rate=actual_profit_pct)
                _traded_today(ctx).add(code)
                proceeds = int(exec_price_f * qty)
                add_daily_budget(ctx, proceeds, is_take_profit=True,
                                 profit_amount=int((exec_price_f - avg_price) * qty))
                _notify_take_profit_sell(ctx, code, qty, actual_profit_pct)
                logger.info(f"익절 매도: {code} | 매입가 {avg_price:,.0f}원 | 체결가 {exec_price_f:,.0f}원 | 수익률 {actual_profit_pct:+.2f}% | 당일 잔여예산: {get_daily_budget(ctx):,}원")

    except Exception as e:
        logger.error(f"익절 사이클 오류: {e}", exc_info=True)


def _notify_sell(ctx, code, qty, price, signal_type: str = "", market: str = "KR"):
    if _tg(ctx):
        tg_notify_sell(_tg(ctx), code, qty, price, signal_type=signal_type, market=market)


def _run_domestic_cycle(ctx: dict, token: str, skip_buy: bool = False) -> int:
    """국내 매매 사이클. 매수한 종목 수 반환"""
    config = ctx["config"]
    holdings = ctx["order_client"].get_holdings(token)
    _exclude = set(config.exclude_list)

    for stock_code, info in list(holdings.items()):
        if stock_code in _traded_today(ctx):
            continue
        if stock_code in _exclude:
            continue
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
                add_daily_budget(ctx, int(current_price * qty),
                                 profit_amount=int((current_price - avg_price) * qty))
                _notify_sell(ctx, stock_code, qty, prices[-1], signal_type="손절")
                logger.info(f"국내 손절 매도: {label} | 매입가: {avg_price:,.0f} | 현재가: {current_price:,.0f} | 수익률: {profit_pct:.2f}%")
                continue

        if ctx["strategy"].should_sell(prices):
            result = ctx["order_client"].sell(stock_code, qty, token)
            ctx["trade_logger"].log("SELL", stock_code, qty, result)
            del holdings[stock_code]
            add_daily_budget(ctx, int(current_price * qty),
                             profit_amount=int((current_price - avg_price) * qty))
            _notify_sell(ctx, stock_code, qty, prices[-1])
            logger.info(f"국내 매도 완료: {label}")

    bought = 0
    capacity = config.max_positions - len(holdings)
    if skip_buy:
        return 0
    remaining = get_daily_budget(ctx)
    if remaining <= 0:
        logger.info("당일 예산 소진 — 매수 건너뜀")
        return 0
    per_position = min(config.mock_budget // config.max_positions, remaining)
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
            if code in _traded_today(ctx):
                continue

            if code in holdings:
                avg_p = float(holdings[code].get("avg_price") or 0)
                if avg_p <= 0 or price >= avg_p:
                    logger.debug(f"보유 중 수익 종목 추가매수 스킵: {code} | 매입가: {avg_p:,.0f}원 | 현재가: {price:,}원")
                    continue
                # 물타기 추가 조건 확인
                ok, reason = check_matagi_conditions(
                    ctx["price_client"], code, token, avg_p, price,
                    drop_pct=config.matagi_drop_pct,
                )
                _name = get_stock_name(code)
                _label = f"{code}({_name})" if _name else code
                if not ok:
                    logger.info(f"물타기 스킵 [{_label}]: {reason}")
                    continue
                signal_type = "물타기"
                logger.info(f"물타기 조건 통과 [{_label}]: {reason}")

            # 1단계: 신호 감지 알림
            if _tg(ctx):
                tg_notify_signal(_tg(ctx), code, price, signal_type)

            # 2단계: mock_budget 기반 수량 계산
            quantity = per_position // price if price > 0 else 0
            if config.order_quantity > 0:
                quantity = min(quantity, config.order_quantity)
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
            cost = int(float(exec_price) * quantity)
            deduct_daily_budget(ctx, cost)
            logger.info(f"당일 잔여예산: {get_daily_budget(ctx):,}원")
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
                add_daily_budget(ctx, int(current_price * info["qty"]),
                                 profit_amount=int((current_price - avg_price) * info["qty"]))
                _notify_sell(ctx, symbol, info["qty"], prices[-1], signal_type="손절", market="US")
                logger.info(f"해외 손절 매도: {label} | 매입가: ${avg_price:.2f} | 현재가: ${current_price:.2f} | 수익률: {profit_pct:.2f}%")
                continue

        if ctx["strategy"].should_sell(prices):
            result = ctx["order_client"].sell_overseas(symbol, info["exchange"], info["qty"], token)
            ctx["trade_logger"].log("SELL", symbol, info["qty"], result)
            del holdings[symbol]
            add_daily_budget(ctx, int(current_price * info["qty"]),
                             profit_amount=int((current_price - avg_price) * info["qty"]))
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


def _get_today_buys(mode: str) -> set:
    """오늘 매수된 종목 코드 셋 반환 (트레이드 로그 기반)."""
    today = str(datetime.date.today())
    bought: set = set()
    path = Path(_LOG_DIR) / f"trades_{mode}.jsonl"
    if not path.exists():
        return bought
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("action") == "BUY" and str(r.get("timestamp", "")).startswith(today):
                code = r.get("stock_code")
                if code:
                    bought.add(code)
        except Exception:
            pass
    return bought


def run_morning_sell_cycle(ctx: dict) -> None:
    """장 시작 시 전일 보유 종목 중 수익률 기준 초과 종목 익절 매도.

    MORNING_SELL_PROFIT_PCT_{MODE} 환경변수로 기준 수익률 설정 (0 = 비활성화).
    오늘 매수한 종목은 제외하고, 전날부터 보유 중인 종목만 대상으로 한다.
    """
    if not is_market_open():
        return
    config = ctx["config"]
    threshold = config.morning_sell_profit_pct
    if threshold <= 0:
        return
    today = datetime.date.today()
    if ctx.get("morning_sell_date") == today:
        return  # 당일 이미 실행됨
    ctx["morning_sell_date"] = today

    try:
        token = ctx["token_manager"].get_valid_token()
        today_buys = _get_today_buys(config.mode)
        holdings_detail = ctx["order_client"].get_holdings_detail(token)
        _exclude = set(config.exclude_list)
        lock = ctx.get("order_lock")
        sold = 0

        for code, detail in list(holdings_detail.items()):
            if code in _traded_today(ctx):
                continue
            if code in _exclude:
                continue
            if code in today_buys:
                logger.debug(f"[장초 익절] 오늘 매수 종목 제외: {code}")
                continue
            profit_rate = float(detail["profit_rate"])
            if profit_rate < threshold:
                continue

            qty       = detail["qty"]
            avg_price = detail["avg_price"]
            name      = get_stock_name(code)
            label     = f"{code}({name})" if name else code
            sell_price = round(avg_price * (1 + profit_rate / 100))
            profit_amount = int(avg_price * qty * profit_rate / 100)

            try:
                if lock:
                    lock.acquire()
                try:
                    result = ctx["order_client"].sell(code, qty, token)
                finally:
                    if lock:
                        lock.release()

                ctx["trade_logger"].log(
                    "SELL", code, qty, result,
                    signal_type="장초익절",
                    profit_rate=round(profit_rate, 2),
                    profit_amount=profit_amount,
                )
                _traded_today(ctx).add(code)
                add_daily_budget(ctx, int(sell_price * qty),
                                 is_take_profit=True,
                                 profit_amount=profit_amount)
                if _tg(ctx):
                    tg_notify_sell(_tg(ctx), code, qty, sell_price,
                                   signal_type=f"장초익절 +{profit_rate:.1f}%")
                logger.info(
                    f"[장초 익절] {label} | 수익률: {profit_rate:+.2f}% ≥ +{threshold}% | "
                    f"{qty}주 | 수익: {profit_amount:+,}원"
                )
                sold += 1
            except Exception as e:
                logger.warning(f"[장초 익절] 매도 실패 [{label}]: {e}")
                _traded_today(ctx).add(code)

        if sold:
            logger.info(f"[장초 익절] 총 {sold}종목 매도 완료")
        else:
            logger.info(f"[장초 익절] 기준 수익률 +{threshold}% 초과 종목 없음")

    except Exception as e:
        logger.error(f"[장초 익절] 사이클 오류: {e}", exc_info=True)


def run_morning_stoploss_cycle(ctx: dict) -> None:
    """10:30에 전일 보유 종목 중 여전히 손실 중인 종목 손절 매도.

    MORNING_STOPLOSS_ENABLED_{MODE}=true 시 활성화.
    오늘 매수한 종목은 제외하고 전날부터 보유 중인 종목만 대상으로 한다.
    """
    if not is_market_open():
        return
    config = ctx["config"]
    if not config.morning_stoploss_enabled:
        return
    today = datetime.date.today()
    if ctx.get("morning_stoploss_date") == today:
        return  # 당일 이미 실행됨
    ctx["morning_stoploss_date"] = today

    try:
        token = ctx["token_manager"].get_valid_token()
        today_buys = _get_today_buys(config.mode)
        holdings_detail = ctx["order_client"].get_holdings_detail(token)
        _exclude = set(config.exclude_list)
        lock = ctx.get("order_lock")
        sold = 0

        for code, detail in list(holdings_detail.items()):
            if code in _traded_today(ctx):
                continue
            if code in _exclude:
                continue
            if code in today_buys:
                logger.debug(f"[10:30 손절] 오늘 매수 종목 제외: {code}")
                continue
            profit_rate = float(detail["profit_rate"])
            if profit_rate >= 0:
                continue  # 본전 이상이면 손절 제외

            qty       = detail["qty"]
            avg_price = detail["avg_price"]
            name      = get_stock_name(code)
            label     = f"{code}({name})" if name else code
            sell_price    = round(avg_price * (1 + profit_rate / 100))
            profit_amount = int(avg_price * qty * profit_rate / 100)

            try:
                if lock:
                    lock.acquire()
                try:
                    result = ctx["order_client"].sell(code, qty, token)
                finally:
                    if lock:
                        lock.release()

                ctx["trade_logger"].log(
                    "SELL", code, qty, result,
                    signal_type="10시반손절",
                    profit_rate=round(profit_rate, 2),
                    profit_amount=profit_amount,
                )
                _traded_today(ctx).add(code)
                add_daily_budget(ctx, int(sell_price * qty), profit_amount=profit_amount)
                if _tg(ctx):
                    tg_notify_sell(_tg(ctx), code, qty, sell_price,
                                   signal_type=f"10:30손절 {profit_rate:.1f}%")
                logger.info(
                    f"[10:30 손절] {label} | 수익률: {profit_rate:+.2f}% | "
                    f"{qty}주 | 손실: {profit_amount:+,}원"
                )
                sold += 1
            except Exception as e:
                logger.warning(f"[10:30 손절] 매도 실패 [{label}]: {e}")
                _traded_today(ctx).add(code)

        if sold:
            logger.info(f"[10:30 손절] 총 {sold}종목 손절 완료")
        else:
            logger.info("[10:30 손절] 손실 중인 전일 보유 종목 없음")

    except Exception as e:
        logger.error(f"[10:30 손절] 사이클 오류: {e}", exc_info=True)


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
        _exclude = set(config.exclude_list)
        for stock_code, info in list(holdings.items()):
            if stock_code in _traded_today(ctx):
                continue
            if stock_code in _exclude:
                continue
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
                limit_pct = config.stop_loss_limit_pct or config.stop_loss_pct
                limit_price = OrderClient._round_to_tick(int(avg_price * (1 - limit_pct / 100)))
                lock = ctx.get("order_lock")
                try:
                    with lock if lock else _null_ctx():
                        result = ctx["order_client"].sell(stock_code, qty, token, limit_price=limit_price)
                except Exception as sell_err:
                    logger.warning(f"손절 매도 실패 [{stock_code}]: {sell_err} — 당일 재시도 중단")
                    _traded_today(ctx).add(stock_code)
                    continue
                order_no = result.get("output", {}).get("ODNO", "")
                exec_info = ctx["order_client"].get_execution(stock_code, order_no, token, side="sell")
                exec_price_str = exec_info["exec_price"] if exec_info else str(limit_price)
                exec_time = exec_info["exec_time"] if exec_info else ""
                exec_price_f = float(exec_price_str)
                actual_profit_pct = round((exec_price_f - avg_price) / avg_price * 100, 2)
                ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="손절",
                                        exec_price=exec_price_str, exec_confirmed_at=exec_time,
                                        profit_rate=actual_profit_pct)
                _traded_today(ctx).add(stock_code)
                add_daily_budget(ctx, int(exec_price_f * qty),
                                 profit_amount=int((exec_price_f - avg_price) * qty))
                _notify_sell(ctx, stock_code, qty, current_price, signal_type="손절")
                logger.info(
                    f"손절 매도: {label} | 매입가: {avg_price:,.0f}원 | "
                    f"지정가: {limit_price:,}원 | 수익률: {actual_profit_pct:+.2f}%"
                )
        _save_holdings_snapshot(config.mode, snapshot)

    except Exception as e:
        logger.error(f"손절 체크 오류: {e}", exc_info=True)


def run_domestic_cycle(ctx: dict) -> None:
    """국내 장 스캔 — 매일 09:05 실행"""
    if not is_market_open():
        logger.info("국내 장 시간 외 — 건너뜀")
        return

    # 장 시작 시 전일 보유 종목 익절 체크 (당일 1회)
    run_morning_sell_cycle(ctx)

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


def _next_aligned_run(interval_minutes: int, anchor: datetime.time) -> datetime.datetime:
    """interval_minutes 주기를 anchor 시각 기준으로 정렬한 다음 실행 시각 반환.
    anchor 이전이면 anchor를, 이후면 anchor + N*interval 중 now 직후 시각을 반환."""
    now = datetime.datetime.now()
    anchor_dt = datetime.datetime.combine(now.date(), anchor)
    if now <= anchor_dt:
        return anchor_dt
    elapsed_seconds = (now - anchor_dt).total_seconds()
    interval_seconds = interval_minutes * 60
    periods = int(elapsed_seconds // interval_seconds) + 1
    return anchor_dt + datetime.timedelta(seconds=periods * interval_seconds)


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

    order_lock  = threading.Lock()  # 동시 주문 충돌 방지
    budget_lock = threading.Lock()  # 당일 예산 동시 접근 방지
    stop_event  = threading.Event()

    ctx = {
        "config":              config,
        "token_manager":       TokenManager(config),
        "price_client":        price_client,
        "order_client":        OrderClient(config),
        "strategy":            strategy,
        "screener":            StockScreener(config, price_client, strategy),
        "trade_logger":        TradeLogger(config.mode),
        "telegram_bot":        telegram_bot,
        "domestic_buy_date":   None,
        "order_lock":          order_lock,
        "budget_lock":         budget_lock,
    }

    # real 모드: 봇 시작 시 KIS API 체결 내역으로 당일 예산 현황 초기화
    if config.mode == "real":
        try:
            _init_token = ctx["token_manager"].get_valid_token()
            _executions = ctx["order_client"].get_today_ccld(_init_token)
            init_daily_from_api(ctx, _executions)
        except Exception as e:
            logger.warning(f"[금일현황] KIS API 초기화 실패 — 로컬 로그로 폴백: {e}")

    interval = config.scan_interval_minutes
    if interval > 0:
        domestic_anchor = datetime.time(9, 0)
        job_dom = schedule.every(interval).minutes.do(run_domestic_cycle, ctx)
        job_dom.next_run = _next_aligned_run(interval, domestic_anchor)
        if config.scan_nasdaq:
            nasdaq_anchor = datetime.time(23, 35)
            job_nas = schedule.every(interval).minutes.do(run_nasdaq_cycle, ctx)
            job_nas.next_run = _next_aligned_run(interval, nasdaq_anchor)
        logger.info(
            f"스캔 주기: {interval}분 간격 | 국내 첫 실행: {job_dom.next_run.strftime('%H:%M')}"
        )
    else:
        schedule.every().day.at("09:05").do(run_domestic_cycle, ctx)
        if config.scan_nasdaq:
            schedule.every().day.at("23:35").do(run_nasdaq_cycle, ctx)
        logger.info("스캔 주기: 국내 09:05 / 나스닥 23:35 고정")

    # 10:30 손절은 스캔 주기와 무관하게 항상 고정 시간 등록
    if config.morning_stoploss_enabled:
        schedule.every().day.at("10:30").do(run_morning_stoploss_cycle, ctx)
        logger.info("10:30 전일 손실 종목 손절 활성화")

    monitor_interval = config.monitor_interval_seconds

    def _start_monitor(name: str, fn, interval: int) -> threading.Thread:
        def _loop():
            logger.info(f"[{name}] 모니터링 스레드 시작 | {interval}초 주기")
            while not stop_event.is_set():
                try:
                    fn(ctx)
                except Exception as e:
                    logger.error(f"[{name}] 모니터링 오류: {e}", exc_info=True)
                stop_event.wait(interval)
            logger.info(f"[{name}] 모니터링 스레드 종료")
        t = threading.Thread(target=_loop, name=name, daemon=True)
        t.start()
        return t

    if config.stop_loss_pct > 0:
        _start_monitor("손절", run_stop_loss_check, monitor_interval)
        logger.info(f"손절 모니터링 활성화: -{config.stop_loss_pct}% | {monitor_interval}초 전용 스레드")
    if config.take_profit_rate > 0:
        _start_monitor("익절", run_take_profit_cycle, monitor_interval)
        logger.info(f"익절 모니터링 활성화: +{config.take_profit_rate}% | {monitor_interval}초 전용 스레드")

    def _on_signal(signum, frame):
        logger.info("종료 신호 수신 — 모니터링 스레드 정리 중...")
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    while not stop_event.is_set():
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
