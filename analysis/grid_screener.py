"""평행채널 그리드 스크리너 CLI.

핵심 로직은 market.grid_screener에 있고, 여기서는 콘솔 출력만 담당.
KOSPI + KOSDAQ 전종목(캐시 목록)을 스캔한다 (수 분 소요).

사용: python analysis/grid_screener.py [tol_pct] [weeks]
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market.grid_screener import screen

tol = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
weeks = int(sys.argv[2]) if len(sys.argv) > 2 else 100

print(f"전종목(KOSPI+KOSDAQ) 그리드 스크리닝 (±{tol}%, 주봉 {weeks}) — 수 분 소요...")
d = screen(mode="real", tol_pct=tol, weeks=weeks)
if not d["ok"]:
    print("실패:", d["error"])
    sys.exit(1)

r = d["results"]
print("=" * 80)
print(f"전종목 {d['scanned']}개 중 그리드 근접 {d['count']}개 · ⭐추천 {d.get('recommended', 0)}개")
print("=" * 80)
print(f"{'':<2}{'코드':<7}{'종목명':<14}{'테마(업종)':<12}{'현재가':>10}{'거래량':>12}{'그리드선':>9}{'이격%':>7}{'채널':>5}{'RSI':>6}")
for x in r:
    mark = '⭐' if x.get('recommended') else '  '
    print(f"{mark:<2}{x['code']:<7}{x['name'][:12]:<14}{(x.get('theme') or '-')[:10]:<12}{x['close']:>10,}{x.get('volume', 0):>12,}{x['line']:>9}"
          f"{x['dist_pct']:>7}{'상승' if x['asc'] else '하락':>5}{x['rsi'] if x['rsi'] is not None else '-':>6}")

open("/tmp/grid_screen_result.json", "w").write(json.dumps(r, ensure_ascii=False, indent=2))
