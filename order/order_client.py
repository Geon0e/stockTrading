import logging
import requests
from decimal import Decimal
from typing import Dict
from config import Config

logger = logging.getLogger(__name__)

_ORDER_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
_OVERSEAS_ORDER_ENDPOINT = "/uapi/overseas-stock/v1/trading/order"
_OVERSEAS_PRICE_ENDPOINT = "/uapi/overseas-price/v1/quotations/price"
_BALANCE_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"
_HASHKEY_ENDPOINT = "/uapi/hashkey"


class OrderClient:
    def __init__(self, config: Config):
        self._config = config

    def buy(self, stock_code: str, quantity: int, token: str) -> dict:
        return self._place_order("매수", stock_code, quantity, self._config.tr_buy, token)

    def sell(self, stock_code: str, quantity: int, token: str) -> dict:
        return self._place_order("매도", stock_code, quantity, self._config.tr_sell, token)

    def buy_overseas(self, symbol: str, exchange: str, quantity: int, token: str) -> dict:
        price = self._fetch_overseas_price(symbol, exchange, token)
        return self._place_overseas_order("매수", symbol, exchange, quantity, price, self._config.tr_overseas_buy, token)

    def sell_overseas(self, symbol: str, exchange: str, quantity: int, price: str, token: str) -> dict:
        return self._place_overseas_order("매도", symbol, exchange, quantity, price, self._config.tr_overseas_sell, token)

    def get_holdings(self, token: str) -> Dict[str, int]:
        """보유 종목 조회. {종목코드: 수량} 형태로 반환"""
        params = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = f"{self._config.base_url}{_BALANCE_ENDPOINT}"
        resp = requests.get(url, headers=self._headers(self._config.tr_balance, token), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"잔고 조회 실패: {data.get('msg1')}")

        return {
            item["pdno"]: int(item.get("hldg_qty", "0"))
            for item in data.get("output1", [])
            if item.get("pdno") and int(item.get("hldg_qty", "0")) > 0
        }

    def get_holdings_detail(self, token: str) -> Dict[str, dict]:
        """보유 종목 상세 조회. {종목코드: {"qty": int, "profit_rate": Decimal}} 형태로 반환"""
        params = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = f"{self._config.base_url}{_BALANCE_ENDPOINT}"
        resp = requests.get(url, headers=self._headers(self._config.tr_balance, token), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"잔고 조회 실패: {data.get('msg1')}")

        result = {}
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", "0"))
            if not item.get("pdno") or qty <= 0:
                continue
            result[item["pdno"]] = {
                "qty": qty,
                "profit_rate": Decimal(item.get("evlu_pfls_rt", "0")),
            }
        return result

    def _fetch_overseas_price(self, symbol: str, exchange: str, token: str) -> str:
        params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}
        headers = self._headers("HHDFS00000300", token)
        url = f"{self._config.base_url}{_OVERSEAS_PRICE_ENDPOINT}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"해외 가격 조회 실패 [{symbol}]: {data.get('msg1')}")
        return data["output"]["last"]

    def _place_overseas_order(self, side: str, symbol: str, exchange: str, quantity: int, price: str, tr_id: str, token: str) -> dict:
        body = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_DVSN": "00",       # 지정가
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": price,
            "ORD_SVR_DVSN": "0",
        }
        url = f"{self._config.base_url}{_OVERSEAS_ORDER_ENDPOINT}"
        resp = requests.post(url, headers=self._headers(tr_id, token), json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"{side} 주문 실패 [{symbol}]: {data.get('msg1')}")
        logger.info(f"[{self._config.mode}] 해외 {side} 완료 | {exchange}:{symbol} {quantity}주 @ {price}")
        return data

    def _place_order(self, side: str, stock_code: str, quantity: int, tr_id: str, token: str) -> dict:
        body = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": "01",       # 시장가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        }
        headers = self._headers(tr_id, token)
        if self._config.mode == "real":
            headers["hashkey"] = self._get_hash_key(body)

        url = f"{self._config.base_url}{_ORDER_ENDPOINT}"
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"{side} 주문 실패 [{stock_code}]: {data.get('msg1')}")

        logger.info(f"[{self._config.mode}] {side} 완료 | {stock_code} {quantity}주")
        return data

    def _get_hash_key(self, body: dict) -> str:
        url = f"{self._config.base_url}{_HASHKEY_ENDPOINT}"
        headers = {
            "content-type": "application/json",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json().get("HASH", "")

    def _headers(self, tr_id: str, token: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": tr_id,
        }
