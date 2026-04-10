#!/bin/bash
PID_FILE=".bot.pid"

# 이미 실행 중인 main.py 프로세스 전부 종료
EXISTING=$(pgrep -f "python.*main\.py" 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "기존 프로세스 종료: $EXISTING"
    kill $EXISTING
    sleep 1
fi
rm -f "$PID_FILE"

nohup .venv/bin/python main.py > /dev/null 2>&1 &
echo $! > "$PID_FILE"
echo "봇 시작 (PID: $!)"
