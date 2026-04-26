#!/bin/bash
PID_FILE=".dashboard.pid"

EXISTING=$(pgrep -f "python.*dashboard\.py" 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "기존 대시보드 종료: $EXISTING"
    kill $EXISTING
    sleep 1
fi
rm -f "$PID_FILE"

nohup .venv/bin/python dashboard.py > logs/dashboard.log 2>&1 &
echo $! > "$PID_FILE"
echo "대시보드 시작 (PID: $!) → http://localhost:8383"
