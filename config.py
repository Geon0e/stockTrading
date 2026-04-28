from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# TRADING_MODE is set by start.sh at launch — preserve it so real/mock bots
# are not overwritten by the TRADING_MODE=mock default in .env.
_trading_mode_before = os.environ.get("TRADING_MODE")
load_dotenv(override=True)
if _trading_mode_before is not None:
    os.environ["TRADING_MODE"] = _trading_mode_before

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
    take_profit_rate: float       # 익절 트리거 수익률 기준 (%)
    take_profit_limit_pct: float  # 익절 지정가: 매입가 × (1 + %) — 0이면 take_profit_rate와 동일
    stop_loss_pct: float          # 손절 트리거 기준 (%, 0 = 비활성화)
    stop_loss_limit_pct: float    # 손절 지정가: 매입가 × (1 - %) — 0이면 stop_loss_pct와 동일
    mock_budget: int        # 모의 운용 예산 KRW (포지션당 예산 = mock_budget / max_positions)
    real_budget: int        # 실전 운용 예산 KRW (포지션당 예산 = real_budget / max_positions)
    real_usd_budget: float  # 실전 해외주식 예산 USD
    scan_interval_minutes: int  # 스캔 주기(분). 0 = 고정시간(국내 09:05 / 나스닥 23:35)
    watchlist: tuple        # 커스텀 스캔 종목 리스트. 비어있으면 기본 스캔(전종목/거래량 상위)
    exclude_list: tuple     # 거래 제외 종목 코드 리스트 (모드 무관 적용)
    order_type: str         # "market" | "limit"
    limit_order_pct: float  # 지정가 주문 시 포착 가격 대비 허용 % (예: 1.0 → 신호가 × 1.01)
    monitor_interval_seconds: int  # 손절/익절 모니터링 주기(초). 기본 60초


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
        scan_all_stocks=os.getenv(f"SCAN_ALL_STOCKS_{mode.upper()}", os.getenv("SCAN_ALL_STOCKS", "false")).lower() == "true",
        scan_nasdaq=os.getenv(f"SCAN_NASDAQ_{mode.upper()}", os.getenv("SCAN_NASDAQ", "false")).lower() == "true",
        us_scan_mode=os.getenv("US_SCAN_MODE", "nasdaq100"),
        max_positions=int(os.getenv(f"MAX_POSITIONS_{mode.upper()}", os.getenv("MAX_POSITIONS", "5"))),
        take_profit_rate=float(os.getenv(f"TAKE_PROFIT_RATE_{mode.upper()}", os.getenv("TAKE_PROFIT_RATE", "0"))),
        take_profit_limit_pct=float(os.getenv(f"TAKE_PROFIT_LIMIT_PCT_{mode.upper()}", os.getenv("TAKE_PROFIT_LIMIT_PCT", "0"))),
        stop_loss_pct=float(os.getenv(f"STOP_LOSS_PCT_{mode.upper()}", os.getenv("STOP_LOSS_PCT", "0"))),
        stop_loss_limit_pct=float(os.getenv(f"STOP_LOSS_LIMIT_PCT_{mode.upper()}", os.getenv("STOP_LOSS_LIMIT_PCT", "0"))),
        mock_budget=int(os.getenv("MOCK_BUDGET", "500000")),
        real_budget=int(os.getenv("REAL_BUDGET", "500000")),
        real_usd_budget=float(os.getenv("REAL_USD_BUDGET", "750.0")),
        scan_interval_minutes=int(os.getenv(f"SCAN_INTERVAL_MINUTES_{mode.upper()}", "0")),
        order_quantity=int(os.getenv(f"ORDER_QUANTITY_{mode.upper()}", os.getenv("ORDER_QUANTITY", "1"))),
        watchlist=tuple(c.strip() for c in os.getenv(f"WATCHLIST_{mode.upper()}", os.getenv("WATCHLIST", "")).split(",") if c.strip()),
        exclude_list=tuple(c.strip() for c in os.getenv(f"EXCLUDE_LIST_{mode.upper()}", os.getenv("EXCLUDE_LIST", "")).split(",") if c.strip()),
        order_type=os.getenv(f"ORDER_TYPE_{mode.upper()}", "market"),
        limit_order_pct=float(os.getenv(f"LIMIT_ORDER_PCT_{mode.upper()}", "1.0")),
        monitor_interval_seconds=int(os.getenv(f"MONITOR_INTERVAL_SECONDS_{mode.upper()}", os.getenv("MONITOR_INTERVAL_SECONDS", "60"))),
        **env,
    )
