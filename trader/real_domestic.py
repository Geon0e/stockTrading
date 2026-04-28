import logging
import datetime

from screener.name_lookup import get_stock_name
from trader.utils import traded_today as _traded_today, get_daily_budget, deduct_daily_budget, add_daily_budget
from notifications.telegram_notifier import (
    notify_signal as tg_notify_signal,
    notify_order_placed as tg_notify_order_placed,
    notify_buy as tg_notify_buy,
    notify_sell as tg_notify_sell,
    notify_scan_result as tg_notify_scan,
)

logger = logging.getLogger(__name__)


def _tg(ctx):
    return ctx.get("telegram_bot")


def run_real_domestic_cycle(ctx: dict, token: str, skip_buy: bool = False) -> int:
    """실전 국내 매매 사이클.

    mock과의 차이:
    - 포지션당 예산 = real_budget / max_positions
    - 매수 수량 = 포지션당 예산 // 현재가 (자동 계산)
    - 현재가 > 포지션당 예산인 종목은 스킵
    """
    config = ctx["config"]
    logger.info(
        f"[실전] 국내 매매 시작 | 총예산: {config.real_budget:,}원 | "
        f"당일 잔여: {get_daily_budget(ctx):,}원"
    )

    holdings = ctx["order_client"].get_holdings(token)

    # ── 매도 ────────────────────────────────────────────────────────────
    for stock_code, info in list(holdings.items()):
        qty = info["qty"]
        avg_price = float(info.get("avg_price") or 0)
        try:
            prices = ctx["price_client"].fetch_closing_prices(
                stock_code, ctx["strategy"].required_data_points, token
            )
        except Exception as e:
            logger.warning(f"[실전] 가격 조회 실패 [{stock_code}]: {e}")
            continue

        current_price = float(prices[-1])
        name = get_stock_name(stock_code)
        label = f"{stock_code}({name})" if name else stock_code

        # 손절
        if config.stop_loss_pct > 0 and avg_price > 0:
            drop_pct = (current_price - avg_price) / avg_price * 100
            if drop_pct <= -config.stop_loss_pct:
                from order.order_client import OrderClient
                limit_pct = config.stop_loss_limit_pct or config.stop_loss_pct
                limit_price = OrderClient._round_to_tick(int(avg_price * (1 - limit_pct / 100)))
                try:
                    result = ctx["order_client"].sell(stock_code, qty, token, limit_price=limit_price)
                except Exception as e:
                    logger.warning(f"[실전] 손절 매도 실패 [{stock_code}]: {e} — 스킵")
                    continue
                actual_profit_pct = round((limit_price - avg_price) / avg_price * 100, 2)
                ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="손절",
                                        exec_price=str(limit_price), profit_rate=actual_profit_pct)
                _traded_today(ctx).add(stock_code)
                del holdings[stock_code]
                if _tg(ctx):
                    tg_notify_sell(_tg(ctx), stock_code, qty, current_price, signal_type="손절")
                logger.info(
                    f"[실전] 손절 매도: {label} | 매입가: {avg_price:,.0f}원 | "
                    f"지정가: {limit_price:,}원 | 수익률: {actual_profit_pct:+.2f}%"
                )
                continue

        # 전략 매도 신호 (손실 중인 종목은 데드크로스 매도 제외)
        in_loss = avg_price > 0 and current_price < avg_price
        if in_loss and ctx["strategy"].should_sell(prices):
            profit_pct = (current_price - avg_price) / avg_price * 100
            logger.info(f"[실전] 전략 매도 스킵 (손실 중 {profit_pct:.1f}%): {label}")
        elif ctx["strategy"].should_sell(prices):
            try:
                result = ctx["order_client"].sell(stock_code, qty, token)
            except Exception as e:
                logger.warning(f"[실전] 전략 매도 실패 [{stock_code}]: {e} — 스킵")
                continue
            ctx["trade_logger"].log("SELL", stock_code, qty, result)
            del holdings[stock_code]
            if _tg(ctx):
                tg_notify_sell(_tg(ctx), stock_code, qty, prices[-1])
            logger.info(f"[실전] 데드크로스 매도: {label}")

    # ── 매수 ────────────────────────────────────────────────────────────
    bought = 0
    if skip_buy:
        logger.info("[실전] 오늘 이미 매수 완료 — 매수 건너뜀")
        return 0
    remaining = get_daily_budget(ctx)
    if remaining <= 0:
        logger.info("[실전] 당일 예산 소진 — 매수 건너뜀")
        return 0
    capacity = config.max_positions - len(holdings)
    if capacity <= 0:
        logger.info(f"[실전] 최대 포지션 도달 ({config.max_positions}개) — 매수 건너뜀")
        return 0
    per_position = min(config.real_budget // config.max_positions, remaining)

    candidates = ctx["screener"].scan(token, all_stocks=config.scan_all_stocks)
    if candidates and _tg(ctx):
        tg_notify_scan(_tg(ctx), candidates)

    skipped_budget = 0
    for candidate in candidates:
        if bought >= capacity:
            break

        code = candidate["code"]
        signal_type = candidate.get("signal_type", "골든크로스")
        signal_time = candidate.get("signal_detected_at", datetime.datetime.now().isoformat())
        price = int(candidate["price"])

        if code in _traded_today(ctx):
            continue

        if code in holdings:
            avg_p = float(holdings[code].get("avg_price") or 0)
            if avg_p <= 0 or price >= avg_p:
                logger.debug(f"[실전] 보유 중 수익 종목 추가매수 스킵: {code} | 매입가: {avg_p:,.0f}원 | 현재가: {price:,}원")
                continue
            signal_type = "물타기"

        # 예산 초과 종목 스킵
        if price > per_position:
            skipped_budget += 1
            logger.debug(
                f"[실전] 예산 초과 스킵: {code} | 주가: {price:,}원 > 포지션예산: {per_position:,}원"
            )
            continue

        # 수량 자동 계산: 포지션 예산을 주가로 나눔 (당일 잔여예산 재확인)
        available = min(per_position, get_daily_budget(ctx))
        quantity = available // price
        if config.order_quantity > 0:
            quantity = min(quantity, config.order_quantity)
        if quantity < 1:
            continue

        name = get_stock_name(code)
        label = f"{code}({name})" if name else code
        estimated_cost = price * quantity
        logger.info(
            f"[실전] 매수 후보: {label} | {price:,}원 × {quantity}주 = {estimated_cost:,}원 "
            f"(포지션예산 {per_position:,}원 중 {estimated_cost / per_position * 100:.1f}% 사용)"
        )

        # 1단계: 신호 알림
        if _tg(ctx):
            tg_notify_signal(_tg(ctx), code, price, signal_type)

        # 2단계: 매수 주문 (시장가 or 지정가)
        config = ctx["config"]
        limit_price = None
        if config.order_type == "limit":
            limit_price = round(price * (1 + config.limit_order_pct / 100))
            logger.info(f"[실전] 지정가 주문: {label} | 신호가 {price:,}원 × (1+{config.limit_order_pct}%) = {limit_price:,}원")
        result = ctx["order_client"].buy(code, quantity, token, limit_price=limit_price)
        order_no = result.get("output", {}).get("ODNO", "")
        if _tg(ctx):
            tg_notify_order_placed(_tg(ctx), code, quantity, limit_price or price, order_no)

        # 3단계: 체결 확인
        exec_info = ctx["order_client"].get_execution(code, order_no, token)
        exec_price = exec_info["exec_price"] if exec_info else str(limit_price or price)
        exec_time = exec_info["exec_time"] if exec_info else ""
        if _tg(ctx):
            tg_notify_buy(
                _tg(ctx), code, quantity, limit_price or price,
                signal_type=signal_type, signal_time=signal_time,
                exec_price=exec_price,
            )
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
        bought += 1
        logger.info(f"[실전] 매수 완료: {label} | {quantity}주 @ {exec_price}원 | 당일 잔여예산: {get_daily_budget(ctx):,}원")

    if skipped_budget:
        logger.info(f"[실전] 예산 초과로 스킵된 종목: {skipped_budget}개 (포지션당 {per_position:,}원 초과)")
    if not candidates:
        logger.info(f"[실전] 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought
