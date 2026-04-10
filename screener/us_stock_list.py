"""미국 주식 종목 목록 (FinanceDataReader 기반, 24시간 캐싱)"""
import json
import time
import logging
import FinanceDataReader as fdr
from pathlib import Path
from screener.nasdaq100 import NASDAQ_100

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(".token_cache/us_stock_list.json")
_CACHE_TTL  = 86400  # 24시간

# KIS 거래소 코드 매핑
_EXCHANGE_MAP = {
    "NAS": "NAS",   # NASDAQ
    "NYQ": "NYS",   # NYSE
    "NGM": "NAS",   # NASDAQ Global Market
    "NCM": "NAS",   # NASDAQ Capital Market
    "NYS": "NYS",
    "ASE": "AMS",   # AMEX
}


def _load_cache() -> dict | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _save_cache(data: dict) -> None:
    _CACHE_FILE.parent.mkdir(exist_ok=True)
    data["ts"] = time.time()
    _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fetch_nasdaq100() -> list[dict]:
    """나스닥100 — [{symbol, exchange}]"""
    return [{"symbol": s, "exchange": "NAS"} for s in NASDAQ_100]


def fetch_sp500() -> list[dict]:
    """S&P500 (~503개) — [{symbol, exchange}]"""
    cached = _load_cache()
    if cached and "sp500" in cached:
        logger.info(f"S&P500 캐시 사용: {len(cached['sp500'])}개")
        return cached["sp500"]

    logger.info("S&P500 종목 목록 조회 중...")
    df = fdr.StockListing("S&P500")
    stocks = []
    for _, row in df.iterrows():
        symbol   = str(row.get("Symbol", "") or row.get("Code", "")).strip()
        exchange = _EXCHANGE_MAP.get(str(row.get("Exchange", "")), "NAS")
        if symbol:
            stocks.append({"symbol": symbol, "exchange": exchange})

    cached_data = _load_cache() or {}
    cached_data["sp500"] = stocks
    _save_cache(cached_data)
    logger.info(f"S&P500 조회 완료: {len(stocks)}개")
    return stocks


def fetch_all_us() -> list[dict]:
    """NYSE + NASDAQ + AMEX 전종목 (~6,900개) — [{symbol, exchange}]"""
    cached = _load_cache()
    if cached and "all_us" in cached:
        logger.info(f"미국 전종목 캐시 사용: {len(cached['all_us'])}개")
        return cached["all_us"]

    logger.info("미국 전종목 조회 중 (NYSE + NASDAQ + AMEX)...")
    stocks = []
    seen   = set()

    for market, excd in [("NASDAQ", "NAS"), ("NYSE", "NYS"), ("AMEX", "AMS")]:
        try:
            df = fdr.StockListing(market)
            col = "Symbol" if "Symbol" in df.columns else "Code"
            for symbol in df[col].dropna():
                symbol = str(symbol).strip()
                if symbol and symbol not in seen:
                    stocks.append({"symbol": symbol, "exchange": excd})
                    seen.add(symbol)
            logger.info(f"{market}: {len(df)}개 추가")
        except Exception as e:
            logger.warning(f"{market} 조회 실패: {e}")

    cached_data = _load_cache() or {}
    cached_data["all_us"] = stocks
    _save_cache(cached_data)
    logger.info(f"미국 전종목 조회 완료: {len(stocks)}개")
    return stocks


def fetch_us_stocks(mode: str) -> list[dict]:
    """mode: nasdaq100 | sp500 | all"""
    if mode == "sp500":
        return fetch_sp500()
    if mode == "all":
        return fetch_all_us()
    return fetch_nasdaq100()  # 기본값
