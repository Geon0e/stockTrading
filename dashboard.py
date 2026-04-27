import json
import os
import signal
import subprocess
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)
_BASE = Path(__file__).parent


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


def _kill_bot(mode: str) -> tuple:
    pid_file = _BASE / f".bot.{mode}.pid"
    if not pid_file.exists():
        return True, "이미 정지됨"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.8)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pid_file.unlink(missing_ok=True)
        return True, "정지 완료"
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        return True, "이미 정지됨"
    except Exception as e:
        return False, str(e)


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
    mode = _valid_mode((request.get_json(silent=True) or {}).get("mode", "mock"))
    try:
        st = _start_bot(mode)
        return jsonify({"ok": st["running"], "pid": st["pid"], "mode": mode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    mode = _valid_mode((request.get_json(silent=True) or {}).get("mode", "mock"))
    ok, msg = _kill_bot(mode)
    if ok:
        return jsonify({"ok": True, "msg": msg})
    return jsonify({"ok": False, "error": msg}), 500


@app.route("/api/bot/deploy", methods=["POST"])
def api_bot_deploy():
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
    env = _read_env()
    shared = int(env.get("SCAN_INTERVAL_MINUTES", "0"))
    return jsonify({
        "scan_interval_minutes_mock": int(env.get("SCAN_INTERVAL_MINUTES_MOCK", str(shared))),
        "scan_interval_minutes_real": int(env.get("SCAN_INTERVAL_MINUTES_REAL", str(shared))),
        "mock_budget": int(env.get("MOCK_BUDGET", "500000")),
        "real_budget": int(env.get("REAL_BUDGET", "500000")),
        "real_usd_budget": float(env.get("REAL_USD_BUDGET", "750.0")),
        "max_positions": int(env.get("MAX_POSITIONS", "5")),
        "order_quantity": int(env.get("ORDER_QUANTITY", "1")),
        "watchlist": env.get("WATCHLIST", ""),
    })


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    if "scan_interval_minutes" in data:
        mode = _valid_mode(data.get("mode", "mock"))
        val = int(data["scan_interval_minutes"])
        if val < 0:
            return jsonify({"ok": False, "error": "유효하지 않은 값"}), 400
        _write_env_key(f"SCAN_INTERVAL_MINUTES_{mode.upper()}", str(val))
    if "mock_budget" in data:
        _write_env_key("MOCK_BUDGET", str(int(data["mock_budget"])))
    if "real_budget" in data:
        _write_env_key("REAL_BUDGET", str(int(data["real_budget"])))
    if "real_usd_budget" in data:
        _write_env_key("REAL_USD_BUDGET", str(float(data["real_usd_budget"])))
    if "max_positions" in data:
        val = int(data["max_positions"])
        if val < 1:
            return jsonify({"ok": False, "error": "최대 보유 종목 수는 1 이상이어야 합니다"}), 400
        _write_env_key("MAX_POSITIONS", str(val))
    if "order_quantity" in data:
        val = int(data["order_quantity"])
        if val < 1:
            return jsonify({"ok": False, "error": "주문 수량은 1 이상이어야 합니다"}), 400
        _write_env_key("ORDER_QUANTITY", str(val))
    if "watchlist" in data:
        cleaned = ",".join(c.strip() for c in str(data["watchlist"]).split(",") if c.strip())
        _write_env_key("WATCHLIST", cleaned)
    return jsonify({"ok": True})


@app.route("/api/trades")
def api_trades():
    mode = _valid_mode(request.args.get("mode", "mock"))
    records = _load_trades(mode)
    return jsonify(list(reversed(records[-200:])))


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
        {"code": v["code"], "name": v["name"], "qty": v["qty"], "avg_price": round(v["avg_price"], 2)}
        for v in holdings.values()
    ]
    return jsonify(result)


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
