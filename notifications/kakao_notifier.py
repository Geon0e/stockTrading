import json
import os
import logging
from datetime import datetime
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
_ENV_PATH = Path(__file__).parent.parent / ".env"


class KakaoBot:
    def __init__(self, rest_api_key: str, access_token: str, refresh_token: str,
                 client_secret: str = ""):
        self.rest_api_key  = rest_api_key
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.client_secret = client_secret

    def _refresh(self) -> bool:
        """액세스 토큰 갱신 후 .env에 저장"""
        data = {
            "grant_type":    "refresh_token",
            "client_id":     self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = requests.post("https://kauth.kakao.com/oauth/token",
                             data=data, verify=False, timeout=15)
        result = resp.json()
        if "access_token" in result:
            self.access_token = result["access_token"]
            if "refresh_token" in result:
                self.refresh_token = result["refresh_token"]
            _update_env("KAKAO_ACCESS_TOKEN",  self.access_token)
            if "refresh_token" in result:
                _update_env("KAKAO_REFRESH_TOKEN", self.refresh_token)
            logger.info("카카오 액세스 토큰 갱신 완료")
            return True
        logger.error(f"카카오 토큰 갱신 실패: {result}")
        return False

    def _send_payload(self, template: dict) -> bool:
        """카카오 API 호출 (토큰 만료 시 자동 갱신 후 재시도)"""
        for attempt in range(2):
            resp = requests.post(
                KAKAO_SEND_URL,
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"template_object": json.dumps(template, ensure_ascii=False)},
                verify=False,
                timeout=15,
            )
            result = resp.json()
            if result.get("result_code") == 0:
                return True
            if resp.status_code in (401, 403) and attempt == 0:
                if self._refresh():
                    continue
            logger.error(f"카카오 전송 실패: {result}")
            return False
        return False

    def send_text(self, text: str) -> bool:
        """텍스트 메시지 전송 (1900자 초과 시 분할)"""
        ok = True
        for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
            template = {
                "object_type": "text",
                "text": chunk,
                "link": {
                    "web_url":        "https://finance.naver.com",
                    "mobile_web_url": "https://finance.naver.com",
                },
            }
            ok &= self._send_payload(template)
        return ok

    def send_list(self, header: str, items: list) -> bool:
        """리스트형 메시지 전송 (최대 5개)"""
        contents = [
            {
                "title":       item.get("title", ""),
                "description": item.get("description", ""),
                "link": {
                    "web_url":        item.get("link", "https://finance.naver.com"),
                    "mobile_web_url": item.get("link", "https://finance.naver.com"),
                },
            }
            for item in items[:5]
        ]
        template = {
            "object_type":  "list",
            "header_title": header,
            "header_link": {
                "web_url":        "https://finance.naver.com",
                "mobile_web_url": "https://finance.naver.com",
            },
            "contents": contents,
        }
        return self._send_payload(template)


def from_env() -> KakaoBot | None:
    """환경변수에서 KakaoBot 생성. 키 미설정 시 None 반환"""
    rest_api_key  = os.getenv("KAKAO_REST_API_KEY",  "")
    access_token  = os.getenv("KAKAO_ACCESS_TOKEN",  "")
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN", "")
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "")
    if not rest_api_key or not access_token:
        return None
    return KakaoBot(rest_api_key, access_token, refresh_token, client_secret)


def notify_buy(bot: KakaoBot, code: str, quantity: int, price) -> None:
    """매수 체결 알림"""
    msg = (
        f"✅ 매수 체결\n"
        f"{'─'*20}\n"
        f"종목코드 : {code}\n"
        f"수   량  : {quantity}주\n"
        f"현재가   : {int(price):,}원\n"
        f"신호     : 골든크로스 (5MA > 20MA)\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send_text(msg):
        logger.error(f"매수 알림 전송 실패: {code}")


def notify_sell(bot: KakaoBot, code: str, quantity: int, price) -> None:
    """매도 체결 알림"""
    msg = (
        f"🔴 매도 체결\n"
        f"{'─'*20}\n"
        f"종목코드 : {code}\n"
        f"수   량  : {quantity}주\n"
        f"현재가   : {int(price):,}원\n"
        f"신호     : 데드크로스 (5MA < 20MA)\n"
        f"{'─'*20}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if not bot.send_text(msg):
        logger.error(f"매도 알림 전송 실패: {code}")


def notify_scan_result(bot: KakaoBot, results: list) -> None:
    """골든크로스 스캔 결과 알림"""
    if not results:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"골든크로스 감지 {len(results)}개 | {now}"
    items = [
        {
            "title":       r["code"],
            "description": f"현재가: {int(r['price']):,}원",
            "link":        f"https://finance.naver.com/item/main.nhn?code={r['code']}",
        }
        for r in results[:5]
    ]
    if not bot.send_list(header, items):
        logger.error("스캔 결과 알림 전송 실패")


def _update_env(key: str, value: str) -> None:
    """실행 중 토큰이 갱신되면 .env 파일에 반영"""
    if not _ENV_PATH.exists():
        return
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated, found = [], False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                updated.append(f"{key}={value}")
                found = True
                continue
        updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    _ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")
