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
    scan_interval_minutes: int  # 스캔 주기(분). 0 = 고정시간(국내 09:05 / 나스닥 23:35) — 시간대별 설정이 없을 때 사용
    scan_interval_early: int   # 장 초반 09:00~10:00 스캔 주기(분). 0 = 비활성
    scan_interval_mid: int     # 장 중반 10:00~14:30 스캔 주기(분). 0 = 비활성
    scan_interval_late: int    # 장 후반 14:30~15:20 스캔 주기(분). 0 = 비활성
    watchlist: tuple        # 커스텀 스캔 종목 리스트. 비어있으면 기본 스캔(전종목/거래량 상위)
    exclude_list: tuple     # 거래 제외 종목 코드 리스트 (모드 무관 적용)
    buy_source: str         # "strategy" (설정 기반 전략) | "grid" (그리드 스크리닝 추천)
    order_type: str         # "market" | "limit"
    limit_order_pct: float  # 지정가 주문 시 포착 가격 대비 허용 % (예: 1.0 → 신호가 × 1.01)
    grid_tp_steps: int      # 그리드 익절: 매수 그리드선 기준 N칸 위 도달 시 매도 (buy_source=grid 전용)
    grid_sl_pct: float      # 그리드 손절: 매수 지지선 대비 -X% 이탈 시 매도 (0 = 손절 비활성)
    monitor_interval_seconds: int  # 손절/익절 모니터링 주기(초). 기본 60초
    morning_sell_profit_pct: float  # 장초 전일 보유 종목 익절 기준 (%, 0 = 비활성화)
    morning_stoploss_enabled: bool  # 10:30 전일 손실 종목 손절 활성화 여부
    matagi_drop_pct: float          # 물타기 1차 하락 기준 (%, 1차=×1 / 2차=×2.5 / 3차=×4)
    matagi_max_count: int           # 종목당 최대 물타기 횟수 (0 = 비활성화)
    matagi_interval_minutes: int    # 독립 물타기 모니터 주기(분). 0 = 비활성화 (기본 5)
    matagi_ma_period: int           # 물타기 MA 기간 (기본 20)
    matagi_use_ma_filter: bool      # 물타기 MA 추세 필터 사용 여부 (기본 true)
    matagi_vol_lookback: int        # 물타기 거래량 비교 기간 (기본 5)
    matagi_use_rebound_filter: bool # 물타기 반등 신호(양봉/거래량) 필터 사용 여부 (기본 true)
    matagi_stage_multipliers: tuple # 물타기 단계별 하락 배율 (기본 1.0,2.5,4.0)
    bultagi_profit_pct: float       # 불타기 1차 수익 기준 (%, 1차=×1 / 2차=×2.0 / 3차=×3.0, 0 = 비활성화)
    bultagi_max_count: int          # 종목당 최대 불타기 횟수 (0 = 비활성화)
    bultagi_interval_minutes: int   # 독립 불타기 모니터 주기(분). 0 = 비활성화 (기본 5)
    bultagi_ma_period: int          # 불타기 MA 기간 (기본 20)
    bultagi_use_ma_filter: bool     # 불타기 MA 추세 필터 사용 여부 (기본 true)
    bultagi_stage_multipliers: tuple # 불타기 단계별 수익 배율 (기본 1.0,2.0,3.0)
    market_regime_enabled: bool     # 시장 국면 필터 활성화 (하락장 매수 차단)
    market_regime_index_code: str   # 지수 프록시 종목 코드 (기본: 069500 = KODEX 200)
    market_regime_ma_period: int    # 지수 MA 기간 (0 = MA 체크 비활성, 기본 60)
    market_regime_rsi_period: int   # 지수 RSI 기간 (0 = RSI 체크 비활성, 기본 14)
    market_regime_rsi_min: float    # RSI 최솟값 — 이 값 미만이면 매수 차단 (기본 40, 0 = 비활성)


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
        scan_interval_early=int(os.getenv(f"SCAN_INTERVAL_EARLY_{mode.upper()}", os.getenv("SCAN_INTERVAL_EARLY", "5"))),
        scan_interval_mid=int(os.getenv(f"SCAN_INTERVAL_MID_{mode.upper()}", os.getenv("SCAN_INTERVAL_MID", "20"))),
        scan_interval_late=int(os.getenv(f"SCAN_INTERVAL_LATE_{mode.upper()}", os.getenv("SCAN_INTERVAL_LATE", "10"))),
        order_quantity=int(os.getenv(f"ORDER_QUANTITY_{mode.upper()}", os.getenv("ORDER_QUANTITY", "0"))),
        watchlist=tuple(c.strip() for c in os.getenv(f"WATCHLIST_{mode.upper()}", os.getenv("WATCHLIST", "")).split(",") if c.strip()),
        exclude_list=tuple(c.strip() for c in os.getenv(f"EXCLUDE_LIST_{mode.upper()}", os.getenv("EXCLUDE_LIST", "")).split(",") if c.strip()),
        buy_source=os.getenv(f"BUY_SOURCE_{mode.upper()}", os.getenv("BUY_SOURCE", "strategy")).strip().lower(),
        order_type=os.getenv(f"ORDER_TYPE_{mode.upper()}", "market"),
        limit_order_pct=float(os.getenv(f"LIMIT_ORDER_PCT_{mode.upper()}", "1.0")),
        grid_tp_steps=int(os.getenv(f"GRID_TP_STEPS_{mode.upper()}", os.getenv("GRID_TP_STEPS", "2"))),
        grid_sl_pct=float(os.getenv(f"GRID_SL_PCT_{mode.upper()}", os.getenv("GRID_SL_PCT", "2.0"))),
        monitor_interval_seconds=int(os.getenv(f"MONITOR_INTERVAL_SECONDS_{mode.upper()}", os.getenv("MONITOR_INTERVAL_SECONDS", "1" if mode == "real" else "60"))),
        morning_sell_profit_pct=float(os.getenv(f"MORNING_SELL_PROFIT_PCT_{mode.upper()}", os.getenv("MORNING_SELL_PROFIT_PCT", "0"))),
        morning_stoploss_enabled=os.getenv(f"MORNING_STOPLOSS_ENABLED_{mode.upper()}", os.getenv("MORNING_STOPLOSS_ENABLED", "false")).lower() == "true",
        matagi_drop_pct=float(os.getenv(f"MATAGI_DROP_PCT_{mode.upper()}", os.getenv("MATAGI_DROP_PCT", "2.0"))),
        matagi_max_count=int(os.getenv(f"MATAGI_MAX_COUNT_{mode.upper()}", os.getenv("MATAGI_MAX_COUNT", "2"))),
        matagi_interval_minutes=int(os.getenv(f"MATAGI_INTERVAL_MINUTES_{mode.upper()}", os.getenv("MATAGI_INTERVAL_MINUTES", "5"))),
        matagi_ma_period=int(os.getenv(f"MATAGI_MA_PERIOD_{mode.upper()}", os.getenv("MATAGI_MA_PERIOD", "20"))),
        matagi_use_ma_filter=os.getenv(f"MATAGI_USE_MA_FILTER_{mode.upper()}", os.getenv("MATAGI_USE_MA_FILTER", "true")).lower() == "true",
        matagi_vol_lookback=int(os.getenv(f"MATAGI_VOL_LOOKBACK_{mode.upper()}", os.getenv("MATAGI_VOL_LOOKBACK", "5"))),
        matagi_use_rebound_filter=os.getenv(f"MATAGI_USE_REBOUND_FILTER_{mode.upper()}", os.getenv("MATAGI_USE_REBOUND_FILTER", "true")).lower() == "true",
        matagi_stage_multipliers=tuple(
            float(x) for x in os.getenv(f"MATAGI_STAGE_MULTIPLIERS_{mode.upper()}", os.getenv("MATAGI_STAGE_MULTIPLIERS", "1.0,2.5,4.0")).split(",")
        ),
        bultagi_profit_pct=float(os.getenv(f"BULTAGI_PROFIT_PCT_{mode.upper()}", os.getenv("BULTAGI_PROFIT_PCT", "0"))),
        bultagi_max_count=int(os.getenv(f"BULTAGI_MAX_COUNT_{mode.upper()}", os.getenv("BULTAGI_MAX_COUNT", "2"))),
        bultagi_interval_minutes=int(os.getenv(f"BULTAGI_INTERVAL_MINUTES_{mode.upper()}", os.getenv("BULTAGI_INTERVAL_MINUTES", "5"))),
        bultagi_ma_period=int(os.getenv(f"BULTAGI_MA_PERIOD_{mode.upper()}", os.getenv("BULTAGI_MA_PERIOD", "20"))),
        bultagi_use_ma_filter=os.getenv(f"BULTAGI_USE_MA_FILTER_{mode.upper()}", os.getenv("BULTAGI_USE_MA_FILTER", "true")).lower() == "true",
        bultagi_stage_multipliers=tuple(
            float(x) for x in os.getenv(f"BULTAGI_STAGE_MULTIPLIERS_{mode.upper()}", os.getenv("BULTAGI_STAGE_MULTIPLIERS", "1.0,2.0,3.0")).split(",")
        ),
        market_regime_enabled=os.getenv(f"MARKET_REGIME_ENABLED_{mode.upper()}", os.getenv("MARKET_REGIME_ENABLED", "false" if mode == "mock" else "true")).lower() == "true",
        market_regime_index_code=os.getenv("MARKET_REGIME_INDEX_CODE", "069500"),
        market_regime_ma_period=int(os.getenv("MARKET_REGIME_MA_PERIOD", "60")),
        market_regime_rsi_period=int(os.getenv("MARKET_REGIME_RSI_PERIOD", "14")),
        market_regime_rsi_min=float(os.getenv("MARKET_REGIME_RSI_MIN", "40")),
        **env,
    )
