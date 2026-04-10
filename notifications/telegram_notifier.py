import os
import logging
from datetime import datetime

import requests
import urllib3

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


def notify_buy(bot: TelegramBot, code: str, quantity: int, price) -> None:
    """매수 체결 알림"""
    msg = (
        f"✅ <b>매수 체결</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"수   량  : {quantity}주\n"
        f"현재가   : {int(price):,}원\n"
        f"신호     : 골든크로스 (5MA &gt; 20MA)\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 매수 알림 전송 실패: {code}")


def notify_sell(bot: TelegramBot, code: str, quantity: int, price) -> None:
    """매도 체결 알림"""
    msg = (
        f"🔴 <b>매도 체결</b>\n"
        f"{'─'*20}\n"
        f"종목코드 : <code>{code}</code>\n"
        f"수   량  : {quantity}주\n"
        f"현재가   : {int(price):,}원\n"
        f"신호     : 데드크로스 (5MA &lt; 20MA)\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send(msg):
        logger.error(f"텔레그램 매도 알림 전송 실패: {code}")


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
        lines.append(
            f"• <code>{r['code']}</code>  현재가 {int(r['price']):,}원"
        )
    if not bot.send("\n".join(lines)):
        logger.error("텔레그램 스캔 결과 알림 전송 실패")
