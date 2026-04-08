import json
import time
import logging
import FinanceDataReader as fdr
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(".token_cache/stock_list.json")
_CACHE_TTL = 86400  # 24시간


def fetch_all_stock_codes() -> list:
    """KOSPI + KOSDAQ 전종목 코드 반환 (24시간 캐싱)"""
    if _CACHE_FILE.exists():
        cached = json.loads(_CACHE_FILE.read_text())
        if time.time() - cached.get("ts", 0) < _CACHE_TTL:
            logger.info(f"종목 목록 캐시 사용: {len(cached['codes'])}개")
            return cached["codes"]

    logger.info("KOSPI + KOSDAQ 전종목 목록 조회 중...")
    kospi = fdr.StockListing("KOSPI")["Code"].tolist()
    kosdaq = fdr.StockListing("KOSDAQ")["Code"].tolist()
    codes = list(set(kospi + kosdaq))

    logger.info(f"KOSPI: {len(kospi)}개 | KOSDAQ: {len(kosdaq)}개 | 합계: {len(codes)}개")

    _CACHE_FILE.parent.mkdir(exist_ok=True)
    _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "codes": codes}))
    return codes
