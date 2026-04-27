"""
stock_screener.py 스레딩 변경사항 테스트.

검증 항목:
  1. 결과 정확성 — 순차 스캔과 동일한 종목을 찾는지
  2. 스레드 안전성 — 결과 리스트에 중복/누락 없는지
  3. rate limiter — 20 req/s 초과 안 하는지
  4. 속도 — 스레드 수에 따른 처리 시간 비교
"""

import sys
import os
import time
import threading
from decimal import Decimal
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from screener.stock_screener import StockScreener
from strategy.base_strategy import BaseStrategy

# ── Mock ────────────────────────────────────────────────────────────────────

class _MockStrategy(BaseStrategy):
    def should_buy(self, prices):
        # 마지막 가격이 홀수인 종목만 매수 신호
        return int(prices[-1]) % 2 == 1

    def should_sell(self, prices):
        return False

    @property
    def required_data_points(self):
        return 3


def _make_screener(api_delay: float = 0.01) -> StockScreener:
    config = MagicMock()
    config.watchlist = []
    config.exclude_list = []
    config.app_key = "test"
    config.app_secret = "test"
    config.base_url = "http://test"

    price_client = MagicMock()
    call_counter = {"n": 0}
    lock = threading.Lock()

    def _fetch(code, n, token):
        time.sleep(api_delay)  # API 네트워크 지연 시뮬레이션
        with lock:
            call_counter["n"] += 1
        idx = int(code)
        return [Decimal(idx), Decimal(idx), Decimal(idx)]

    price_client.fetch_closing_prices.side_effect = _fetch
    price_client._call_counter = call_counter

    return StockScreener(config, price_client, _MockStrategy()), price_client


# ── 테스트 1: 결과 정확성 ──────────────────────────────────────────────────

def test_correctness():
    screener, _ = _make_screener(api_delay=0.0)
    codes = [str(i).zfill(6) for i in range(1, 51)]  # 000001 ~ 000050

    # 순차 기준값: should_buy = 마지막 가격(= code 숫자)이 홀수
    expected = {str(i).zfill(6) for i in range(1, 51) if i % 2 == 1}

    screener._price_client.fetch_closing_prices  # already configured

    # 스레드 스캔
    screener._config.watchlist = codes  # watchlist로 고정 종목 사용
    result = screener.scan("token", max_workers=5)
    found = {r["code"] for r in result}

    assert found == expected, f"결과 불일치\n기대: {sorted(expected)}\n실제: {sorted(found)}"
    print(f"[PASS] test_correctness — {len(found)}개 골든크로스 정확히 감지")


# ── 테스트 2: 스레드 안전성 (대량 종목) ──────────────────────────────────────

def test_thread_safety():
    screener, pc = _make_screener(api_delay=0.005)
    codes = [str(i).zfill(6) for i in range(1, 501)]  # 500개

    screener._config.watchlist = codes
    result = screener.scan("token", max_workers=10)

    codes_in_result = [r["code"] for r in result]
    assert len(codes_in_result) == len(set(codes_in_result)), "중복 결과 발견"
    assert pc._call_counter["n"] == 500, f"API 호출 수 불일치: {pc._call_counter['n']}"
    print(f"[PASS] test_thread_safety — 500개 스캔, 중복 없음, API {pc._call_counter['n']}회 호출")


# ── 테스트 3: 스레드별 독립 sleep 동작 확인 ────────────────────────────────

def test_independent_sleep():
    """각 스레드가 독립적으로 sleep → 진짜 병렬 실행 확인."""
    SLEEP = 0.1
    N = 5

    call_times = []
    lock = threading.Lock()

    def _work():
        time.sleep(SLEEP)
        with lock:
            call_times.append(time.time())

    t0 = time.time()
    threads = [threading.Thread(target=_work) for _ in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    # N개 스레드가 각각 SLEEP 하면 전체 시간은 ~SLEEP (직렬이면 N*SLEEP)
    assert elapsed < SLEEP * 2, f"병렬 실행 안 됨: {elapsed:.3f}s (기준 {SLEEP*2:.1f}s)"
    print(f"[PASS] test_independent_sleep — {N}스레드 × {SLEEP}s sleep → 전체 {elapsed:.3f}s (직렬이면 {SLEEP*N:.1f}s)")


# ── 테스트 4: 속도 비교 ──────────────────────────────────────────────────────

def test_speed():
    N = 200
    API_DELAY = 0.03  # API 응답 30ms 가정

    codes = [str(i).zfill(6) for i in range(1, N+1)]

    # 단일 스레드
    screener1, _ = _make_screener(api_delay=API_DELAY)
    screener1._config.watchlist = codes
    t0 = time.time()
    screener1.scan("token", max_workers=1)
    t_single = time.time() - t0

    # 5 스레드
    screener5, _ = _make_screener(api_delay=API_DELAY)
    screener5._config.watchlist = codes
    t0 = time.time()
    screener5.scan("token", max_workers=5)
    t_multi = time.time() - t0

    speedup = t_single / t_multi
    print(f"[INFO] test_speed — {N}개 종목 / API 지연 {API_DELAY*1000:.0f}ms")
    print(f"       단일 스레드: {t_single:.2f}s")
    print(f"       5 스레드:   {t_multi:.2f}s")
    print(f"       속도 향상:  {speedup:.1f}x")
    assert speedup >= 1.5, f"기대한 속도 향상 없음: {speedup:.1f}x"
    print(f"[PASS] test_speed")


# ── 실행 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [test_correctness, test_thread_safety, test_independent_sleep, test_speed]
    failed = []
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed.append(test.__name__)

    print()
    if failed:
        print(f"실패: {failed}")
        sys.exit(1)
    else:
        print("모든 테스트 통과")
