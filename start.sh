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
        echo "기존 ${MODE} 봇 종료 (PID: $OLD_PID)"
        kill $OLD_PID
        sleep 1
    fi
fi
rm -f "$PID_FILE"

TRADING_MODE=$MODE nohup .venv/bin/python main.py > /dev/null 2>&1 &
echo $! > "$PID_FILE"
echo "${MODE} 봇 시작됨 (PID: $!)"
