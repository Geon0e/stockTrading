#!/usr/bin/env python3
"""거래 내역 조회 스크립트

사용법:
    python show_trades.py              # 전체 내역
    python show_trades.py -n 20        # 최근 20건
    python show_trades.py -a BUY       # 매수만
    python show_trades.py -a SELL      # 매도만
    python show_trades.py -c 005930    # 특정 종목
    python show_trades.py --date 2026-04-09   # 특정 날짜
    python show_trades.py --summary    # 요약 통계
"""
import json
import argparse
from pathlib import Path
from datetime import datetime
from decimal import Decimal, InvalidOperation

# ── 종목명 조회 ────────────────────────────────────────
_KR_NAME_CACHE: dict | None = None
_NAME_CACHE_FILE = Path(".token_cache/stock_names.json")

_US_NAMES = {
    "AAPL":"Apple","MSFT":"Microsoft","NVDA":"NVIDIA","AMZN":"Amazon",
    "META":"Meta","TSLA":"Tesla","GOOGL":"Alphabet A","GOOG":"Alphabet C",
    "AVGO":"Broadcom","COST":"Costco","NFLX":"Netflix","TMUS":"T-Mobile",
    "AMD":"AMD","PEP":"PepsiCo","QCOM":"Qualcomm","ADBE":"Adobe",
    "AMAT":"Applied Materials","TXN":"Texas Instruments","INTU":"Intuit",
    "ISRG":"Intuitive Surgical","CMCSA":"Comcast","BKNG":"Booking",
    "VRTX":"Vertex Pharma","MU":"Micron","REGN":"Regeneron","LRCX":"Lam Research",
    "PANW":"Palo Alto","KLAC":"KLA Corp","ABNB":"Airbnb","SNPS":"Synopsys",
    "MELI":"MercadoLibre","CDNS":"Cadence","CRWD":"CrowdStrike","ASML":"ASML",
    "ADP":"ADP","CSX":"CSX","ORLY":"O'Reilly","FTNT":"Fortinet",
    "MRVL":"Marvell","NXPI":"NXP Semi","PCAR":"PACCAR","WDAY":"Workday",
    "DASH":"DoorDash","ADSK":"Autodesk","DXCM":"Dexcom","ROST":"Ross Stores",
    "PAYX":"Paychex","CTAS":"Cintas","GILD":"Gilead","SBUX":"Starbucks",
    "AMGN":"Amgen","HON":"Honeywell","INTC":"Intel","MDLZ":"Mondelez",
    "LULU":"Lululemon","MAR":"Marriott","PYPL":"PayPal","EBAY":"eBay",
}


def _load_kr_names() -> dict:
    global _KR_NAME_CACHE
    if _KR_NAME_CACHE is not None:
        return _KR_NAME_CACHE

    if _NAME_CACHE_FILE.exists():
        try:
            _KR_NAME_CACHE = json.loads(_NAME_CACHE_FILE.read_text(encoding="utf-8"))
            return _KR_NAME_CACHE
        except Exception:
            pass

    try:
        import FinanceDataReader as fdr
        print("종목명 로딩 중...", end="\r")
        kospi  = fdr.StockListing("KOSPI")[["Code","Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Code","Name"]]
        import pandas as pd
        combined = pd.concat([kospi, kosdaq]).drop_duplicates("Code")
        _KR_NAME_CACHE = dict(zip(combined["Code"], combined["Name"]))
        _NAME_CACHE_FILE.parent.mkdir(exist_ok=True)
        _NAME_CACHE_FILE.write_text(
            json.dumps(_KR_NAME_CACHE, ensure_ascii=False), encoding="utf-8"
        )
        print(" " * 20, end="\r")
    except Exception:
        _KR_NAME_CACHE = {}

    return _KR_NAME_CACHE


def get_stock_name(code: str) -> str:
    if _is_overseas(code):
        return _US_NAMES.get(code.upper(), code)
    return _load_kr_names().get(code, "")

# ── ANSI 색상 ────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
GRAY   = "\033[90m"
WHITE  = "\033[97m"
BG_DARK = "\033[48;5;235m"


def _load(mode: str) -> list:
    path = Path(f"logs/trades_{mode}.jsonl")
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _fmt_price(price_str: str, market: str = "KR") -> str:
    try:
        p = Decimal(price_str)
        return f"${p:,.2f}" if market == "US" else f"{int(p):,}원"
    except (InvalidOperation, ValueError):
        return price_str or "-"


def _fmt_time(iso: str) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


def _action_color(action: str) -> str:
    return GREEN if action == "BUY" else RED


def _divider(char: str = "─", width: int = 72) -> str:
    return GRAY + char * width + RESET


def print_records(records: list) -> None:
    if not records:
        print(f"\n{YELLOW}거래 내역이 없습니다.{RESET}\n")
        return

    print(f"\n{BOLD}{BG_DARK}{'':^72}{RESET}")
    print(f"{BOLD}{BG_DARK}{'  📋  거래 내역':^72}{RESET}")
    print(f"{BOLD}{BG_DARK}{'':^72}{RESET}")

    for i, r in enumerate(records, 1):
        action    = r.get("action", "")
        code      = r.get("stock_code", "")
        name      = get_stock_name(code)
        market    = "US" if _is_overseas(code) else "KR"
        ac        = _action_color(action)
        icon      = "📈" if action == "BUY" else "📉"
        qty       = r.get("quantity", 0)
        sig_type  = r.get("signal_type", "-")
        sig_at    = _fmt_time(r.get("signal_detected_at", ""))
        placed_at = _fmt_time(r.get("order_placed_at", r.get("timestamp", "")))
        exec_p    = _fmt_price(r.get("exec_price", ""), market)
        order_no  = r.get("order_no") or "-"
        mode      = r.get("mode", "")
        rt_cd     = r.get("rt_cd", "")
        status    = f"{GREEN}체결{RESET}" if rt_cd == "0" else f"{RED}실패{RESET}"
        name_disp = f"  {GRAY}{name}{RESET}" if name else ""

        print(_divider())
        print(f"  {BOLD}#{i:03d}{RESET}  {icon} {BOLD}{ac}{action}{RESET}  "
              f"{CYAN}{BOLD}{code}{RESET}{name_disp}  {GRAY}[{mode}]{RESET}  {status}")
        print()
        print(f"  {'주문번호':<10}: {BOLD}{order_no}{RESET}")
        print(f"  {'신호 종류':<10}: {YELLOW}{sig_type}{RESET}")
        print(f"  {'신호 발생':<10}: {sig_at}")
        print(f"  {'주문 시각':<10}: {placed_at}")
        print(f"  {'체결가':<10}: {BOLD}{exec_p}{RESET}")
        print(f"  {'수량':<10}: {qty}주")

    print(_divider("═"))
    print(f"  총 {BOLD}{len(records)}{RESET}건\n")


def print_summary(records: list) -> None:
    if not records:
        print(f"\n{YELLOW}거래 내역이 없습니다.{RESET}\n")
        return

    buys  = [r for r in records if r.get("action") == "BUY"]
    sells = [r for r in records if r.get("action") == "SELL"]

    # 종목별 집계
    by_code: dict = {}
    for r in records:
        code = r.get("stock_code", "")
        if code not in by_code:
            by_code[code] = {"BUY": 0, "SELL": 0}
        by_code[code][r.get("action", "")] = by_code[code].get(r.get("action", ""), 0) + 1

    # 신호 종류별 집계
    by_signal: dict = {}
    for r in buys:
        sig = r.get("signal_type", "기타")
        by_signal[sig] = by_signal.get(sig, 0) + 1

    print(f"\n{BOLD}{'═'*50}{RESET}")
    print(f"{BOLD}  📊  거래 요약{RESET}")
    print(f"{BOLD}{'═'*50}{RESET}")
    print(f"  전체 거래   : {BOLD}{len(records)}{RESET}건")
    print(f"  매수        : {GREEN}{BOLD}{len(buys)}{RESET}건")
    print(f"  매도        : {RED}{BOLD}{len(sells)}{RESET}건")

    if by_code:
        print(f"\n  {BOLD}종목별{RESET}")
        for code, cnt in sorted(by_code.items()):
            name = get_stock_name(code)
            name_disp = f" {GRAY}{name}{RESET}" if name else ""
            print(f"    {CYAN}{code}{RESET}{name_disp:<24} 매수 {GREEN}{cnt['BUY']}{RESET}건  매도 {RED}{cnt['SELL']}{RESET}건")

    if by_signal:
        print(f"\n  {BOLD}신호 종류별 매수{RESET}")
        for sig, cnt in sorted(by_signal.items(), key=lambda x: -x[1]):
            print(f"    {YELLOW}{sig:<20}{RESET} {cnt}건")

    print(f"{'═'*50}\n")


def _is_overseas(code: str) -> bool:
    return bool(code) and code.isalpha()


def main():
    parser = argparse.ArgumentParser(description="거래 내역 조회")
    parser.add_argument("-n", "--limit",   type=int, default=0,    help="최근 N건 표시")
    parser.add_argument("-a", "--action",  type=str, default="",   help="BUY 또는 SELL")
    parser.add_argument("-c", "--code",    type=str, default="",   help="종목코드 필터")
    parser.add_argument("--date",          type=str, default="",   help="날짜 필터 (YYYY-MM-DD)")
    parser.add_argument("--mode",          type=str, default="mock", help="mock 또는 real")
    parser.add_argument("--summary",       action="store_true",    help="요약 통계 표시")
    args = parser.parse_args()

    records = _load(args.mode)

    if args.action:
        records = [r for r in records if r.get("action") == args.action.upper()]
    if args.code:
        records = [r for r in records if r.get("stock_code") == args.code.upper()]
    if args.date:
        records = [r for r in records if r.get("timestamp", "").startswith(args.date)]

    if args.summary:
        print_summary(records)
    else:
        if args.limit:
            records = records[-args.limit:]
        print_records(records)


if __name__ == "__main__":
    main()
