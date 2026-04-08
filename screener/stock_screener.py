import logging
import time
import requests
from decimal import Decimal
from typing import List, Dict
from config import Config
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

_VOLUME_RANK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"


class StockScreener:
    def __init__(self, config: Config, price_client, strategy: BaseStrategy):
        self._config = config
        self._price_client = price_client
        self._strategy = strategy

    def scan(self, token: str, top_n: int = 100) -> List[Dict]:
        """거래량 상위 종목 중 골든크로스 조건에 맞는 종목 반환"""
        stock_codes = self._fetch_volume_top(token, top_n)
        logger.info(f"스크리닝 대상: {len(stock_codes)}개 종목")

        results = []
        for code in stock_codes:
            try:
                prices = self._price_client.fetch_closing_prices(
                    code, self._strategy.required_data_points, token
                )
                if self._strategy.should_buy(prices):
                    results.append({"code": code, "price": prices[-1]})
                    logger.info(f"골든크로스 감지: {code} | 현재가: {prices[-1]}")
                time.sleep(0.05)  # API rate limit 방지
            except Exception as e:
                logger.debug(f"{code} 스킵: {e}")
                continue

        return results

    def _fetch_volume_top(self, token: str, top_n: int) -> List[str]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": "FHPST01710000",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0001",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": "",
        }
        url = f"{self._config.base_url}{_VOLUME_RANK_ENDPOINT}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"거래량 순위 조회 실패: {data.get('msg1')}")

        codes = [item["mksc_shrn_iscd"] for item in data.get("output", [])[:top_n]]
        logger.info(f"거래량 상위 {len(codes)}개 종목 조회 완료")
        return codes
