import os
import time
import logging
from datetime import datetime

import requests
import urllib3
from screener.name_lookup import get_stock_name

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._api    = f"https://api.telegram.org/bot{token}"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송 (4000자 초과 시 자동 분할)"""
        ok = True
        for chunk in _split(text, 4000):
            resp = requests.post(
                f"{self._api}/sendMessage",
                json={"chat_id": self.chat_id, "text": chunk, "parse_mode": parse_mode},
                timeout=15,
                verify=False,
            )
            if not resp.ok:
                logger.error(f"텔레그램 전송 실패: {resp.text}")
                ok = False
        return ok


def _split(text: str, limit: int) -> list:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def from_env() -> TelegramBot | None:
    """환경변수에서 TelegramBot 생성. 미설정 시 None 반환"""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID",   "")
    if not token or not chat_id:
        return None
    return TelegramBot(token, chat_id)


def notify_signal(bot: TelegramBot, code: str, price, signal_type: str, market: str = "KR") -> None:
    """골든크로스 신호 감지 알림"""
    price_str = f"{int(price):,}원" if market == "KR" else f"${price}"
    name = get_stock_name(code)
    name_line = f"종목명   : {name}\n" if name else ""
    msg = (
        f"📊 <b>골든크로스 신호 감지</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"{name_line}"
        f"현재가   : {price_str}\n"
        f"신호     : {signal_type}\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 신호 알림 전송 실패: {code}")


def notify_order_placed(bot: TelegramBot, code: str, quantity: int, price, order_no: str, market: str = "KR") -> None:
    """매수 주문 접수 알림"""
    price_str = f"{int(price):,}원" if market == "KR" else f"${price}"
    name = get_stock_name(code)
    name_line = f"종목명   : {name}\n" if name else ""
    msg = (
        f"🟡 <b>매수 주문 접수</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"{name_line}"
        f"수   량  : {quantity}주\n"
        f"주문가   : {price_str}\n"
        f"주문번호 : {order_no}\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 주문접수 알림 전송 실패: {code}")


def notify_buy(bot: TelegramBot, code: str, quantity: int, price, signal_type: str = "",
               signal_time: str = "", exec_price=None, market: str = "KR") -> None:
    """매수 체결 완료 알림"""
    price_fmt = (lambda p: f"{int(p):,}원") if market == "KR" else (lambda p: f"${p}")
    name = get_stock_name(code)
    name_line = f"종목명   : {name}\n" if name else ""
    msg = (
        f"✅ <b>매수 체결 완료</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"{name_line}"
        f"수   량  : {quantity}주\n"
        f"체결가   : {price_fmt(exec_price if exec_price else price)}\n"
        f"신호종류 : {signal_type or '골든크로스'}\n"
        f"신호발생 : {signal_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 매수체결 알림 전송 실패: {code}")


def notify_sell(bot: TelegramBot, code: str, quantity: int, price, signal_type: str = "",
                buy_price=None, market: str = "KR") -> None:
    """매도 체결 알림"""
    price_fmt = (lambda p: f"{int(p):,}원") if market == "KR" else (lambda p: f"${p}")
    name = get_stock_name(code)
    name_line = f"종목명   : {name}\n" if name else ""
    pnl = ""
    if buy_price:
        rate = (float(price) - float(buy_price)) / float(buy_price) * 100
        sign = "+" if rate >= 0 else ""
        pnl = f"\n수익률   : {sign}{rate:.2f}%"
    msg = (
        f"🔴 <b>매도 체결</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"{name_line}"
        f"수   량  : {quantity}주\n"
        f"체결가   : {price_fmt(price)}\n"
        f"신호종류 : {signal_type or '데드크로스'}{pnl}\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 매도체결 알림 전송 실패: {code}")


def ask_confirm(bot: TelegramBot, code: str, price, signal_type: str,
                market: str = "KR", timeout: int = 300) -> bool:
    """인라인 키보드로 매수 확인 요청.
    timeout초 내 응답 없으면 False 반환 (기본 5분).
    """
    price_str = f"${price}" if market == "US" else f"{int(price):,}원"
    name = get_stock_name(code)
    name_line = f"종목명   : {name}\n" if name else ""
    text = (
        f"🔔 <b>[실전] 매수 신호 발생</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"{name_line}"
        f"현재가   : {price_str}\n"
        f"신호     : {signal_type}\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"매수하시겠습니까?"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ 매수", "callback_data": f"buy_{code}"},
        {"text": "❌ 취소", "callback_data": f"cancel_{code}"},
    ]]}

    resp = requests.post(
        f"{bot._api}/sendMessage",
        json={"chat_id": bot.chat_id, "text": text,
              "parse_mode": "HTML", "reply_markup": keyboard},
        timeout=15, verify=False,
    )
    if not resp.ok:
        logger.error(f"확인 메시지 전송 실패: {resp.text}")
        return False

    message_id = resp.json()["result"]["message_id"]

    # 현재 offset 파악 (이미 처리된 업데이트 건너뜀)
    upd = requests.get(f"{bot._api}/getUpdates",
                       params={"offset": -1, "timeout": 1},
                       timeout=5, verify=False)
    offset = 0
    if upd.ok and upd.json().get("result"):
        offset = upd.json()["result"][-1]["update_id"] + 1

    # 응답 대기
    deadline = time.time() + timeout
    while time.time() < deadline:
        poll_sec = min(30, int(deadline - time.time()))
        try:
            upd = requests.get(
                f"{bot._api}/getUpdates",
                params={"offset": offset, "timeout": poll_sec,
                        "allowed_updates": ["callback_query"]},
                timeout=poll_sec + 5, verify=False,
            )
        except requests.RequestException:
            time.sleep(2)
            continue

        if not upd.ok:
            time.sleep(2)
            continue

        for update in upd.json().get("result", []):
            offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if not cb:
                continue
            if cb.get("message", {}).get("message_id") != message_id:
                continue

            # 버튼 클릭 처리
            requests.post(f"{bot._api}/answerCallbackQuery",
                          json={"callback_query_id": cb["id"]},
                          timeout=5, verify=False)

            result = cb.get("data", "").startswith("buy_")
            label  = "✅ 매수 확인" if result else "❌ 취소됨"
            bot.send(f"{label} — <code>{code}</code>")
            return result

    # 타임아웃
    bot.send(f"⏰ <code>{code}</code> 확인 시간 초과 ({timeout//60}분). 건너뜀.")
    logger.warning(f"매수 확인 타임아웃: {code}")
    return False


def notify_take_profit_sell(bot: TelegramBot, code: str, quantity: int, profit_rate) -> None:
    """익절 매도 알림"""
    msg = (
        f"💰 <b>익절 매도 체결</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"수   량  : {quantity}주\n"
        f"수익률   : +{profit_rate}%\n"
        f"신호     : 수익률 5% 이상 익절\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 익절 알림 전송 실패: {code}")


def notify_scan_result(bot: TelegramBot, results: list) -> None:
    """골든크로스 스캔 결과 알림"""
    if not results:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📈 <b>골든크로스 감지 {len(results)}개</b>  |  {now}\n"]
    for r in results[:10]:
        code = r['code']
        price_str = f"{int(r['price']):,}원" if r.get("market", "KR") == "KR" else f"${r['price']}"
        name = get_stock_name(code)
        label = f"{name} (<code>{code}</code>)" if name else f"<code>{code}</code>"
        lines.append(f"• {label}  {price_str}  [{r.get('signal_type', '골든크로스')}]")
    if not bot.send("\n".join(lines)):
        logger.error("텔레그램 스캔 결과 알림 전송 실패")
