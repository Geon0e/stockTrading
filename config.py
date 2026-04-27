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
    "tr_overseas_buy": "VTTT1002U",
    "tr_overseas_sell": "VTTT1001U",
    "tr_overseas_balance": "VTTS3012R",
}

_REAL = {
    "base_url": "https://openapi.koreainvestment.com:9443",
    "tr_buy": "TTTC0802U",
    "tr_sell": "TTTC0801U",
    "tr_balance": "TTTC8434R",
    "tr_overseas_buy": "TTTT1002U",
    "tr_overseas_sell": "TTTT1001U",
    "tr_overseas_balance": "TTTS3012R",
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
    tr_overseas_buy: str
    tr_overseas_sell: str
    tr_overseas_balance: str
    scan_nasdaq: bool
    us_scan_mode: str    # nasdaq100 | sp500 | all
    order_quantity: int
    ma_short_period: int
    ma_long_period: int
    scan_all_stocks: bool   # True: 전종목, False: 거래량 상위만
    max_positions: int      # 최대 동시 보유 종목 수
    take_profit_rate: float # 익절 수익률 기준 (%)
    stop_loss_pct: float    # 손절 기준 (예: 5.0 → 매입가 대비 -5% 시 매도, 0 = 비활성화)
    real_budget: int        # 실전 운용 예산 KRW (포지션당 예산 = real_budget / max_positions)
    real_usd_budget: float  # 실전 해외주식 예산 USD
    scan_interval_minutes: int  # 스캔 주기(분). 0 = 고정시간(국내 09:05 / 나스닥 23:35)


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
        ma_short_period=int(os.getenv("MA_SHORT_PERIOD", "5")),
        ma_long_period=int(os.getenv("MA_LONG_PERIOD", "20")),
        scan_all_stocks=os.getenv("SCAN_ALL_STOCKS", "false").lower() == "true",
        scan_nasdaq=os.getenv("SCAN_NASDAQ", "false").lower() == "true",
        us_scan_mode=os.getenv("US_SCAN_MODE", "nasdaq100"),
        order_quantity=int(os.getenv("ORDER_QUANTITY", "1")),
        max_positions=int(os.getenv("MAX_POSITIONS", "5")),
        take_profit_rate=float(os.getenv("TAKE_PROFIT_RATE", "5.0")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0")),
        real_budget=int(os.getenv("REAL_BUDGET", "500000")),
        real_usd_budget=float(os.getenv("REAL_USD_BUDGET", "750.0")),
        scan_interval_minutes=int(os.getenv(f"SCAN_INTERVAL_MINUTES_{mode.upper()}", os.getenv("SCAN_INTERVAL_MINUTES", "0"))),
        **env,
    )
