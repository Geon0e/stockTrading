import logging
import time
import datetime
import requests
from typing import List, Dict
from config import Config
from strategy.base_strategy import BaseStrategy
from screener.stock_list import fetch_all_stock_codes
from screener.us_stock_list import fetch_us_stocks
from screener.name_lookup import get_stock_name

logger = logging.getLogger(__name__)

_VOLUME_RANK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"


class StockScreener:
    def __init__(self, config: Config, price_client, strategy: BaseStrategy):
        self._config = config
        self._price_client = price_client
        self._strategy = strategy

    def scan(self, token: str, all_stocks: bool = False, top_n: int = 100) -> List[Dict]:
        """골든크로스 조건에 맞는 종목 반환.

        watchlist 설정 시 해당 종목만 스캔.
        all_stocks=True : KOSPI + KOSDAQ 전종목 스캔 (~2,500개)
        all_stocks=False: 거래량 상위 top_n개만 스캔
        """
        watchlist = list(self._config.watchlist)
        if watchlist:
            codes = watchlist
            logger.info(f"워치리스트 스캔: {len(codes)}개 종목")
        elif all_stocks:
            codes = fetch_all_stock_codes()
        else:
            codes = self._fetch_volume_top(token, top_n)

        total = len(codes)
        logger.info(f"스크리닝 시작: {total}개 종목")

        results = []
        for i, code in enumerate(codes, 1):
            try:
                prices = self._price_client.fetch_closing_prices(
                    code, self._strategy.required_data_points, token
                )
                if self._strategy.should_buy(prices):
                    name = get_stock_name(code)
                    results.append({
                        "code": code,
                        "name": name,
                        "price": prices[-1],
                        "signal_type": "골든크로스",
                        "signal_detected_at": datetime.datetime.now().isoformat(),
                        "market": "KR",
                    })
                    label = f"{code}({name})" if name else code
                    logger.info(f"골든크로스 감지: {label} | 현재가: {prices[-1]}")
                if i % 200 == 0:
                    logger.info(f"진행: {i}/{total} | 감지: {len(results)}개")
                time.sleep(0.05)  # API rate limit 방지
            except Exception as e:
                logger.debug(f"{code} 스킵: {e}")

        logger.info(f"스크리닝 완료: {total}개 중 {len(results)}개 골든크로스")
        return results

    def scan_us(self, token: str, mode: str = "nasdaq100") -> List[Dict]:
        """미국 주식 골든크로스 스캔.

        mode: nasdaq100 | sp500 | all
        """
        stocks = fetch_us_stocks(mode)
        total  = len(stocks)
        label  = {"nasdaq100": "나스닥100", "sp500": "S&P500", "all": "미국 전종목"}.get(mode, mode)
        logger.info(f"{label} 스크리닝 시작: {total}개 종목")

        results = []
        for i, stock in enumerate(stocks, 1):
            symbol   = stock["symbol"]
            exchange = stock["exchange"]
            try:
                prices = self._price_client.fetch_overseas_closing_prices(
                    symbol, exchange, self._strategy.required_data_points, token
                )
                if self._strategy.should_buy(prices):
                    name = get_stock_name(symbol)
                    results.append({
                        "code": symbol,
                        "name": name,
                        "price": prices[-1],
                        "exchange": exchange,
                        "signal_type": "골든크로스",
                        "signal_detected_at": datetime.datetime.now().isoformat(),
                        "market": "US",
                    })
                    label = f"{symbol}({name})" if name else symbol
                    logger.info(f"골든크로스 감지: {label} ({exchange}) | 현재가: {prices[-1]}")
                time.sleep(0.1)
            except Exception as e:
                logger.debug(f"{symbol} 스킵: {e}")
            if i % 100 == 0:
                logger.info(f"진행: {i}/{total} | 감지: {len(results)}개")

        logger.info(f"{label} 스크리닝 완료: {total}개 중 {len(results)}개 골든크로스")
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
