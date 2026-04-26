#!/bin/bash
MODE=${1:-mock}
if [[ "$MODE" != "mock" && "$MODE" != "real" ]]; then
    echo "사용법: ./stop.sh [mock|real]"
    exit 1
fi
PID_FILE=".bot.${MODE}.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "${MODE} 봇이 실행 중이지 않습니다"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm "$PID_FILE"
    echo "${MODE} 봇 종료 (PID: $PID)"
else
    rm "$PID_FILE"
    echo "이미 종료된 프로세스입니다"
fi
