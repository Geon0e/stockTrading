import json
import logging
import queue
import threading
import time
from typing import Optional

import requests
import websocket

logger = logging.getLogger(__name__)

_APPROVAL_URL = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
_WS_URL = "ws://ops.koreainvestment.com:21000"
_TR_ID = "H0STCNT0"


class KisRealtimePrice:
    """KIS WebSocket 실시간 현재가 구독 관리자 (실전 전용)

    사용 예:
        client = KisRealtimePrice(app_key, app_secret)
        client.start()
        client.subscribe(["005930", "000660"])
        q = client.add_sse_queue()   # SSE 스트림용
    """

    def __init__(self, app_key: str, app_secret: str):
        self._app_key = app_key
        self._app_secret = app_secret
        self._approval_key = ""
        self._ws: Optional[websocket.WebSocketApp] = None
        self._subscribed: set = set()
        self._prices: dict = {}        # {code: {"price": int, "prdy_ctrt": float}}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sse_queues: list = []    # 연결된 SSE 클라이언트 큐 목록

    # ── 공개 API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="kis-ws"
        )
        self._thread.start()
        logger.info("[KIS WS] 실시간 시세 스레드 시작")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            self._ws.close()  # 서버에 close frame 전송
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def subscribe(self, codes: list) -> None:
        """종목 코드 목록 구독 추가 (실행 중에도 가능)"""
        with self._lock:
            new_codes = [c for c in codes if c not in self._subscribed]
            self._subscribed.update(new_codes)
        for code in new_codes:
            if self._ws:
                self._send_sub(code, "1")

    def unsubscribe(self, codes: list) -> None:
        with self._lock:
            to_remove = [c for c in codes if c in self._subscribed]
            for c in to_remove:
                self._subscribed.discard(c)
                self._prices.pop(c, None)
        for code in to_remove:
            if self._ws:
                self._send_sub(code, "2")

    def get_prices(self) -> dict:
        with self._lock:
            return dict(self._prices)

    def add_sse_queue(self) -> queue.Queue:
        """SSE 클라이언트 큐 등록. 반환된 큐에서 price_data를 소비한다."""
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._sse_queues.append(q)
        return q

    def remove_sse_queue(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._sse_queues.remove(q)
            except ValueError:
                pass

    # ── 내부 구현 ──────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._approval_key = self._fetch_approval_key()
                ws = websocket.WebSocketApp(
                    _WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[KIS WS] 루프 오류: {e}")
            finally:
                self._ws = None
            if self._running:
                logger.info("[KIS WS] 10초 후 재연결...")
                time.sleep(10)

    def _fetch_approval_key(self) -> str:
        resp = requests.post(
            _APPROVAL_URL,
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._app_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        key = resp.json().get("approval_key", "")
        logger.info("[KIS WS] 접속키 발급 완료")
        return key

    def _on_open(self, ws) -> None:
        logger.info("[KIS WS] 연결됨")
        with self._lock:
            codes = list(self._subscribed)
        for code in codes:
            self._send_sub(code, "1")

    def _send_sub(self, code: str, tr_type: str) -> None:
        if not self._ws:
            return
        msg = json.dumps({
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": _TR_ID, "tr_key": code}},
        })
        try:
            self._ws.send(msg)
            logger.debug(f"[KIS WS] {'구독' if tr_type == '1' else '해제'}: {code}")
        except Exception as e:
            logger.debug(f"[KIS WS] 전송 오류 [{code}]: {e}")

    def _on_message(self, ws, message: str) -> None:
        if message == "PINGPONG":
            ws.send("PINGPONG")
            return

        if message.startswith("{"):
            try:
                body = json.loads(message).get("body", {})
                msg1 = body.get("msg1", "")
                if body.get("rt_cd") == "0":
                    logger.debug(f"[KIS WS] {msg1}")
                else:
                    logger.warning(f"[KIS WS] {msg1}")
            except Exception:
                pass
            return

        # 실시간 데이터: "0|H0STCNT0|001|field^field^..."
        parts = message.split("|")
        if len(parts) < 4 or parts[1] != _TR_ID:
            return

        fields = parts[3].split("^")
        if len(fields) < 6:
            return

        try:
            code = fields[0]
            price = int(fields[2])
            prdy_ctrt = float(fields[5])
            price_data = {"price": price, "prdy_ctrt": prdy_ctrt}

            with self._lock:
                self._prices[code] = price_data
                queues = list(self._sse_queues)

            update = {"code": code, "price": price, "prdy_ctrt": prdy_ctrt}
            for q in queues:
                try:
                    q.put_nowait(update)
                except queue.Full:
                    pass
        except (ValueError, IndexError) as e:
            logger.debug(f"[KIS WS] 파싱 오류: {e}")

    def _on_error(self, ws, error) -> None:
        logger.error(f"[KIS WS] 오류: {error}")

    def _on_close(self, ws, status_code, msg) -> None:
        logger.info(f"[KIS WS] 연결 종료 ({status_code})")
