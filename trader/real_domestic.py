import logging
import datetime
import queue
import threading

from screener.name_lookup import get_stock_name
from trader.utils import traded_today as _traded_today, get_daily_budget, deduct_daily_budget, add_daily_budget
from trader.matagi import check_matagi_conditions
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
    _exclude = set(config.exclude_list)
    for stock_code, info in list(holdings.items()):
        if stock_code in _traded_today(ctx):
            continue
        if stock_code in _exclude:
            continue
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
                # 현재가 기준: avg_price 기준이면 이미 더 하락한 경우 지정가가 현재가 위에 위치해 미체결
                limit_price = OrderClient._round_to_tick(int(current_price * (1 - limit_pct / 100)))
                try:
                    result = ctx["order_client"].sell(stock_code, qty, token, limit_price=limit_price)
                except Exception as e:
                    logger.warning(f"[실전] 손절 매도 실패 [{stock_code}]: {e} — 당일 재시도 중단")
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
                del holdings[stock_code]
                ctx["holdings_cache"].pop(stock_code, None)
                ctx.get("matagi_count", {}).pop(stock_code, None)
                if ctx.get("realtime_price"):
                    ctx["realtime_price"].unsubscribe([stock_code])
                add_daily_budget(ctx, int(exec_price_f * qty),
                                 profit_amount=int((exec_price_f - avg_price) * qty))
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
                logger.warning(f"[실전] 전략 매도 실패 [{stock_code}]: {e} — 당일 재시도 중단")
                _traded_today(ctx).add(stock_code)
                continue
            order_no = result.get("output", {}).get("ODNO", "")
            exec_info = ctx["order_client"].get_execution(stock_code, order_no, token, side="sell")
            exec_price_str = exec_info["exec_price"] if exec_info else str(current_price)
            exec_time = exec_info["exec_time"] if exec_info else ""
            exec_price_f = float(exec_price_str)
            actual_profit_pct = round((exec_price_f - avg_price) / avg_price * 100, 2) if avg_price > 0 else 0
            ctx["trade_logger"].log("SELL", stock_code, qty, result, signal_type="데드크로스",
                                    exec_price=exec_price_str, exec_confirmed_at=exec_time,
                                    profit_rate=actual_profit_pct)
            del holdings[stock_code]
            ctx["holdings_cache"].pop(stock_code, None)
            ctx.get("matagi_count", {}).pop(stock_code, None)
            if ctx.get("realtime_price"):
                ctx["realtime_price"].unsubscribe([stock_code])
            add_daily_budget(ctx, int(exec_price_f * qty),
                             profit_amount=int((exec_price_f - avg_price) * qty))
            if _tg(ctx):
                tg_notify_sell(_tg(ctx), stock_code, qty, prices[-1])
            logger.info(f"[실전] 데드크로스 매도: {label} | 체결가: {exec_price_f:,.0f}원 | 수익률: {actual_profit_pct:+.2f}%")

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

    # 이전 사이클 scan_thread가 남아있으면 정리 (join timeout 초과 잔존 방지)
    _prev_thread: threading.Thread | None = ctx.pop("_scan_thread", None)
    _prev_stop: threading.Event | None    = ctx.pop("_scan_stop_event", None)
    if _prev_thread and _prev_thread.is_alive():
        logger.debug("[실전] 이전 스캔 스레드 정리 중...")
        if _prev_stop:
            _prev_stop.set()
        _prev_thread.join(timeout=35)
        if _prev_thread.is_alive():
            logger.warning("[실전] 이전 스캔 스레드가 35초 후에도 실행 중 — 강제 진행")

    # 스캔을 백그라운드 스레드로 돌리고 신호 감지 즉시 매수 처리 (파이프라인)
    signal_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    def _run_scan():
        try:
            ctx["screener"].scan(
                token,
                all_stocks=config.scan_all_stocks,
                signal_queue=signal_queue,
                stop_event=stop_event,
            )
        except Exception as e:
            logger.warning(f"[실전] 스캔 스레드 오류: {e}")
        finally:
            signal_queue.put(None)  # 스캔 완료 sentinel

    scan_thread = threading.Thread(target=_run_scan, daemon=True, name="screener")
    scan_thread.start()

    skipped_budget = 0
    total_signals = 0
    _SCAN_TIMEOUT = 180  # 스캔 최대 대기 시간(초) — 비정상 종료 안전장치

    while bought < capacity:
        try:
            candidate = signal_queue.get(timeout=_SCAN_TIMEOUT)
        except queue.Empty:
            logger.warning("[실전] 스캔 타임아웃 — 신호 대기 종료")
            break
        if candidate is None:
            break  # 스캔 완료

        total_signals += 1
        code = candidate["code"]
        signal_type = candidate.get("signal_type", "골든크로스")
        signal_time = candidate.get("signal_detected_at", datetime.datetime.now().isoformat())
        price = int(candidate["price"])

        if code in _traded_today(ctx):
            _n = get_stock_name(code)
            logger.info(f"[매수 스킵] 당일 이미 거래: {code}({_n})" if _n else f"[매수 스킵] 당일 이미 거래: {code}")
            continue

        if code in holdings:
            avg_p = float(holdings[code].get("avg_price") or 0)
            if avg_p <= 0 or price >= avg_p:
                _n = get_stock_name(code)
                _lbl = f"{code}({_n})" if _n else code
                logger.info(f"[매수 스킵] 보유 중 수익 종목: {_lbl} | 매입가: {avg_p:,.0f}원 | 현재가: {price:,}원")
                continue
            # 최대 물타기 횟수 초과 시 스킵
            matagi_count = ctx.get("matagi_count", {})
            stage = matagi_count.get(code, 0)
            max_count = ctx["config"].matagi_max_count
            _name = get_stock_name(code)
            _label = f"{code}({_name})" if _name else code
            if max_count > 0 and stage >= max_count:
                logger.info(f"[실전] 물타기 횟수 초과 [{_label}]: {stage}/{max_count}회 완료")
                continue
            # 단계별 물타기 조건 확인
            ok, reason = check_matagi_conditions(
                ctx["price_client"], code, token, avg_p, price,
                drop_pct=ctx["config"].matagi_drop_pct,
                matagi_stage=stage,
            )
            if not ok:
                logger.info(f"[실전] 물타기 스킵 [{_label}]: {reason}")
                continue
            signal_type = "물타기"
            logger.info(f"[실전] 물타기 {stage + 1}차 조건 통과 [{_label}]: {reason}")

        # 예산 초과 종목 스킵
        if price > per_position:
            skipped_budget += 1
            _n = get_stock_name(code)
            _lbl = f"{code}({_n})" if _n else code
            logger.info(
                f"[매수 스킵] 예산 초과: {_lbl} | 주가: {price:,}원 > 포지션예산: {per_position:,}원"
            )
            continue

        # 수량 자동 계산: 포지션 예산을 주가로 나눔 (당일 잔여예산 재확인)
        available = min(per_position, get_daily_budget(ctx))
        quantity = available // price
        if config.order_quantity > 0:
            quantity = min(quantity, config.order_quantity)
        if quantity < 1:
            _n = get_stock_name(code)
            _lbl = f"{code}({_n})" if _n else code
            logger.info(f"[매수 스킵] 잔여예산 부족으로 수량 0: {_lbl} | 가용예산: {available:,}원 | 주가: {price:,}원")
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
        is_executed = exec_info is not None

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

        if not is_executed:
            # 미체결: 캐시에 올리지 않음 — holdings_cache 기반 손절/매도 로직이 오동작하지 않도록
            _traded_today(ctx).add(code)
            logger.info(f"[실전] 주문 접수 (미체결): {label} | 주문번호: {order_no}")
            bought += 1
            continue

        holdings[code] = {"qty": quantity, "avg_price": exec_price}
        ctx["holdings_cache"][code] = {"qty": quantity, "avg_price": float(exec_price)}
        # 물타기였으면 단계 카운터 증가
        if signal_type == "물타기":
            mc = ctx.get("matagi_count", {})
            mc[code] = mc.get(code, 0) + 1
            ctx["matagi_count"] = mc
        cost = int(float(exec_price) * quantity)
        deduct_daily_budget(ctx, cost)
        if ctx.get("realtime_price"):
            ctx["realtime_price"].subscribe([code])
        bought += 1
        logger.info(f"[실전] 매수 완료: {label} | {quantity}주 @ {exec_price}원 | 당일 잔여예산: {get_daily_budget(ctx):,}원")

    # capacity 채워지면 스캐너 조기 종료
    stop_event.set()
    scan_thread.join(timeout=35)  # API call 최악 케이스(페이지네이션 2회×10s) 커버
    if scan_thread.is_alive():
        # 35초 후에도 살아있으면 다음 사이클에서 정리
        ctx["_scan_thread"]      = scan_thread
        ctx["_scan_stop_event"]  = stop_event

    if skipped_budget:
        logger.info(f"[실전] 예산 초과로 스킵된 종목: {skipped_budget}개 (포지션당 {per_position:,}원 초과)")
    if total_signals == 0:
        logger.info(f"[실전] 골든크로스 종목 없음 | 보유: {len(holdings)}개")

    return bought
