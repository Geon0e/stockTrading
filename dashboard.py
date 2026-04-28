import datetime
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, session, stream_with_context

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY", "stocktrading-secret-key-2026")
_BASE = Path(__file__).parent

_ADMIN_USER = "admin"
_ADMIN_PASS = "cjswotl"


# ── 인증 헬퍼 ──────────────────────────────────────────────────────────────

def _role() -> str:
    return session.get("role", "")


def _require_admin():
    if _role() != "admin":
        return jsonify({"ok": False, "error": "관리자 권한이 필요합니다"}), 403
    return None


@app.before_request
def _check_auth():
    public = {"login", "logout", "static"}
    if request.endpoint in public:
        return
    if not _role():
        if request.path.startswith("/api/") or request.path.startswith("/stream/"):
            return jsonify({"ok": False, "error": "로그인이 필요합니다"}), 401
        return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("guest"):
            session["role"] = "guest"
            return redirect("/")
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if u == _ADMIN_USER and p == _ADMIN_PASS:
            session["role"] = "admin"
            return redirect("/")
        error = "아이디 또는 비밀번호가 틀렸습니다"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/auth/status")
def api_auth_status():
    return jsonify({"role": _role()})


def _load_trades(mode: str) -> list:
    path = _BASE / f"logs/trades_{mode}.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _bot_status(mode: str) -> dict:
    pid_file = _BASE / f".bot.{mode}.pid"
    running = False
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)
        except PermissionError:
            running = True
    return {"running": running, "pid": pid}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_bot(mode: str) -> tuple:
    pid_file = _BASE / f".bot.{mode}.pid"
    if not pid_file.exists():
        return True, "이미 정지됨"

    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return True, "이미 정지됨"

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return True, "이미 정지됨"
    except Exception as e:
        return False, str(e)

    # Poll until confirmed dead (up to 5s), then SIGKILL
    deadline = time.time() + 5
    while time.time() < deadline:
        time.sleep(0.2)
        if not _pid_alive(pid):
            break
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.5)

    pid_file.unlink(missing_ok=True)
    return True, "정지 완료"


def _start_bot(mode: str) -> dict:
    subprocess.Popen(
        [str(_BASE / "start.sh"), mode],
        cwd=str(_BASE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.2)
    return _bot_status(mode)


def _valid_mode(mode) -> str:
    return mode if mode in ("mock", "real") else "mock"


def _read_env() -> dict:
    env_path = _BASE / ".env"
    result = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _clean_codes(raw: str) -> str:
    """콤마·줄바꿈·공백 혼합 입력을 콤마 구분 종목코드 문자열로 정규화."""
    return ",".join(c.strip() for c in re.split(r"[,\n\r]+", str(raw)) if c.strip())


def _write_env_key(key: str, value: str) -> None:
    env_path = _BASE / ".env"
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _append_settings_history(mode: str, changes: dict) -> None:
    history_path = _BASE / "logs/settings_history.jsonl"
    history_path.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "changes": changes,
    }
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _format_interval(mins: int) -> str:
    if mins == 0:
        return "고정 시간 (09:05 / 23:35)"
    if mins % 1440 == 0:
        return f"{mins // 1440}일마다"
    if mins % 60 == 0:
        return f"{mins // 60}시간마다"
    return f"{mins}분마다"


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "mock": _bot_status("mock"),
        "real": _bot_status("real"),
    })


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    err = _require_admin();
    if err: return err
    mode = _valid_mode((request.get_json(silent=True) or {}).get("mode", "mock"))
    try:
        st = _start_bot(mode)
        return jsonify({"ok": st["running"], "pid": st["pid"], "mode": mode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    err = _require_admin()
    if err: return err
    mode = _valid_mode((request.get_json(silent=True) or {}).get("mode", "mock"))
    ok, msg = _kill_bot(mode)
    if ok:
        return jsonify({"ok": True, "msg": msg})
    return jsonify({"ok": False, "error": msg}), 500


@app.route("/api/bot/deploy", methods=["POST"])
def api_bot_deploy():
    err = _require_admin()
    if err: return err
    mode = _valid_mode((request.get_json(silent=True) or {}).get("mode", "mock"))
    lines = []
    try:
        # 1. 해당 모드 봇 정지
        ok, msg = _kill_bot(mode)
        lines.append(f"[deploy] {msg}")

        # 2. git pull
        lines.append("[deploy] git pull 실행 중...")
        result = subprocess.run(
            ["git", "pull"],
            cwd=str(_BASE),
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                lines.append(f"[git] {line}")
        if result.returncode != 0:
            return jsonify({"ok": False, "lines": lines, "error": "git pull 실패"})

        # 3. 봇 재시작
        lines.append(f"[deploy] {mode} 봇 재시작 중...")
        st = _start_bot(mode)
        lines.append(f"[deploy] {mode} 봇 {'시작됨 (PID ' + str(st['pid']) + ')' if st['running'] else '시작 실패'}")
        return jsonify({"ok": st["running"], "lines": lines, "pid": st["pid"], "mode": mode})

    except subprocess.TimeoutExpired:
        lines.append("[deploy] git pull 타임아웃")
        return jsonify({"ok": False, "lines": lines, "error": "timeout"}), 500
    except Exception as e:
        lines.append(f"[deploy] 오류: {e}")
        return jsonify({"ok": False, "lines": lines, "error": str(e)}), 500


def _write_strategy(config: dict, mode: str = "mock") -> None:
    strategy_path = _BASE / f"STRATEGY_{mode.upper()}.md"

    def fmt_val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    lines = [
        "# 매매 전략 설정", "",
        "## 매수 조건",
        "> 활성화된 조건을 **모두** 충족할 때 매수", "",
    ]
    for name, params in (config.get("buy") or {}).items():
        lines.append(f"### {name}")
        for k, v in params.items():
            lines.append(f"- {k}: {fmt_val(v)}")
        lines.append("")
    lines += ["---", "", "## 매도 조건",
              "> 활성화된 조건 중 **하나라도** 충족하면 매도", ""]
    for name, params in (config.get("sell") or {}).items():
        lines.append(f"### {name}")
        for k, v in params.items():
            lines.append(f"- {k}: {fmt_val(v)}")
        lines.append("")
    strategy_path.write_text("\n".join(lines), encoding="utf-8")


@app.route("/api/strategy")
def api_get_strategy():
    mode = _valid_mode(request.args.get("mode", "mock"))
    path = _BASE / f"STRATEGY_{mode.upper()}.md"
    if not path.exists():
        path = _BASE / "STRATEGY.md"
    try:
        from strategy.strategy_loader import load_strategy_config
        return jsonify(load_strategy_config(str(path)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategy", methods=["POST"])
def api_set_strategy():
    err = _require_admin()
    if err: return err
    data = request.get_json(silent=True) or {}
    mode = _valid_mode(data.get("mode", "mock"))
    strategy_data = {k: v for k, v in data.items() if k != "mode"}
    try:
        _write_strategy(strategy_data, mode)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config")
def api_get_config():
    mode = _valid_mode(request.args.get("mode", "mock"))
    env = _read_env()
    budget = int(env.get("MOCK_BUDGET", "500000")) if mode == "mock" else int(env.get("REAL_BUDGET", "500000"))
    m = mode.upper()
    result = {
        "mode": mode,
        "scan_interval_minutes": int(env.get(f"SCAN_INTERVAL_MINUTES_{m}", "0")),
        "budget": budget,
        "max_positions": int(env.get(f"MAX_POSITIONS_{m}", env.get("MAX_POSITIONS", "5"))),
        "order_quantity": int(env.get(f"ORDER_QUANTITY_{m}", env.get("ORDER_QUANTITY", "0"))),
        "watchlist": env.get(f"WATCHLIST_{m}", env.get("WATCHLIST", "")),
        "exclude_list": env.get(f"EXCLUDE_LIST_{m}", env.get("EXCLUDE_LIST", "")),
        "scan_all_stocks": env.get(f"SCAN_ALL_STOCKS_{m}", env.get("SCAN_ALL_STOCKS", "false")).lower() == "true",
        "scan_nasdaq": env.get(f"SCAN_NASDAQ_{m}", env.get("SCAN_NASDAQ", "false")).lower() == "true",
        "take_profit_rate": float(env.get(f"TAKE_PROFIT_RATE_{m}", env.get("TAKE_PROFIT_RATE", "0"))),
        "take_profit_limit_pct": float(env.get(f"TAKE_PROFIT_LIMIT_PCT_{m}", env.get("TAKE_PROFIT_LIMIT_PCT", "0"))),
        "stop_loss_pct": float(env.get(f"STOP_LOSS_PCT_{m}", env.get("STOP_LOSS_PCT", "0"))),
        "stop_loss_limit_pct": float(env.get(f"STOP_LOSS_LIMIT_PCT_{m}", env.get("STOP_LOSS_LIMIT_PCT", "0"))),
        "monitor_interval_seconds": int(env.get(f"MONITOR_INTERVAL_SECONDS_{m}", env.get("MONITOR_INTERVAL_SECONDS", "60"))),
        "order_type": env.get(f"ORDER_TYPE_{m}", "market"),
        "limit_order_pct": float(env.get(f"LIMIT_ORDER_PCT_{m}", "1.0")),
    }
    if mode == "real":
        result["usd_budget"] = float(env.get("REAL_USD_BUDGET", "750.0"))
    return jsonify(result)


@app.route("/api/config", methods=["POST"])
def api_set_config():
    err = _require_admin()
    if err: return err
    data = request.get_json(silent=True) or {}
    mode = _valid_mode(data.get("mode", "mock"))
    changes = {}

    if "scan_interval_minutes" in data:
        val = int(data["scan_interval_minutes"])
        if val < 0:
            return jsonify({"ok": False, "error": "유효하지 않은 값"}), 400
        _write_env_key(f"SCAN_INTERVAL_MINUTES_{mode.upper()}", str(val))
        changes["scan_interval_minutes"] = val

    if "budget" in data:
        val = int(data["budget"])
        key = "MOCK_BUDGET" if mode == "mock" else "REAL_BUDGET"
        _write_env_key(key, str(val))
        changes["budget"] = val

    if "usd_budget" in data and mode == "real":
        val = float(data["usd_budget"])
        _write_env_key("REAL_USD_BUDGET", str(val))
        changes["usd_budget"] = val

    if "max_positions" in data:
        val = int(data["max_positions"])
        if val < 1:
            return jsonify({"ok": False, "error": "최대 보유 종목 수는 1 이상이어야 합니다"}), 400
        _write_env_key(f"MAX_POSITIONS_{mode.upper()}", str(val))
        changes["max_positions"] = val

    if "order_quantity" in data:
        val = int(data["order_quantity"])
        if val < 1:
            return jsonify({"ok": False, "error": "주문 수량은 1 이상이어야 합니다"}), 400
        _write_env_key(f"ORDER_QUANTITY_{mode.upper()}", str(val))
        changes["order_quantity"] = val

    if "watchlist" in data:
        cleaned = _clean_codes(data["watchlist"])
        _write_env_key(f"WATCHLIST_{mode.upper()}", cleaned)
        changes["watchlist"] = cleaned

    if "exclude_list" in data:
        cleaned = _clean_codes(data["exclude_list"])
        _write_env_key(f"EXCLUDE_LIST_{mode.upper()}", cleaned)
        changes["exclude_list"] = cleaned

    if "monitor_interval_seconds" in data:
        val = int(data["monitor_interval_seconds"])
        if val >= 1:
            _write_env_key(f"MONITOR_INTERVAL_SECONDS_{mode.upper()}", str(val))
            changes["monitor_interval_seconds"] = val

    if changes:
        _append_settings_history(mode, changes)

    return jsonify({"ok": True})


@app.route("/api/save-restart", methods=["POST"])
def api_save_restart():
    err = _require_admin()
    if err: return err
    data = request.get_json(silent=True) or {}
    mode = _valid_mode(data.get("mode", "mock"))
    cfg = data.get("config", {})
    strategy_data = data.get("strategy", {})
    changes = {}

    # ── 설정 저장 ──────────────────────────────────────────────────────────
    if "scan_interval_minutes" in cfg:
        val = int(cfg["scan_interval_minutes"])
        _write_env_key(f"SCAN_INTERVAL_MINUTES_{mode.upper()}", str(val))
        changes["스캔 주기"] = _format_interval(val)

    if "budget" in cfg:
        val = int(cfg["budget"])
        _write_env_key("MOCK_BUDGET" if mode == "mock" else "REAL_BUDGET", str(val))
        changes["예산"] = f"{val:,}원"

    if "usd_budget" in cfg and mode == "real":
        val = float(cfg["usd_budget"])
        _write_env_key("REAL_USD_BUDGET", str(val))
        changes["미국 예산"] = f"${val:,.2f}"

    if "max_positions" in cfg:
        val = int(cfg["max_positions"])
        if val >= 1:
            _write_env_key(f"MAX_POSITIONS_{mode.upper()}", str(val))
            changes["최대 보유"] = f"{val}개"

    if "order_quantity" in cfg:
        val = int(cfg["order_quantity"])
        if val >= 1:
            _write_env_key(f"ORDER_QUANTITY_{mode.upper()}", str(val))
            changes["주문 수량"] = f"{val}주"

    if "watchlist" in cfg:
        cleaned = _clean_codes(cfg["watchlist"])
        _write_env_key(f"WATCHLIST_{mode.upper()}", cleaned)
        changes["스캔 종목"] = cleaned if cleaned else "자동 스캔"

    if "exclude_list" in cfg:
        cleaned = _clean_codes(cfg["exclude_list"])
        _write_env_key(f"EXCLUDE_LIST_{mode.upper()}", cleaned)
        changes["제외 종목"] = cleaned if cleaned else "없음"

    if "scan_all_stocks" in cfg:
        val = bool(cfg["scan_all_stocks"])
        _write_env_key(f"SCAN_ALL_STOCKS_{mode.upper()}", "true" if val else "false")
        changes["국내 스캔"] = "전종목" if val else "거래량 상위"

    if "scan_nasdaq" in cfg:
        val = bool(cfg["scan_nasdaq"])
        _write_env_key(f"SCAN_NASDAQ_{mode.upper()}", "true" if val else "false")
        changes["나스닥 스캔"] = "활성화" if val else "비활성화"

    if "take_profit_rate" in cfg:
        val = float(cfg["take_profit_rate"])
        _write_env_key(f"TAKE_PROFIT_RATE_{mode.upper()}", str(val))
        changes["익절 트리거"] = f"+{val}%" if val > 0 else "비활성화"

    if "take_profit_limit_pct" in cfg:
        val = float(cfg["take_profit_limit_pct"])
        _write_env_key(f"TAKE_PROFIT_LIMIT_PCT_{mode.upper()}", str(val))
        changes["익절 지정가"] = f"매입가×(1+{val}%)" if val > 0 else "트리거%와 동일"

    if "stop_loss_pct" in cfg:
        val = float(cfg["stop_loss_pct"])
        _write_env_key(f"STOP_LOSS_PCT_{mode.upper()}", str(val))
        changes["손절 트리거"] = f"-{val}%" if val > 0 else "비활성화"

    if "stop_loss_limit_pct" in cfg:
        val = float(cfg["stop_loss_limit_pct"])
        _write_env_key(f"STOP_LOSS_LIMIT_PCT_{mode.upper()}", str(val))
        changes["손절 지정가"] = f"매입가×(1-{val}%)" if val > 0 else "트리거%와 동일"

    if "monitor_interval_seconds" in cfg:
        val = int(cfg["monitor_interval_seconds"])
        if val >= 1:
            _write_env_key(f"MONITOR_INTERVAL_SECONDS_{mode.upper()}", str(val))
            changes["모니터링 주기"] = f"{val}초"

    if "order_type" in cfg:
        val = cfg["order_type"] if cfg["order_type"] in ("market", "limit") else "market"
        _write_env_key(f"ORDER_TYPE_{mode.upper()}", val)
        changes["주문 방식"] = "시장가" if val == "market" else "지정가"

    if "limit_order_pct" in cfg:
        val = float(cfg["limit_order_pct"])
        _write_env_key(f"LIMIT_ORDER_PCT_{mode.upper()}", str(val))
        if cfg.get("order_type") == "limit":
            changes["지정가 허용폭"] = f"+{val}%"

    # ── 전략 저장 ──────────────────────────────────────────────────────────
    if strategy_data:
        try:
            _write_strategy(strategy_data, mode)
            buy_active = [
                name for name, params in (strategy_data.get("buy") or {}).items()
                if params.get("활성화")
            ]
            sell_active = [
                name for name, params in (strategy_data.get("sell") or {}).items()
                if params.get("활성화")
            ]
            changes["매수 조건"] = ", ".join(buy_active) if buy_active else "없음"
            changes["매도 조건"] = ", ".join(sell_active) if sell_active else "없음"
        except Exception as e:
            return jsonify({"ok": False, "error": f"전략 저장 실패: {e}"}), 500

    # ── 히스토리 기록 ──────────────────────────────────────────────────────
    if changes:
        _append_settings_history(mode, changes)

    # ── 봇 재시작 ──────────────────────────────────────────────────────────
    _kill_bot(mode)
    st = _start_bot(mode)
    return jsonify({"ok": st["running"], "pid": st["pid"], "mode": mode})


@app.route("/api/settings/history")
def api_settings_history():
    mode = _valid_mode(request.args.get("mode", "mock"))
    history_path = _BASE / "logs/settings_history.jsonl"
    if not history_path.exists():
        return jsonify([])
    # 날짜별 마지막 레코드만 유지 (파일은 시간순이므로 뒤에 올수록 최신)
    by_date: dict = {}
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("mode") == mode:
                date = r.get("timestamp", "")[:10]  # "2026-04-27"
                by_date[date] = r
        except json.JSONDecodeError:
            pass
    records = sorted(by_date.values(), key=lambda r: r["timestamp"], reverse=True)
    return jsonify(records[:50])


@app.route("/api/trades")
def api_trades():
    mode = _valid_mode(request.args.get("mode", "mock"))
    date = request.args.get("date", "")
    records = _load_trades(mode)
    if date:
        records = [r for r in records if (r.get("timestamp") or "").startswith(date)]
        return jsonify(list(reversed(records)))
    return jsonify(list(reversed(records[-200:])))


@app.route("/api/trades/dates")
def api_trades_dates():
    mode = _valid_mode(request.args.get("mode", "mock"))
    records = _load_trades(mode)
    seen = {}
    for r in records:
        ts = r.get("timestamp") or ""
        d = ts[:10]
        if d:
            seen[d] = seen.get(d, 0) + 1
    dates = sorted(seen.keys(), reverse=True)
    return jsonify([{"date": d, "count": seen[d]} for d in dates])


@app.route("/api/portfolio")
def api_portfolio():
    mode = _valid_mode(request.args.get("mode", "mock"))
    snapshot_path = _BASE / f"logs/holdings_{mode}.json"
    if snapshot_path.exists():
        try:
            return jsonify(json.loads(snapshot_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    records = _load_trades(mode)
    holdings = {}
    for r in records:
        code = r.get("stock_code")
        if not code:
            continue
        action = r.get("action")
        qty = int(r.get("quantity", 0))
        price = float(r.get("exec_price") or 0)
        name = r.get("stock_name", "")
        if action == "BUY":
            if code not in holdings:
                holdings[code] = {"code": code, "name": name, "qty": 0, "avg_price": 0.0, "total_cost": 0.0}
            h = holdings[code]
            h["total_cost"] += price * qty
            h["qty"] += qty
            h["avg_price"] = h["total_cost"] / h["qty"] if h["qty"] > 0 else 0.0
            if name:
                h["name"] = name
        elif action == "SELL":
            if code in holdings:
                holdings[code]["qty"] -= qty
                if holdings[code]["qty"] <= 0:
                    del holdings[code]
    result = [
        {"code": v["code"], "name": v["name"], "qty": v["qty"], "avg_price": round(v["avg_price"], 2),
         "current_price": v.get("current_price"), "profit_pct": v.get("profit_pct")}
        for v in holdings.values()
    ]
    result.sort(key=lambda x: (x["profit_pct"] is None, -(x["profit_pct"] or 0)))
    return jsonify(result)


@app.route("/api/daily-status")
def api_daily_status():
    mode = _valid_mode(request.args.get("mode", "mock"))
    today = datetime.date.today()
    today_str = str(today)
    req_date = request.args.get("date", "").strip() or today_str

    # 오늘 날짜면 캐시 파일 우선 사용
    status_path = _BASE / f"logs/daily_status_{mode}.json"
    if req_date == today_str and status_path.exists():
        try:
            cached = json.loads(status_path.read_text(encoding="utf-8"))
            if cached.get("date") == today_str and "profit_amount" in cached:
                return jsonify(cached)
        except Exception:
            pass

    # 거래 내역에서 해당 날짜 현황 계산
    env = _read_env()
    budget = int(env.get("MOCK_BUDGET", "500000")) if mode == "mock" else int(env.get("REAL_BUDGET", "500000"))
    buy_count = buy_amount = sell_amount = profit_amount = tp_count = tp_amount = 0
    for r in _load_trades(mode):
        ts = r.get("timestamp", "")
        if not ts.startswith(req_date):
            continue
        qty = int(r.get("quantity", 0))
        price = float(r.get("exec_price") or 0)
        amount = int(price * qty)
        if r.get("action") == "BUY":
            buy_count += 1
            buy_amount += amount
        elif r.get("action") == "SELL":
            sell_amount += amount
            if r.get("profit_amount") is not None:
                profit_amount += int(r["profit_amount"])
            elif r.get("profit_rate_pct") is not None and amount > 0:
                rate = float(r["profit_rate_pct"])
                profit_amount += int(amount * rate / (100 + rate))
            if "익절" in str(r.get("signal_type", "")):
                tp_count += 1
                tp_amount += amount

    data = {
        "date": req_date, "mode": mode,
        "budget_total": budget,
        "budget_remaining": max(0, budget - buy_amount + sell_amount),
        "buy_count": buy_count, "buy_amount": buy_amount,
        "sell_amount": sell_amount, "profit_amount": profit_amount,
        "take_profit_count": tp_count, "take_profit_amount": tp_amount,
    }
    # 오늘 날짜 재계산 결과만 캐시 파일로 저장
    if req_date == today_str:
        try:
            status_path.parent.mkdir(exist_ok=True)
            status_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return jsonify(data)


@app.route("/api/trades/summary")
def api_trades_summary():
    mode = _valid_mode(request.args.get("mode", "mock"))
    records = _load_trades(mode)
    buys = [r for r in records if r.get("action") == "BUY"]
    sells = [r for r in records if r.get("action") == "SELL"]
    by_signal: dict = {}
    for r in buys:
        sig = r.get("signal_type", "기타")
        by_signal[sig] = by_signal.get(sig, 0) + 1
    return jsonify({"total": len(records), "buys": len(buys), "sells": len(sells), "by_signal": by_signal})


@app.route("/stream/logs")
def stream_logs():
    mode = _valid_mode(request.args.get("mode", "mock"))
    log_file = _BASE / f"logs/trading_{mode}.log"

    def generate():
        if not log_file.exists():
            yield f"data: {json.dumps(f'[{mode.upper()}] 로그 파일 없음 — 봇을 먼저 시작하세요')}\n\n"
            while not log_file.exists():
                time.sleep(1)
                yield ": waiting\n\n"

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            for line in lines[-150:]:
                stripped = line.rstrip()
                if stripped:
                    yield f"data: {json.dumps(stripped)}\n\n"
            while True:
                line = f.readline()
                if line:
                    stripped = line.rstrip()
                    if stripped:
                        yield f"data: {json.dumps(stripped)}\n\n"
                else:
                    time.sleep(0.3)
                    yield ": heartbeat\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8383, debug=False, threaded=True)
