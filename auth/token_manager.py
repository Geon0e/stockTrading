import time
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)


class TokenManager:
    def __init__(self, config: Config):
        self._config = config
        self._token: str | None = None
        self._expires_at: float = 0

    def get_valid_token(self) -> str:
        if self._token and time.time() < self._expires_at - 300:  # 만료 5분 전 갱신
            return self._token
        return self._refresh_token()

    def _refresh_token(self) -> str:
        url = f"{self._config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"토큰 발급 실패: {data}")

        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 86400))
        logger.info(f"[{self._config.mode}] 액세스 토큰 갱신 완료")
        return self._token
