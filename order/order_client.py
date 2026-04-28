import logging
import time
import datetime
import requests
from decimal import Decimal
from typing import Dict, Optional
from config import Config

logger = logging.getLogger(__name__)

_ORDER_ENDPOINT        = "/uapi/domestic-stock/v1/trading/order-cash"
_EXECUTION_ENDPOINT    = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_OVERSEAS_ORDER_ENDPOINT = "/uapi/overseas-stock/v1/trading/order"
_OVERSEAS_PRICE_ENDPOINT = "/uapi/overseas-price/v1/quotations/price"
_BALANCE_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"
_HASHKEY_ENDPOINT = "/uapi/hashkey"


class OrderClient:
    def __init__(self, config: Config):
        self._config = config

    def buy(self, stock_code: str, quantity: int, token: str, limit_price: int = None) -> dict:
        return self._place_order("매수", stock_code, quantity, self._config.tr_buy, token, limit_price=limit_price)

    def sell(self, stock_code: str, quantity: int, token: str, limit_price: int = None) -> dict:
        return self._place_order("매도", stock_code, quantity, self._config.tr_sell, token, limit_price=limit_price)

    def buy_overseas(self, symbol: str, exchange: str, quantity: int, token: str, limit_price: float = None) -> dict:
        price = str(limit_price) if limit_price else self._fetch_overseas_price(symbol, exchange, token)
        return self._place_overseas_order("매수", symbol, exchange, quantity, price, self._config.tr_overseas_buy, token)

    def sell_overseas(self, symbol: str, exchange: str, quantity: int, token: str) -> dict:
        price = self._fetch_overseas_price(symbol, exchange, token)
        return self._place_overseas_order("매도", symbol, exchange, quantity, price, self._config.tr_overseas_sell, token)

    def get_overseas_holdings(self, token: str) -> Dict[str, dict]:
        """{심볼: {"qty": 수량, "exchange": 거래소}} 형태로 반환"""
        params = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "OVRS_EXCG_CD": "NAS",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        url = f"{self._config.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        resp = requests.get(url, headers=self._headers(self._config.tr_overseas_balance, token), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(f"해외 잔고 조회 실패: {data.get('msg1')}")

        return {
            item["ovrs_pdno"]: {
                "qty":       int(item.get("ovrs_cblc_qty", "0")),
                "exchange":  item.get("ovrs_excg_cd", "NAS"),
                "avg_price": item.get("pchs_avg_pric", "0"),
            }
            for item in data.get("output1", [])
            if item.get("ovrs_pdno") and int(item.get("ovrs_cblc_qty", "0")) > 0
        }

    def get_holdings(self, token: str) -> Dict[str, dict]:
        """보유 종목 조회. {종목코드: {"qty": 수량, "avg_price": 매입평균가}} 형태로 반환"""
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
            item["pdno"]: {
                "qty":       int(item.get("hldg_qty", "0")),
                "avg_price": item.get("pchs_avg_pric", "0"),
            }
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
                "qty":         qty,
                "profit_rate": Decimal(item.get("evlu_pfls_rt", "0")),
                "avg_price":   float(item.get("pchs_avg_pric", "0")),
            }
        return result

    def get_execution(self, stock_code: str, order_no: str, token: str,
                      retries: int = 5, delay: float = 1.0) -> Optional[dict]:
        """주문번호로 체결 내역 조회. 미체결이면 None 반환"""
        tr_id = "VTTC8001R" if self._config.mode == "mock" else "TTTC8001R"
        today = datetime.date.today().strftime("%Y%m%d")
        params = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "02",   # 매수
            "INQR_DVSN": "00",
            "PDNO": stock_code,
            "CCLD_DVSN": "01",          # 체결만
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = f"{self._config.base_url}{_EXECUTION_ENDPOINT}"
        for attempt in range(retries):
            time.sleep(delay)
            resp = requests.get(url, headers=self._headers(tr_id, token), params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("output1", [])
            if items:
                item = items[0]
                return {
                    "exec_price": item.get("avg_prvs") or item.get("ccld_avg_pric", "0"),
                    "exec_qty":   item.get("tot_ccld_qty", "0"),
                    "exec_time":  item.get("ord_tmd", ""),
                }
            logger.debug(f"체결 대기 중... ({attempt + 1}/{retries})")
        return None

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
        # KIS VTS(모의투자)는 해외주식 주문을 미지원 — mock 모드에서는 로컬 시뮬레이션
        if self._config.mode == "mock":
            logger.info(f"[mock] 해외 {side} 시뮬레이션 | {exchange}:{symbol} {quantity}주 @ {price}")
            return {"rt_cd": "0", "msg1": "모의 주문 완료", "output": {"ODNO": "MOCK0000001"}}

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

    @staticmethod
    def _round_to_tick(price: int) -> int:
        """KRX 호가단위에 맞게 내림"""
        if price < 1_000:
            tick = 1
        elif price < 5_000:
            tick = 5
        elif price < 10_000:
            tick = 10
        elif price < 50_000:
            tick = 50
        elif price < 100_000:
            tick = 100
        elif price < 500_000:
            tick = 500
        else:
            tick = 1_000
        return (price // tick) * tick

    def _place_order(self, side: str, stock_code: str, quantity: int, tr_id: str, token: str, limit_price: int = None) -> dict:
        if limit_price:
            limit_price = self._round_to_tick(int(limit_price))
            ord_dvsn = "00"             # 지정가
            ord_unpr = str(limit_price)
        else:
            ord_dvsn = "01"             # 시장가
            ord_unpr = "0"
        body = {
            "CANO": self._config.cano,
            "ACNT_PRDT_CD": self._config.acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": ord_unpr,
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

        order_label = f"지정가 {limit_price:,}원" if limit_price else "시장가"
        logger.info(f"[{self._config.mode}] {side} 완료 | {stock_code} {quantity}주 ({order_label})")
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
