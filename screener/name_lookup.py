"""종목명 조회 유틸리티 (KR/US 공통)"""
import json
from pathlib import Path

_KR_NAME_CACHE: dict | None = None
_NAME_CACHE_FILE = Path(".token_cache/stock_names.json")

_US_NAMES = {
    "AAPL":"Apple","MSFT":"Microsoft","NVDA":"NVIDIA","AMZN":"Amazon",
    "META":"Meta","TSLA":"Tesla","GOOGL":"Alphabet A","GOOG":"Alphabet C",
    "AVGO":"Broadcom","COST":"Costco","NFLX":"Netflix","TMUS":"T-Mobile",
    "AMD":"AMD","PEP":"PepsiCo","QCOM":"Qualcomm","ADBE":"Adobe",
    "AMAT":"Applied Materials","TXN":"Texas Instruments","INTU":"Intuit",
    "ISRG":"Intuitive Surgical","CMCSA":"Comcast","BKNG":"Booking",
    "VRTX":"Vertex Pharma","MU":"Micron","REGN":"Regeneron","LRCX":"Lam Research",
    "PANW":"Palo Alto","KLAC":"KLA Corp","ABNB":"Airbnb","SNPS":"Synopsys",
    "MELI":"MercadoLibre","CDNS":"Cadence","CRWD":"CrowdStrike","ASML":"ASML",
    "ADP":"ADP","CSX":"CSX","ORLY":"O'Reilly","FTNT":"Fortinet",
    "MRVL":"Marvell","NXPI":"NXP Semi","PCAR":"PACCAR","WDAY":"Workday",
    "DASH":"DoorDash","ADSK":"Autodesk","DXCM":"Dexcom","ROST":"Ross Stores",
    "PAYX":"Paychex","CTAS":"Cintas","GILD":"Gilead","SBUX":"Starbucks",
    "AMGN":"Amgen","HON":"Honeywell","INTC":"Intel","MDLZ":"Mondelez",
    "LULU":"Lululemon","MAR":"Marriott","PYPL":"PayPal","EBAY":"eBay",
}


def _is_overseas(code: str) -> bool:
    return not code.isdigit()


def _load_kr_names() -> dict:
    global _KR_NAME_CACHE
    if _KR_NAME_CACHE is not None:
        return _KR_NAME_CACHE

    if _NAME_CACHE_FILE.exists():
        try:
            _KR_NAME_CACHE = json.loads(_NAME_CACHE_FILE.read_text(encoding="utf-8"))
            return _KR_NAME_CACHE
        except Exception:
            pass

    try:
        import FinanceDataReader as fdr
        import pandas as pd
        kospi  = fdr.StockListing("KOSPI")[["Code","Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Code","Name"]]
        combined = pd.concat([kospi, kosdaq]).drop_duplicates("Code")
        _KR_NAME_CACHE = dict(zip(combined["Code"], combined["Name"]))
        _NAME_CACHE_FILE.parent.mkdir(exist_ok=True)
        _NAME_CACHE_FILE.write_text(
            json.dumps(_KR_NAME_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        _KR_NAME_CACHE = {}

    return _KR_NAME_CACHE


def get_stock_name(code: str) -> str:
    """종목코드로 종목명 반환. 못 찾으면 빈 문자열."""
    if _is_overseas(code):
        return _US_NAMES.get(code.upper(), "")
    return _load_kr_names().get(code, "")
