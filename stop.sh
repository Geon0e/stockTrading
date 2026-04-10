#!/bin/bash
PID_FILE=".bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "실행 중인 봇이 없습니다"
    exit 1
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm "$PID_FILE"
    echo "봇 종료 (PID: $PID)"
else
    rm "$PID_FILE"
    echo "이미 종료된 프로세스입니다"
fi
