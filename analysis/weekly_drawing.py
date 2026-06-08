"""주봉/일봉 작도 분석 CLI.

market.weekly_chart의 계산 로직을 그대로 사용해 콘솔에 요약·봉별 데이터를
출력한다. (대시보드 /api/weekly-chart 와 동일한 결과)

사용: python analysis/weekly_drawing.py 006800 100 real
"""
import os
import sys
import json

# 패키지가 아닌 파일로 직접 실행되므로 프로젝트 루트를 path에 추가.
# (analysis 패키지는 누락 모듈을 import하는 __init__ 때문에 import 불가)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market.weekly_chart import build_chart


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "006800"
    weeks = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    mode = sys.argv[3] if len(sys.argv) > 3 else "real"

    d = build_chart(code, weeks, mode)
    w = d["weekly"]

    print("=== SUMMARY ===")
    print(json.dumps({
        "code": d["code"], "name": d["name"], "mode": d["mode"],
        "weeks": w["count"], "weekFirst": w["first"], "weekLast": w["last"],
        "lastClose": w["lastClose"], "lastRsi": w["lastRsi"],
        "days": d["daily"]["count"] if d["daily"] else 0,
        "periodHigh": d["periodHigh"], "periodLow": d["periodLow"],
    }, ensure_ascii=False, indent=2))

    print("\n=== 작도선 앵커 (주봉 기준: a → b) ===")
    for l in d["lines"]:
        print(f"  {l['label']:<14} {l['a']['time']} {l['a']['value']:>8} → {l['b']['time']} {l['b']['value']:>8}")

    print("\n=== 피보나치 ===")
    for f in d["fib"]:
        print(f"  {f['label']}: {f['price']:>8}")

    print("\n=== 주봉 (date O H L C V RSI) ===")
    rsi_map = {x["time"]: x.get("value", "-") for x in w["rsi"]}
    for c, v in zip(w["candles"], w["volumes"]):
        print(f"{c['time']}  O{c['open']:>7} H{c['high']:>7} L{c['low']:>7} C{c['close']:>7} "
              f"V{v['value']:>11}  RSI{rsi_map.get(c['time'], '-')}")


if __name__ == "__main__":
    main()
