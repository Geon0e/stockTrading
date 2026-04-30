# StockTrading Bot

KIS(한국투자증권) API를 사용하는 국내·해외 주식 자동매매 봇.  
모의(Mock) / 실전(Real) 두 모드를 지원하며, Flask 대시보드와 텔레그램 알림을 제공한다.

---

## 목차

1. [전체 구조](#전체-구조)
2. [실행 흐름](#실행-흐름)
3. [파일별 설명](#파일별-설명)
   - [진입점 · 오케스트레이터](#진입점--오케스트레이터)
   - [설정](#설정)
   - [매매 실행 레이어](#매매-실행-레이어)
   - [주문 · 시세 레이어](#주문--시세-레이어)
   - [스크리너 · 전략 레이어](#스크리너--전략-레이어)
   - [알림 · 감사 레이어](#알림--감사-레이어)
   - [대시보드](#대시보드)
4. [데이터 파일 · 로그](#데이터-파일--로그)
5. [핵심 설계 원칙](#핵심-설계-원칙)
6. [환경 변수 레퍼런스](#환경-변수-레퍼런스)

---

## 전체 구조

```
stockTrading/
│
├── main.py                     # 봇 진입점, 스케줄러, 모니터링 스레드
├── config.py                   # 환경변수 → Config 데이터클래스
├── dashboard.py                # Flask 웹 대시보드 (설정·모니터링·제어)
│
├── trader/
│   ├── real_domestic.py        # 실전 국내 매매 사이클
│   ├── real_nasdaq.py          # 실전 해외(나스닥) 매매 사이클
│   ├── matagi.py               # 물타기(손실 추가매수) 조건 검증
│   ├── bultagi.py              # 불타기(수익 추가매수) 조건 검증
│   └── utils.py                # 일일 예산 추적·공유 유틸
│
├── order/
│   └── order_client.py         # KIS API 주문·잔고 조회 래퍼
│
├── market/
│   ├── price_client.py         # KIS API 시세 조회 (OHLCV·현재가)
│   └── kis_websocket.py        # 실시간 WebSocket 시세 수신
│
├── screener/
│   ├── stock_screener.py       # 종목 스캔 (국내·해외, 병렬처리)
│   ├── market_regime.py        # 시장 추세 필터 (지수 MA·RSI)
│   ├── name_lookup.py          # 종목코드 ↔ 이름 변환
│   ├── stock_list.py           # 국내 전종목 코드 캐시
│   └── us_stock_list.py        # 미국 주식 유니버스 캐시
│
├── strategy/
│   ├── base_strategy.py        # 전략 추상 기반 클래스
│   ├── ma_cross_strategy.py    # 골든/데드크로스 전략 (기본)
│   ├── configurable_strategy.py# STRATEGY.md 기반 동적 전략
│   ├── strategy_loader.py      # STRATEGY.md 파서
│   └── indicators/
│       ├── moving_average.py   # SMA, EMA 계산
│       ├── rsi.py              # RSI 계산
│       └── bollinger.py        # 볼린저밴드 계산
│
├── auth/
│   └── token_manager.py        # KIS OAuth 토큰 발급·갱신·캐싱
│
├── notifications/
│   └── telegram_notifier.py    # 텔레그램 알림·매수 확인 요청
│
├── audit/
│   └── trade_logger.py         # 거래 기록 (append-only JSONL)
│
├── logs/                       # 런타임 생성 (gitignore)
├── .token_cache/               # 토큰·종목 캐시 (gitignore)
├── STRATEGY.md                 # 기본 전략 설정 (대시보드에서 편집)
└── .env                        # 환경변수 (gitignore)
```

---

## 실행 흐름

```
봇 시작 (main.py)
  │
  ├─ 1. .env 로드 → Config 생성
  ├─ 2. TokenManager: 캐시된 토큰 복구 또는 신규 발급
  ├─ 3. PriceClient · OrderClient · StockScreener 초기화
  │
  ├─ [실전 모드만]
  │   ├─ KisRealtimePrice WebSocket 스레드 시작
  │   ├─ KIS API로 당일 체결 내역 조회 → 일일 예산 초기화
  │   └─ ws_prices_real.json 파일 라이터 스레드 시작
  │
  ├─ 4. holdings_cache 로드 (holdings_{mode}.json)
  ├─ 5. matagi_count 복구 (오늘 trades_{mode}.jsonl에서 집계)
  │
  ├─ 6. 스케줄 등록
  │   ├─ 09:05 매일: 국내 매매 사이클
  │   ├─ 23:35 매일: 나스닥 매매 사이클
  │   └─ 10:30 매일: 장초 손절 사이클
  │
  ├─ 7. 모니터링 스레드 시작 (각각 독립 주기)
  │   ├─ 손절 모니터 (run_stop_loss_check)
  │   ├─ 익절 모니터 (run_take_profit_cycle)
  │   ├─ 물타기 모니터 (run_matagi_cycle)
  │   └─ 불타기 모니터 (run_bultagi_cycle)
  │
  └─ 8. 메인 루프: schedule.run_pending() + sleep(1)
```

---

## 파일별 설명

---

### 진입점 · 오케스트레이터

#### `main.py`

봇 전체를 조율하는 진입점. 스케줄러 등록, 모니터링 스레드 관리, 시장 개장 여부 판별을 담당한다.

**핵심 함수 설명**

```
is_market_open()
  └─ KST 기준 평일 09:00~15:30 여부 확인
     → 모든 국내 매매·모니터링 사이클의 선행 조건

is_nasdaq_open()
  └─ KST 기준 평일 23:30~익일 06:00 여부 확인
     → 나스닥 사이클의 선행 조건

run_take_profit_cycle(ctx)
  └─ 독립 익절 모니터
     holdings_cache 순회 → WebSocket 우선·REST 보조로 현재가 조회
     수익률 >= take_profit_rate 이면 지정가 매도
     제외 종목(exclude_list) 건너뜀

run_matagi_cycle(ctx)
  └─ 독립 물타기 모니터
     손실 보유 종목 대상으로 matagi.py 조건 검증
     통과 시 추가매수 → matagi_count 증가
     제외 종목 건너뜀

run_bultagi_cycle(ctx)
  └─ 독립 불타기 모니터
     수익 보유 종목 대상으로 bultagi.py 조건 검증
     통과 시 추가매수 → bultagi_count 증가
     제외 종목 건너뜀

run_morning_sell_cycle(ctx)
  └─ 장 시작 직후 실행 (09:05 전후)
     전일 보유 종목 중 수익률 >= morning_sell_profit_pct 이면 즉시 매도
     당일 매수 종목은 제외(traded_today)

run_morning_stoploss_cycle(ctx)
  └─ 10:30 강제 손절
     전일 보유 종목 중 손실 상태인 종목 전량 매도
     morning_stoploss_enabled=false 이면 미실행

run_stop_loss_check(ctx)
  └─ 인트라데이 손절 모니터 (monitor_interval_seconds 주기)
     WebSocket 가격 우선 → 없으면 REST 조회
     손실률 <= -stop_loss_pct 이면 지정가 매도

_run_domestic_cycle(ctx, token)
  └─ 모의 모드 국내 매매
     holdings 순회 → 손절/데드크로스 매도
     screener.scan() 결과 → 예산 범위 내 매수

_run_nasdaq_cycle(ctx, token)
  └─ 모의 모드 나스닥 매매
     holdings 순회 → 손절/데드크로스 매도
     screener.scan_us() 결과 → USD 예산 범위 내 매수
```

**ctx (컨텍스트 딕셔너리) 주요 키**

| 키 | 내용 |
|----|------|
| `config` | Config 데이터클래스 |
| `token_manager` | 토큰 발급·갱신 |
| `price_client` | 시세 조회 클라이언트 |
| `order_client` | 주문 클라이언트 |
| `screener` | 스캔 인스턴스 |
| `trade_logger` | 거래 기록 |
| `telegram_bot` | 텔레그램 봇 |
| `holdings_cache` | `{code: {qty, avg_price}}` 인메모리 캐시 |
| `matagi_count` | `{code: 물타기_횟수}` |
| `bultagi_count` | `{code: 불타기_횟수}` |
| `realtime_price` | KisRealtimePrice 인스턴스 |
| `order_lock` | 주문 뮤텍스 |
| `budget_lock` | 예산 뮤텍스 |

---

### 설정

#### `config.py`

`.env` 파일의 환경변수를 읽어 `Config` frozen 데이터클래스로 변환한다.  
`load_config(mode)` 호출 시 모드별 접두사(`REAL_*` / `MOCK_*`)를 우선 적용하고, 없으면 공통 값을 사용한다.

**주요 설정 그룹**

| 그룹 | 주요 변수 | 기본값 |
|------|-----------|--------|
| API 자격증명 | `app_key`, `app_secret`, `account_no` | — |
| 전략 | `ma_short_period`, `ma_long_period` | 5, 20 |
| 포지션 관리 | `max_positions`, `mock_budget`, `real_budget` | 5, 500000, 500000 |
| 익절 | `take_profit_rate`, `take_profit_limit_pct` | 3.0%, 0.3% |
| 손절 | `stop_loss_pct`, `stop_loss_limit_pct` | 2.0%, 0.5% |
| 스캔 | `scan_all_stocks`, `watchlist`, `exclude_list` | false, "", "" |
| 물타기 | `matagi_drop_pct`, `matagi_max_count`, `matagi_stage_multipliers` | 2.0, 3, [1.0,2.5,4.0] |
| 불타기 | `bultagi_profit_pct`, `bultagi_max_count`, `bultagi_stage_multipliers` | 3.0, 3, [1.0,2.0,3.0] |
| 시장 체제 | `market_regime_enabled`, `market_regime_ma_period`, `market_regime_rsi_min` | false, 20, 40 |
| 주문 유형 | `order_type`, `limit_order_pct` | market, 0.3% |
| 모니터링 | `monitor_interval_seconds` | 30 |

**모드별 오버라이드 규칙**
```
# MOCK 모드 예시
EXCLUDE_LIST_MOCK=005930,000660   ← 모의 전용
EXCLUDE_LIST=035720               ← 공통 (모드 전용 없을 때 사용)
```

---

### 매매 실행 레이어

#### `trader/real_domestic.py`

실전 국내 매매 사이클. 모의 모드와 달리 **파이프라인 방식**으로 신호 즉시 매수한다.

```
run_real_domestic_cycle(ctx, token, skip_buy)

  [매도 페이즈]
  holdings 순회 (제외 종목 건너뜀)
    → 손절 조건: 손실률 <= -stop_loss_pct → 지정가 매도
    → 데드크로스: strategy.should_sell(prices) → 매도

  [매수 페이즈] (skip_buy=False 일 때)
  screener.scan()을 백그라운드 스레드로 실행
    └─ 매칭 종목 발견 시 signal_queue.put() 즉시 전송
  메인 스레드: signal_queue.get(timeout=1) 루프
    → 신호 수신 → 예산 계산 → 주문 → 체결 확인 → 캐시 업데이트
    → WebSocket 구독 추가 → 텔레그램 알림
  포지션 한도 도달 시 stop_event로 스캔 조기 종료
```

**파이프라인의 이점**  
스캔과 매수가 동시에 진행되므로, 첫 신호 포착 즉시 주문이 나간다.  
배치 방식(스캔 완료 후 일괄 매수)보다 체결 시점이 빠르다.

---

#### `trader/real_nasdaq.py`

실전 해외(나스닥) 매매 사이클.  
국내 사이클과 구조는 같지만 **텔레그램 매수 확인** 단계가 추가된다.

```
run_real_nasdaq_cycle(ctx, token)

  [매도] holdings 순회 (제외 종목 건너뜀)
    → 손절 또는 데드크로스 → sell_overseas()

  [매수] screener.scan_us() 결과 루프
    → 제외 종목, 이미 보유 종목 건너뜀
    → 텔레그램으로 매수 승인 요청 (5분 타임아웃)
    → 승인 시 buy_overseas() → 거래 기록
    → 거절 또는 타임아웃 시 건너뜀
```

**해외 주문의 제약**  
KIS API는 해외 주문 체결 조회를 지원하지 않으므로 주문가를 체결가로 간주한다.

---

#### `trader/matagi.py`

물타기(손실 추가매수) 조건 검증기. 아래 조건을 **모두** 충족해야 True를 반환한다.

```
check_matagi_conditions(price_client, code, token, avg_price, current_price, ...)

  1. MA 필터 (use_ma_filter=True 일 때)
     현재가 > MA(ma_period)  ← 추세 하락 중이면 물타기 금지

  2. 하락률 조건
     (현재가 - 매입가) / 매입가 <= -(drop_pct × stage_multiplier)
     1차: -2.0% (×1.0), 2차: -5.0% (×2.5), 3차: -8.0% (×4.0)

  3. 반등 신호 (use_rebound_filter=True 일 때)
     양봉 (종가 > 시가)
     또는 거래량 급증 (당일 > 직전 vol_lookback 평균)
```

**단계별 승수(stage_multipliers)**  
추가매수를 할수록 더 깊은 하락이어야 발동한다.  
1차에서 작은 하락, 3차에서는 큰 하락이 있어야 평균단가를 효과적으로 낮출 수 있다.

---

#### `trader/bultagi.py`

불타기(수익 추가매수) 조건 검증기. 물타기와 반대 방향이다.

```
check_bultagi_conditions(price_client, code, token, avg_price, current_price, ...)

  1. MA 필터 (use_ma_filter=True 일 때)
     현재가 > MA(ma_period)

  2. 수익률 조건
     (현재가 - 매입가) / 매입가 >= profit_pct × stage_multiplier
     1차: +3.0% (×1.0), 2차: +6.0% (×2.0), 3차: +9.0% (×3.0)
```

**물타기와의 차이**  
볼륨 반등 필터가 없다. 수익 중인 종목은 이미 모멘텀이 있으므로 단순 수익률만 검증한다.

---

#### `trader/utils.py`

일일 예산을 추적하는 공유 유틸. 모든 매매 함수가 이 모듈을 통해 예산을 읽고 차감한다.

```
get_daily_budget(ctx)      → 오늘 남은 예산
deduct_daily_budget(ctx, amount)  → 매수 시 차감 (스레드 안전)
add_daily_budget(ctx, amount)     → 매도 시 복구

_ensure_daily_budget(ctx)
  └─ 날짜가 바뀌면 자동으로 예산 리셋
     당일 trades_{mode}.jsonl에서 이미 실행한 매수/매도를 재계산하여 초기화

init_daily_from_api(ctx, executions)
  └─ 봇 시작 시 KIS API 당일 체결 내역으로 예산 초기화 (실전 모드)
```

**이중 초기화 전략**  
봇이 재시작되어도 API 체결 내역 → 로그 재계산 순으로 예산을 복구한다.

---

### 주문 · 시세 레이어

#### `order/order_client.py`

KIS API 주문·잔고 조회를 감싸는 클라이언트.  
모드(mock/real)에 따라 TR_ID와 엔드포인트를 자동으로 전환한다.

```
buy(code, qty, token, limit_price=None)
sell(code, qty, token, limit_price=None)
  └─ limit_price=None → 시장가, 있으면 지정가
     주문번호(ODNO) 반환

buy_overseas(symbol, exchange, qty, token, limit_price=None)
sell_overseas(symbol, exchange, qty, token)

get_execution(code, order_no, token, retries=5)
  └─ 체결 여부 폴링 (1초 간격, 최대 retries회)
     체결 시 {exec_price, exec_time} 반환
     미체결 시 None

get_holdings(token)           → {code: {qty, avg_price}}
get_holdings_detail(token)    → {code: {qty, profit_rate, avg_price}}
get_overseas_holdings(token)  → {symbol: {qty, exchange, avg_price}}

_round_to_tick(price)
  └─ 한국 주식 호가 단위에 맞게 반올림
     (1000원 미만: 1원, 5000원 미만: 5원, 10000원 미만: 10원, ...)
```

**5xx 재시도 로직**  
KIS 서버 오류(5xx) 발생 시 지수 백오프(2s → 4s → 8s...)로 재시도한다.  
클라이언트 오류(4xx)는 즉시 예외를 발생시킨다.

---

#### `market/price_client.py`

KIS 시세 API 클라이언트. 부족한 데이터를 자동으로 페이지네이션하여 채운다.

```
fetch_closing_prices(code, count, token) → List[Decimal]
  └─ 종가 리스트 (오래된 순 → 최신 순)
     한 번 호출로 30개씩 자동 페이지네이션
     최대 2년 이내 데이터만 허용

fetch_ohlcv(code, count, token) → List[dict]
  └─ {open, high, low, close, volume} 리스트
     matagi/bultagi 반등 신호 검증에 사용

fetch_current_price(code, token) → Decimal
  └─ 단일 현재가 조회 (WebSocket 대안)

fetch_overseas_closing_prices(symbol, exchange, count, token) → List[Decimal]
  └─ 미국 주식 종가 (나스닥 사이클에 사용)
```

**Decimal 사용 이유**  
float 연산의 반올림 오류를 방지한다.  
금융 계산에서 `0.1 + 0.2 ≠ 0.3` 문제를 피하기 위해 전 레이어에서 Decimal을 사용한다.

---

#### `market/kis_websocket.py`

KIS WebSocket으로 실시간 현재가를 수신한다. 실전 모드에서만 활성화된다.

```
KisRealtimePrice
  start()          → 데몬 스레드에서 WS 연결 유지
  subscribe(codes) → 종목 구독 추가 (매수 완료 후 호출)
  unsubscribe(codes) → 구독 해제 (매도 후 호출)
  get_prices()     → {code: {price, prdy_ctrt}}  스레드 안전 스냅샷

내부 동작
  승인키 발급 → ws://ops.koreainvestment.com:21000 연결
  TR_ID=H0STCNT0 로 실시간 체결가 수신
  _prices 딕셔너리 갱신 (RLock 보호)
  SSE 큐에 변경 이벤트 브로드캐스트 → 대시보드 실시간 업데이트
```

**봇 ↔ 대시보드 IPC**  
main.py의 라이터 스레드가 1초마다 `logs/ws_prices_real.json`에 가격을 덤프한다.  
dashboard.py의 `/stream/portfolio`가 이 파일을 읽어 SSE로 브라우저에 전달한다.

---

### 스크리너 · 전략 레이어

#### `screener/stock_screener.py`

종목 스캔의 핵심. ThreadPoolExecutor로 병렬 스캔하고 결과를 signal_queue에 즉시 전달한다.

```
scan(token, all_stocks, top_n, max_workers, signal_queue, stop_event)

  1. 시장 체제 확인 (market_regime.check_market_regime)
     → 하락장이면 매수 금지, 스캔 건너뜀

  2. 종목 목록 결정
     watchlist 있음 → 지정 종목만
     all_stocks=True → fetch_all_stock_codes() (~2,500개)
     all_stocks=False → 거래량 상위 100개
     exclude_list 제거

  3. ThreadPoolExecutor(max_workers)로 병렬 처리
     per-thread: fetch_ohlcv → strategy.should_buy() 평가
     매칭 시 results에 추가 + signal_queue.put() (실전 모드)
     stop_event 감지 시 조기 종료

scan_us(token, mode, max_workers)
  └─ 미국 종목 대상 동일 로직
     mode: "nasdaq100" | "sp500" | "all"
     exclude_list 제거
```

**signal_queue 패턴 (실전 모드)**  
스캔이 완료되기 전에도 매칭 종목을 즉시 real_domestic.py에 전달한다.  
포지션 한도에 도달하면 stop_event로 남은 스캔을 중단한다.

---

#### `screener/market_regime.py`

시장 전체가 하락 추세일 때 신규 매수를 차단하는 필터.

```
check_market_regime(price_client, token, config) → (bool, reason)

  결과 캐싱: 5분간 동일 결과 재사용 (불필요한 API 호출 방지)

  MA 필터 (market_regime_ma_period > 0):
    기준 지수(기본: 069500 KODEX200) 종가 조회
    현재가 < MA(period) → False (하락장, 매수 금지)

  RSI 필터 (market_regime_rsi_period > 0, rsi_min > 0):
    RSI 계산
    RSI < rsi_min → False (과매도, 매수 금지)

  에러 발생 시 → True 반환 (봇 멈춤 방지 폴백)
```

---

#### `screener/name_lookup.py`

종목코드를 이름으로 변환하는 유틸.

```
get_stock_name(code) → str

  미국 종목 (비숫자 코드): 하드코딩 딕셔너리 (_US_NAMES)
    예) "AAPL" → "Apple", "MSFT" → "Microsoft"

  국내 종목 (숫자 코드):
    캐시 파일(.token_cache/stock_names.json) 우선
    미스 시 FinanceDataReader로 조회 후 캐시에 저장
```

---

#### `screener/stock_list.py` · `us_stock_list.py`

매매 대상 종목 유니버스를 캐시하는 모듈.

```
stock_list.py
  fetch_all_stock_codes() → List[str]
    KOSPI + KOSDAQ 전 종목 코드
    24시간 캐시 (.token_cache/stock_list.json)

us_stock_list.py
  fetch_nasdaq100() → List[{symbol, exchange}]   # 하드코딩 100개
  fetch_sp500()     → List[{symbol, exchange}]   # FinanceDataReader, 24h 캐시
  fetch_all_us()    → List[{symbol, exchange}]   # NYSE+NASDAQ+AMEX, 24h 캐시
  fetch_us_stocks(mode)  → mode에 따라 위 셋 중 하나 반환
```

---

#### `strategy/base_strategy.py`

모든 전략이 구현해야 하는 추상 기반 클래스.

```python
class BaseStrategy:
    def should_buy(self, prices: List[Decimal]) -> bool   # 매수 신호
    def should_sell(self, prices: List[Decimal]) -> bool  # 매도 신호
    def required_data_points(self) -> int                 # 필요 캔들 수
    def volume_filter(self) -> (bool, int, int)           # 거래량 필터 (선택)
    def get_signal_type(self, prices) -> str              # 신호 이름 (로그용)
```

---

#### `strategy/ma_cross_strategy.py`

기본 전략. 단기 MA와 장기 MA의 교차를 감지한다.

```
골든크로스 (매수):
  전봉: SMA(단기) ≤ SMA(장기)
  현봉: SMA(단기) > SMA(장기)

데드크로스 (매도):
  전봉: SMA(단기) ≥ SMA(장기)
  현봉: SMA(단기) < SMA(장기)

required_data_points = 장기 기간 + 1
```

---

#### `strategy/configurable_strategy.py`

`STRATEGY.md` 또는 `STRATEGY_{MODE}.md` 파일을 읽어 동적으로 전략을 구성한다.  
대시보드에서 설정 변경 후 봇 재시작 없이 반영된다.

```
매수 조건 (모두 True여야 매수):
  골든크로스          - SMA 단기/장기 교차
  상승추세필터        - 중기 MA > 장기 MA (추세 확인)
  수익률필터          - 현재가 > N일 전 가격
  눌림목반등          - 추세 유지 + 단기 하락 후 반등

매도 조건 (하나라도 True이면 매도):
  데드크로스          - SMA 역교차
  손절                - 손실률 초과
  익절                - 수익률 초과

STRATEGY.md 예시:
  ## 매수
  ### 골든크로스
  - 활성화: true
  - 단기: 5
  - 장기: 20
  ### 상승추세필터
  - 활성화: false
```

---

#### `strategy/indicators/`

전략에서 사용하는 지표 계산 모듈.

```
moving_average.py
  sma(prices, period) → Decimal   # 단순 이동평균
  ema(prices, period) → Decimal   # 지수 이동평균 (Wilder 방식)

rsi.py
  rsi(prices, period=14) → Decimal
    Wilder 방식 RSI (0~100)
    >70: 과매수, <30: 과매도

bollinger.py
  bollinger_bands(prices, period, std_dev) → (upper, middle, lower)
    중심선: SMA(period)
    상단: 중심선 + std_dev × 표준편차
    하단: 중심선 - std_dev × 표준편차
```

---

#### `strategy/strategy_loader.py`

`STRATEGY.md`를 파싱하여 `configurable_strategy.py`에 전달하는 딕셔너리로 변환한다.

```
load_strategy_config(path) → dict

  ## 매수 / ## 매도 섹션 파싱
  ### 인디케이터명 → 하위 파라미터 파싱
  - key: value → 타입 자동 추론 (bool / int / float / str)

출력 예:
{
  "buy": {"골든크로스": {"활성화": True, "단기": 5, "장기": 20}},
  "sell": {"데드크로스": {"활성화": True}}
}
```

---

#### `auth/token_manager.py`

KIS OAuth 2.0 액세스 토큰을 관리한다.

```
TokenManager.get_valid_token() → str

  1. 인메모리 캐시 확인 → 유효하면 즉시 반환
  2. 파일 캐시 확인 (.token_cache/token_{mode}.json) → 유효하면 반환
  3. OAuth 엔드포인트 호출 → 신규 토큰 발급
     만료까지 300초(5분) 여유를 두고 갱신 시점 판단
  4. 발급된 토큰 캐시 저장 후 반환

  403 응답 → 분당 1회 제한 감지, 1분 후 재시도
```

**토큰 수명**: KIS 토큰은 24시간 유효. 봇 재시작 시 캐시 파일에서 복구한다.

---

### 알림 · 감사 레이어

#### `notifications/telegram_notifier.py`

매매 이벤트를 텔레그램으로 알린다. 실전 모드 전용 기능인 매수 확인(인라인 키보드)도 제공한다.

```
notify_signal(bot, code, price, signal_type, market)
  → "📊 골든크로스 감지: 삼성전자(005930) ₩70,500"

notify_order_placed(bot, code, qty, price, order_no)
  → "🟡 주문 접수: ..."

notify_buy(bot, code, qty, price, signal_type, signal_time, exec_price)
  → "✅ 매수 체결: ..."

notify_sell(bot, code, qty, price, signal_type, buy_price)
  → "🔴 매도 체결: P&L 표시"

ask_confirm(bot, code, price, signal_type, market, timeout=300) → bool
  → 인라인 키보드 [승인] [취소] 전송
     5분 내 승인 → True, 거절 또는 타임아웃 → False
     (나스닥 실전 매수 시 사용)
```

---

#### `audit/trade_logger.py`

모든 거래를 `logs/trades_{mode}.jsonl`에 추가 전용(append-only)으로 기록한다.  
대시보드 포트폴리오 재구성과 일일 예산 복구의 원천 데이터다.

```
TradeLogger.log(action, code, qty, result, signal_type, exec_price, ...)

기록되는 필드:
  timestamp         체결 시각
  action            BUY | SELL
  stock_code        종목코드
  stock_name        종목명
  quantity          수량
  signal_type       골든크로스 | 데드크로스 | 손절 | 익절 | 물타기 | 불타기
  signal_detected_at 신호 감지 시각
  exec_price        체결가
  exec_confirmed_at 체결 확인 시각
  profit_rate_pct   수익률 (매도 시)
  profit_amount     손익금액 (매도 시)
```

**불변성 보장**  
파일을 덮어쓰지 않고 항상 추가만 한다. 거래 내역 삭제·수정은 이 코드로는 불가능하다.

---

### 대시보드

#### `dashboard.py`

Flask 기반 웹 UI. 봇 제어, 설정 변경, 거래 내역 조회, 실시간 포트폴리오 확인을 제공한다.

**인증**
```
/login  POST
  → 세션 기반 로그인
  → 5회 실패 시 IP 5분 잠금 (브루트포스 방어)
  → HMAC timing-safe 비교 (timing attack 방어)
```

**봇 제어**
```
/api/start  POST → 봇 프로세스 시작 (start.sh 실행)
/api/stop   POST → SIGTERM → 5초 대기 → SIGKILL
/api/deploy POST → git pull → 봇 재시작
/api/status GET  → {running, pid, mode}
```

**데이터 API**
```
/api/portfolio   GET  → 보유 종목 목록 (제외 종목 필터링 포함)
  스냅샷 파일 우선 → 없으면 trades_{mode}.jsonl에서 재계산

/api/trades      GET  → 거래 내역 (날짜별 페이지네이션)
/api/daily-status GET → 오늘 매수/매도/손익 요약
/api/trades/summary GET → 신호 유형별 집계

/stream/portfolio  SSE → 실시간 시세 (ws_prices_real.json 1초 폴링)
  제외 종목 필터링 후 브로드캐스트
/stream/logs      SSE → 로그 파일 실시간 tail
```

**설정 변경**
```
/api/save-config   POST → .env 업데이트 (봇 재시작 없음)
/api/save-restart  POST → .env + STRATEGY.md 업데이트 → 봇 재시작
  → logs/settings_history.jsonl에 변경 이력 기록
```

---

## 데이터 파일 · 로그

| 파일 | 생성 주체 | 용도 |
|------|-----------|------|
| `logs/trades_{mode}.jsonl` | trade_logger | 전체 거래 감사 로그 (append-only) |
| `logs/holdings_{mode}.json` | main.py | 현재 보유 종목 스냅샷 (대시보드용) |
| `logs/daily_status_{mode}.json` | trader/utils.py | 오늘 예산·손익 캐시 |
| `logs/ws_prices_real.json` | main.py (라이터 스레드) | 실시간 WebSocket 가격 (봇→대시보드 IPC) |
| `logs/trading_{mode}.log` | logging | 회전식 로그 (5MB × 5개) |
| `logs/settings_history.jsonl` | dashboard.py | 설정 변경 이력 |
| `.token_cache/token_{mode}.json` | token_manager | OAuth 토큰 캐시 |
| `.token_cache/stock_list.json` | stock_list.py | 국내 전종목 코드 (24h TTL) |
| `.token_cache/stock_names.json` | name_lookup.py | 종목코드↔이름 매핑 |
| `.token_cache/us_stock_list.json` | us_stock_list.py | 미국 종목 유니버스 (24h TTL) |
| `.bot.{mode}.pid` | dashboard.py | 봇 PID (제어용) |
| `STRATEGY.md` | 사용자·대시보드 | 전략 설정 (열 재시작 시 반영) |

---

## 핵심 설계 원칙

### 1. 제외 종목(exclude_list) 필터링 위치

| 위치 | 적용 |
|------|------|
| `stock_screener.scan()` / `scan_us()` | 스캔 대상에서 제외 |
| `_run_domestic_cycle` / `real_domestic` 매도 루프 | 매도 대상에서 제외 |
| `_run_nasdaq_cycle` / `real_nasdaq` 매도·매수 루프 | 매도·매수 대상에서 제외 |
| `run_matagi_cycle` / `run_bultagi_cycle` | 추가매수 대상에서 제외 |
| `run_morning_sell_cycle` / `run_morning_stoploss_cycle` | 장초 매도에서 제외 |
| `run_take_profit_cycle` / `run_stop_loss_check` | 익절·손절 모니터에서 제외 |
| `dashboard.api_portfolio` / `stream_portfolio` | 포트폴리오 표시에서 제외 |

### 2. 스레드 안전성

```
order_lock   → 주문 발생 시 동시 접근 방지 (물타기/불타기/손절/익절이 동시에 주문 불가)
budget_lock  → 예산 차감·복구 시 경쟁 조건 방지
RLock (WS)   → 실시간 가격 딕셔너리 읽기/쓰기 보호
```

### 3. 예산 회계 원칙

```
매수 → deduct_daily_budget(amount)       잔여 예산 감소
매도 → add_daily_budget(amount)          잔여 예산 증가
봇 재시작 → KIS API 체결 내역 또는 trades.jsonl 재계산으로 복구
날짜 변경 → _ensure_daily_budget()이 자동 리셋
```

### 4. 실전 / 모의 분리

| 구분 | 실전(real) | 모의(mock) |
|------|------------|------------|
| 매매 로직 | real_domestic.py / real_nasdaq.py | _run_domestic_cycle / _run_nasdaq_cycle (main.py) |
| 예산 환경변수 | REAL_BUDGET / REAL_USD_BUDGET | MOCK_BUDGET |
| WebSocket | 활성화 | 비활성화 |
| 텔레그램 확인 (나스닥) | 있음 | 없음 |
| KIS TR_ID | 실전 코드 | 모의 코드 |

---

## 환경 변수 레퍼런스

```bash
# KIS API 자격증명 (모드별 접두사 적용)
MOCK_APP_KEY=...
MOCK_APP_SECRET=...
MOCK_ACCOUNT_NO=...
REAL_APP_KEY=...
REAL_APP_SECRET=...
REAL_ACCOUNT_NO=...

# 모드 선택
TRADING_MODE=mock   # mock | real

# 포지션 관리
MAX_POSITIONS=5
MOCK_BUDGET=500000
REAL_BUDGET=1000000
REAL_USD_BUDGET=1000.0

# 매수 전략
MA_SHORT_PERIOD=5
MA_LONG_PERIOD=20
ORDER_TYPE=market         # market | limit
LIMIT_ORDER_PCT=0.3       # 지정가 허용폭 (%)

# 익절 / 손절
TAKE_PROFIT_RATE=3.0      # 익절 수익률 (%)
STOP_LOSS_PCT=2.0         # 손절 손실률 (%)

# 스캔 설정
SCAN_ALL_STOCKS=false
WATCHLIST=005930,000660   # 빈 값이면 자동 스캔
EXCLUDE_LIST=035720        # 제외 종목
SCAN_NASDAQ=false
US_SCAN_MODE=nasdaq100    # nasdaq100 | sp500 | all

# 물타기
MATAGI_DROP_PCT=2.0
MATAGI_MAX_COUNT=3
MATAGI_USE_MA_FILTER=true
MATAGI_USE_REBOUND_FILTER=true

# 불타기
BULTAGI_PROFIT_PCT=3.0
BULTAGI_MAX_COUNT=3
BULTAGI_USE_MA_FILTER=true

# 시장 체제 필터
MARKET_REGIME_ENABLED=false
MARKET_REGIME_MA_PERIOD=20
MARKET_REGIME_RSI_MIN=40

# 모니터링
MONITOR_INTERVAL_SECONDS=30
MORNING_SELL_PROFIT_PCT=1.0
MORNING_STOPLOSS_ENABLED=true

# 텔레그램
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# 대시보드
DASHBOARD_ADMIN_USER=admin
DASHBOARD_ADMIN_PASS=...
DASHBOARD_SECRET_KEY=...
```

---

> **주의**: `.env` 파일에는 실제 API 키와 비밀번호가 포함되므로 절대 커밋하지 않는다.  
> 처음 설정 시 `.env.example`을 복사하여 값을 채운다.
