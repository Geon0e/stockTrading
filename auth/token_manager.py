import json
import time
import logging
import requests
from pathlib import Path
from config import Config

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".token_cache")


class TokenManager:
    def __init__(self, config: Config):
        self._config = config
        self._token: str | None = None
        self._expires_at: float = 0
        self._cache_file = _CACHE_DIR / f"token_{config.mode}.json"
        _CACHE_DIR.mkdir(exist_ok=True)
        self._load_cache()

    def get_valid_token(self) -> str:
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        return self._refresh_token()

    def _load_cache(self) -> None:
        if not self._cache_file.exists():
            return
        try:
            data = json.loads(self._cache_file.read_text())
            self._token = data["token"]
            self._expires_at = data["expires_at"]
            logger.info(f"[{self._config.mode}] 캐시된 토큰 로드")
        except Exception:
            pass

    def _save_cache(self) -> None:
        self._cache_file.write_text(
            json.dumps({"token": self._token, "expires_at": self._expires_at})
        )

    def _refresh_token(self) -> str:
        url = f"{self._config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)

        if resp.status_code == 403:
            raise RuntimeError(f"토큰 발급 실패 (1분당 1회 제한): {resp.json().get('error_description')}")

        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"토큰 발급 실패: {data}")

        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 86400))
        self._save_cache()
        logger.info(f"[{self._config.mode}] 액세스 토큰 갱신 완료")
        return self._token
