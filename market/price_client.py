import logging
import requests
from decimal import Decimal
from typing import List
from config import Config

logger = logging.getLogger(__name__)

_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
_TR_ID = "FHKST01010400"  # 모의/실제 공통
_CURRENT_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
_CURRENT_PRICE_TR_ID = "FHKST01010100"
_OVERSEAS_DAILY_ENDPOINT = "/uapi/overseas-price/v1/quotations/dailyprice"
_OVERSEAS_DAILY_TR_ID = "HHDFS76240000"


class PriceClient:
    def __init__(self, config: Config):
        self._config = config

    def fetch_closing_prices(self, stock_code: str, count: int, token: str) -> List[Decimal]:
        """일별 종가를 오래된 순으로 반환 (이동평균 계산용)"""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        url = f"{self._config.base_url}{_ENDPOINT}"
        resp = requests.get(url, headers=self._headers(token), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"가격 조회 실패 [{stock_code}]: {data.get('msg1')}")

        # KIS API는 최신순(내림차순) 반환 → 오래된 순으로 뒤집기
        rows = data.get("output", [])
        prices = [Decimal(row["stck_clpr"]) for row in rows if row.get("stck_clpr")]

        if len(prices) < count:
            raise RuntimeError(f"데이터 부족: {count}개 필요, {len(prices)}개 조회됨")

        prices = prices[:count]
        prices.reverse()
        return prices

    def fetch_current_price(self, stock_code: str, token: str) -> Decimal:
        """국내주식 실시간 현재가 조회"""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        headers = self._headers(token)
        headers["tr_id"] = _CURRENT_PRICE_TR_ID
        url = f"{self._config.base_url}{_CURRENT_PRICE_ENDPOINT}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"현재가 조회 실패 [{stock_code}]: {data.get('msg1')}")
        return Decimal(data["output"]["stck_prpr"])

    def fetch_overseas_closing_prices(self, symbol: str, exchange: str, count: int, token: str) -> List[Decimal]:
        """해외주식 일별 종가를 오래된 순으로 반환"""
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "GUBN": "0",   # 일봉
            "BYMD": "",    # 오늘 기준
            "MODP": "0",   # 원주가
        }
        url = f"{self._config.base_url}{_OVERSEAS_DAILY_ENDPOINT}"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": _OVERSEAS_DAILY_TR_ID,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"해외 가격 조회 실패 [{symbol}]: {data.get('msg1')}")

        rows = data.get("output2", [])
        prices = [Decimal(row["clos"]) for row in rows if row.get("clos") and row["clos"] != "0"]

        if len(prices) < count:
            raise RuntimeError(f"데이터 부족: {count}개 필요, {len(prices)}개 조회됨")

        prices = prices[:count]
        prices.reverse()
        return prices

    def _headers(self, token: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": _TR_ID,
        }
