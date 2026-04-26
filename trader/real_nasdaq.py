import logging
import datetime

from screener.name_lookup import get_stock_name
from notifications.telegram_notifier import (
    notify_signal as tg_notify_signal,
    notify_order_placed as tg_notify_order_placed,
    notify_buy as tg_notify_buy,
    notify_sell as tg_notify_sell,
    notify_scan_result as tg_notify_scan,
    ask_confirm as tg_ask_confirm,
)

logger = logging.getLogger(__name__)


def _tg(ctx):
    return ctx.get("telegram_bot")


def run_real_nasdaq_cycle(ctx: dict, token: str) -> int:
    """실전 나스닥 매매 사이클.

    mock과의 차이:
    - 포지션당 예산 = real_usd_budget / max_positions (USD)
    - 매수 수량 = 포지션당 예산 // 현재가 (자동 계산, 최소 1주)
    - 현재가 > 포지션당 예산인 종목은 스킵
    """
    config = ctx["config"]
    per_position_usd = config.real_usd_budget / config.max_positions
    logger.info(
        f"[실전] 나스닥 매매 시작 | 총예산: ${config.real_usd_budget:.2f} | "
        f"포지션({config.max_positions}개)당: ${per_position_usd:.2f}"
    )

    holdings = ctx["order_client"].get_overseas_holdings(token)

    # ── 매도 ────────────────────────────────────────────────────────────
    for symbol, info in list(holdings.items()):
        avg_price = float(info.get("avg_price") or 0)
        try:
            prices = ctx["price_client"].fetch_overseas_closing_prices(
                symbol, info["exchange"], ctx["strategy"].required_data_points, token
            )
        except Exception as e:
            logger.warning(f"[실전] 해외 가격 조회 실패 [{symbol}]: {e}")
            continue

        current_price = float(prices[-1])
        name = get_stock_name(symbol)
        label = f"{symbol}({name})" if name else symbol

        # 손절
        if config.stop_loss_pct > 0 and avg_price > 0:
            drop_pct = (current_price - avg_price) / avg_price * 100
            if drop_pct <= -config.stop_loss_pct:
                result = ctx["order_client"].sell_overseas(symbol, info["exchange"], info["qty"], token)
                ctx["trade_logger"].log("SELL", symbol, info["qty"], result, signal_type="손절")
                del holdings[symbol]
                if _tg(ctx):
                    tg_notify_sell(_tg(ctx), symbol, info["qty"], current_price, signal_type="손절", market="US")
                logger.info(
                    f"[실전] 해외 손절: {label} | 매입가: ${avg_price:.2f} | "
                    f"현재가: ${current_price:.2f} | 하락률: {drop_pct:.2f}%"
                )
                continue

        # 전략 매도 신호
        if ctx["strategy"].should_sell(prices):
            result = ctx["order_client"].sell_overseas(symbol, info["exchange"], info["qty"], token)
            ctx["trade_logger"].log("SELL", symbol, info["qty"], result)
            del holdings[symbol]
            if _tg(ctx):
                tg_notify_sell(_tg(ctx), symbol, info["qty"], prices[-1], market="US")
            logger.info(f"[실전] 해외 데드크로스 매도: {label}")

    # ── 매수 ────────────────────────────────────────────────────────────
    bought = 0
    capacity = config.max_positions - len(holdings)
    if capacity <= 0:
        logger.info(f"[실전] 최대 포지션 도달 ({config.max_positions}개) — 매수 건너뜀")
        return 0

    candidates = ctx["screener"].scan_us(token, mode=config.us_scan_mode)
    if candidates and _tg(ctx):
        tg_notify_scan(_tg(ctx), candidates)

    skipped_budget = 0
    for candidate in candidates:
        if bought >= capacity:
            break

        symbol = candidate["code"]
        exchange = candidate["exchange"]
        signal_type = candidate.get("signal_type", "골든크로스")
        signal_time = candidate.get("signal_detected_at", datetime.datetime.now().isoformat())
        price = float(candidate["price"])

        if symbol in holdings:
            continue

        # 예산 초과 종목 스킵
        if price > per_position_usd:
            skipped_budget += 1
            logger.debug(
                f"[실전] 해외 예산 초과 스킵: {symbol} | ${price:.2f} > 포지션예산: ${per_position_usd:.2f}"
            )
            continue

        # 수량 자동 계산
        quantity = int(per_position_usd // price)
        if quantity < 1:
            continue

        name = get_stock_name(symbol)
        label = f"{symbol}({name})" if name else symbol
        estimated_cost = price * quantity
        logger.info(
            f"[실전] 해외 매수 후보: {label} | ${price:.2f} × {quantity}주 = ${estimated_cost:.2f} "
            f"(포지션예산 ${per_position_usd:.2f} 중 {estimated_cost / per_position_usd * 100:.1f}% 사용)"
        )

        # 1단계: 신호 알림
        if _tg(ctx):
            tg_notify_signal(_tg(ctx), symbol, price, signal_type, market="US")

        # 2단계: 텔레그램 매수 확인
        if _tg(ctx):
            if not tg_ask_confirm(_tg(ctx), symbol, price, signal_type, market="US"):
                logger.info(f"[실전] 해외 매수 취소 (사용자 거절 또는 타임아웃): {label}")
                continue

        # 3단계: 매수 주문
        result = ctx["order_client"].buy_overseas(symbol, exchange, quantity, token)
        order_no = result.get("output", {}).get("ODNO", "")
        if _tg(ctx):
            tg_notify_order_placed(_tg(ctx), symbol, quantity, price, order_no, market="US")

        # 4단계: 체결 알림 + 저장 (해외는 체결조회 미지원 → 주문가로 대체)
        if _tg(ctx):
            tg_notify_buy(
                _tg(ctx), symbol, quantity, price,
                signal_type=signal_type, signal_time=signal_time,
                exec_price=str(price), market="US",
            )
        ctx["trade_logger"].log(
            "BUY", symbol, quantity, result,
            signal_type=signal_type,
            signal_detected_at=signal_time,
            exec_price=str(price),
        )
        holdings[symbol] = {"qty": quantity, "exchange": exchange, "avg_price": str(price)}
        bought += 1
        logger.info(f"[실전] 해외 매수 완료: {label} | {quantity}주 @ ${price:.2f}")

    if skipped_budget:
        logger.info(f"[실전] 예산 초과로 스킵된 종목: {skipped_budget}개 (포지션당 ${per_position_usd:.2f} 초과)")
    if not candidates:
        logger.info(f"[실전] 나스닥 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought
