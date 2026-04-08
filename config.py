from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

_MOCK = {
    "base_url": "https://openapivts.koreainvestment.com:29443",
    "tr_buy": "VTTC0802U",
    "tr_sell": "VTTC0801U",
    "tr_balance": "VTTC8434R",
}

_REAL = {
    "base_url": "https://openapi.koreainvestment.com:9443",
    "tr_buy": "TTTC0802U",
    "tr_sell": "TTTC0801U",
    "tr_balance": "TTTC8434R",
}


@dataclass(frozen=True)
class Config:
    mode: str            # "mock" | "real"
    base_url: str
    app_key: str
    app_secret: str
    account_no: str      # "XXXXXXXX-XX" 형식
    cano: str            # 계좌번호 앞 8자리
    acnt_prdt_cd: str    # 계좌번호 뒤 2자리
    tr_buy: str
    tr_sell: str
    tr_balance: str
    target_stock: str
    order_quantity: int
    check_interval_minutes: int
    ma_short_period: int
    ma_long_period: int


def load_config() -> Config:
    mode = os.getenv("TRADING_MODE", "mock")
    if mode not in ("mock", "real"):
        raise ValueError(f"TRADING_MODE은 'mock' 또는 'real'이어야 합니다. 현재값: {mode}")

    prefix = "MOCK" if mode == "mock" else "REAL"
    env = _MOCK if mode == "mock" else _REAL

    app_key = os.getenv(f"{prefix}_APP_KEY", "")
    app_secret = os.getenv(f"{prefix}_APP_SECRET", "")
    account_no = os.getenv(f"{prefix}_ACCOUNT_NO", "")

    if not all([app_key, app_secret, account_no]):
        raise ValueError(
            f"{prefix}_APP_KEY, {prefix}_APP_SECRET, {prefix}_ACCOUNT_NO 환경변수를 설정하세요"
        )

    parts = account_no.split("-")
    if len(parts) != 2:
        raise ValueError(f"계좌번호 형식 오류 (예: 12345678-01): {account_no}")

    return Config(
        mode=mode,
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        cano=parts[0],
        acnt_prdt_cd=parts[1],
        target_stock=os.getenv("TARGET_STOCK_CODE", "005930"),
        order_quantity=int(os.getenv("ORDER_QUANTITY", "1")),
        check_interval_minutes=int(os.getenv("CHECK_INTERVAL_MINUTES", "60")),
        ma_short_period=int(os.getenv("MA_SHORT_PERIOD", "5")),
        ma_long_period=int(os.getenv("MA_LONG_PERIOD", "20")),
        **env,
    )
