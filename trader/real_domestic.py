import logging
import datetime

from screener.name_lookup import get_stock_name
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


def run_real_domestic_cycle(ctx: dict, token: str) -> int:
    """실전 국내 매매 사이클.

    mock과의 차이:
    - 포지션당 예산 = real_budget / max_positions
    - 매수 수량 = 포지션당 예산 // 현재가 (자동 계산)
    - 현재가 > 포지션당 예산인 종목은 스킵
    """
    config = ctx["config"]
    per_position = config.real_budget // config.max_positions
    logger.info(
        f"[실전] 국내 매매 시작 | 총예산: {config.real_budget:,}원 | "
        f"포지션({config.max_positions}개)당: {per_position:,}원"
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
                try:
                    result = ctx["order_client"].sell(stock_code, qty, token)
                except Exception as e:
                    logger.warning(f"[실전] 손절 매도 실패 [{stock_code}]: {e} — 스킵")
                    continue
                ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="손절")
                del holdings[stock_code]
                if _tg(ctx):
                    tg_notify_sell(_tg(ctx), stock_code, qty, current_price, signal_type="손절")
                logger.info(
                    f"[실전] 손절 매도: {label} | 매입가: {avg_price:,.0f}원 | "
                    f"현재가: {current_price:,.0f}원 | 하락률: {drop_pct:.2f}%"
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
    capacity = config.max_positions - len(holdings)
    if capacity <= 0:
        logger.info(f"[실전] 최대 포지션 도달 ({config.max_positions}개) — 매수 건너뜀")
        return 0

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

        if code in holdings:
            continue

        # 예산 초과 종목 스킵
        if price > per_position:
            skipped_budget += 1
            logger.debug(
                f"[실전] 예산 초과 스킵: {code} | 주가: {price:,}원 > 포지션예산: {per_position:,}원"
            )
            continue

        # 수량 자동 계산: 포지션 예산을 주가로 나눔
        quantity = per_position // price
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

        # 2단계: 매수 주문
        result = ctx["order_client"].buy(code, quantity, token)
        order_no = result.get("output", {}).get("ODNO", "")
        if _tg(ctx):
            tg_notify_order_placed(_tg(ctx), code, quantity, price, order_no)

        # 4단계: 체결 확인
        exec_info = ctx["order_client"].get_execution(code, order_no, token)
        exec_price = exec_info["exec_price"] if exec_info else str(price)
        exec_time = exec_info["exec_time"] if exec_info else ""
        if _tg(ctx):
            tg_notify_buy(
                _tg(ctx), code, quantity, price,
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
        bought += 1
        logger.info(f"[실전] 매수 완료: {label} | {quantity}주 @ {exec_price}원")

    if skipped_budget:
        logger.info(f"[실전] 예산 초과로 스킵된 종목: {skipped_budget}개 (포지션당 {per_position:,}원 초과)")
    if not candidates:
        logger.info(f"[실전] 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought
