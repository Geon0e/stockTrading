#!/bin/bash
PID_FILE=".bot.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    echo "실행 중 (PID: $(cat $PID_FILE))"
else
    echo "중지됨"
fi

echo ""
echo "=== 최근 로그 ==="
tail -20 logs/trading.log
