import datetime
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
        """일별 종가를 오래된 순으로 반환 (이동평균 계산용).

        KIS inquire-daily-price API는 호출당 최대 30행 반환 → count > 30이면
        end_date를 앞당겨 가며 페이지네이션으로 충분한 데이터를 수집한다.
        """
        _MAX_PER_CALL = 30  # KIS API 호출당 최대 반환 행수
        _SAFETY_LIMIT = datetime.date.today() - datetime.timedelta(days=365 * 2)

        # 최신순(내림차순)으로 누적
        accumulated: list = []  # [(date_str, Decimal), ...]
        end_date = datetime.date.today()
        url = f"{self._config.base_url}{_ENDPOINT}"

        is_first_call = True
        while len(accumulated) < count:
            # 첫 호출: count*2 일수로 원래 동작 유지 (불필요하게 넓은 범위 방지)
            # 페이지네이션: API 최대 반환 수 기준 고정 window
            window = count * 2 if is_first_call else _MAX_PER_CALL * 2
            start_date = end_date - datetime.timedelta(days=window)
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
                "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
            }
            resp = requests.get(url, headers=self._headers(token), params=params, timeout=10)
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                if is_first_call:
                    raise  # 첫 호출 실패는 그대로 전파
                break  # 페이지네이션 중 실패 = 상장 이전 날짜 소급 → 수집 종료
            is_first_call = False
            data = resp.json()

            if data.get("rt_cd") != "0":
                raise RuntimeError(f"가격 조회 실패 [{stock_code}]: {data.get('msg1')}")

            rows = data.get("output", [])
            batch = [
                (row["stck_bsop_date"], Decimal(row["stck_clpr"]))
                for row in rows
                if row.get("stck_clpr") and row.get("stck_bsop_date")
            ]
            if not batch:
                break

            accumulated.extend(batch)

            # 다음 페이지: 이번 배치에서 가장 오래된 날짜 전날부터
            oldest_str = batch[-1][0]  # YYYYMMDD, 내림차순이므로 마지막이 가장 오래됨
            oldest_date = datetime.datetime.strptime(oldest_str, "%Y%m%d").date()
            end_date = oldest_date - datetime.timedelta(days=1)

            if end_date < _SAFETY_LIMIT:
                break

        prices = [price for _, price in accumulated[:count]]

        if len(prices) < count:
            raise RuntimeError(f"데이터 부족: {count}개 필요, {len(prices)}개 조회됨")

        prices.reverse()  # 최신순 → 오래된 순
        return prices

    def fetch_ohlcv(self, stock_code: str, count: int, token: str) -> List[dict]:
        """일별 OHLCV를 오래된 순으로 반환. keys: open, high, low, close, volume

        KIS API 30행 제한 → count > 30이면 페이지네이션으로 수집.
        """
        _MAX_PER_CALL = 30
        _SAFETY_LIMIT = datetime.date.today() - datetime.timedelta(days=365 * 2)

        accumulated: list = []  # [(date_str, dict), ...] 최신순
        end_date = datetime.date.today()
        url = f"{self._config.base_url}{_ENDPOINT}"

        is_first_call = True
        while len(accumulated) < count:
            window = count * 2 if is_first_call else _MAX_PER_CALL * 2
            start_date = end_date - datetime.timedelta(days=window)
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
                "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
            }
            resp = requests.get(url, headers=self._headers(token), params=params, timeout=10)
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                if is_first_call:
                    raise
                break  # 페이지네이션 중 실패 = 상장 이전 날짜 소급 → 수집 종료
            is_first_call = False
            data = resp.json()

            if data.get("rt_cd") != "0":
                raise RuntimeError(f"OHLCV 조회 실패 [{stock_code}]: {data.get('msg1')}")

            rows = data.get("output", [])
            batch = []
            for row in rows:
                try:
                    c = int(row.get("stck_clpr") or 0)
                    d = row.get("stck_bsop_date", "")
                    if c <= 0 or not d:
                        continue
                    batch.append((d, {
                        "open":   int(row.get("stck_oprc") or 0),
                        "high":   int(row.get("stck_hgpr") or 0),
                        "low":    int(row.get("stck_lwpr") or 0),
                        "close":  c,
                        "volume": int(row.get("acml_vol") or 0),
                    }))
                except (ValueError, TypeError):
                    pass

            if not batch:
                break

            accumulated.extend(batch)

            oldest_str = batch[-1][0]
            oldest_date = datetime.datetime.strptime(oldest_str, "%Y%m%d").date()
            end_date = oldest_date - datetime.timedelta(days=1)

            if end_date < _SAFETY_LIMIT:
                break

        result = [bar for _, bar in accumulated[:count]]

        if len(result) < count:
            raise RuntimeError(f"OHLCV 데이터 부족: {count}개 필요, {len(result)}개 조회됨")

        result.reverse()  # 최신순 → 오래된 순
        return result

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
