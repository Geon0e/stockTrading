#!/bin/bash
MODE=${1:-mock}
if [[ "$MODE" != "mock" && "$MODE" != "real" ]]; then
    echo "사용법: ./start.sh [mock|real]"
    exit 1
fi
PID_FILE=".bot.${MODE}.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "기존 ${MODE} 봇 종료 (PID: $OLD_PID) — 완전 종료 대기 중..."
        kill "$OLD_PID"
        # 단일화 락 해제(=프로세스 완전 종료)까지 대기 — 가드와의 재시작 레이스 방지
        for _ in $(seq 1 30); do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "정상 종료 안 됨 — 강제 종료(kill -9)"
            kill -9 "$OLD_PID" 2>/dev/null
            sleep 2
        fi
    fi
fi
rm -f "$PID_FILE"

TRADING_MODE=$MODE nohup .venv/bin/python main.py > /dev/null 2>&1 &
echo $! > "$PID_FILE"
echo "${MODE} 봇 시작됨 (PID: $!)"
