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


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    pid_file = _BASE / ".bot.pid"
    running = False
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (ProcessLookupError, ValueError):
            pass
        except PermissionError:
            running = True  # 프로세스 존재하지만 권한 없음

    mode = os.getenv("TRADING_MODE", "mock")
    return jsonify({"running": running, "pid": pid, "mode": mode})


@app.route("/api/bot/deploy", methods=["POST"])
def api_bot_deploy():
    lines = []
    try:
        # 1. 봇 정지
        pid_file = _BASE / ".bot.pid"
        if pid_file.exists():
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
                lines.append("[deploy] 봇 정지 완료")
            except (ProcessLookupError, ValueError):
                pid_file.unlink(missing_ok=True)
                lines.append("[deploy] 봇 이미 정지됨")

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
        lines.append("[deploy] 봇 재시작 중...")
        subprocess.Popen(
            [str(_BASE / "start.sh")],
            cwd=str(_BASE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.2)
        pid = None
        running = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                running = True
            except (ProcessLookupError, ValueError):
                pass
            except PermissionError:
                running = True
        lines.append(f"[deploy] 봇 {'시작됨 (PID ' + str(pid) + ')' if running else '시작 실패'}")
        return jsonify({"ok": running, "lines": lines, "pid": pid})

    except subprocess.TimeoutExpired:
        lines.append("[deploy] git pull 타임아웃")
        return jsonify({"ok": False, "lines": lines, "error": "timeout"}), 500
    except Exception as e:
        lines.append(f"[deploy] 오류: {e}")
        return jsonify({"ok": False, "lines": lines, "error": str(e)}), 500


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    try:
        subprocess.Popen(
            [str(_BASE / "start.sh")],
            cwd=str(_BASE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.2)
        pid_file = _BASE / ".bot.pid"
        pid = None
        running = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                running = True
            except (ProcessLookupError, ValueError):
                pass
            except PermissionError:
                running = True
        return jsonify({"ok": running, "pid": pid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    pid_file = _BASE / ".bot.pid"
    if not pid_file.exists():
        return jsonify({"ok": True, "msg": "이미 정지됨"})
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
        return jsonify({"ok": True})
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    mode = request.args.get("mode", os.getenv("TRADING_MODE", "mock"))
    records = _load_trades(mode)
    return jsonify(list(reversed(records[-200:])))


@app.route("/api/trades/summary")
def api_trades_summary():
    mode = request.args.get("mode", os.getenv("TRADING_MODE", "mock"))
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
    mode = request.args.get("mode", "mock")
    if mode not in ("mock", "real"):
        mode = "mock"
    log_file = _BASE / f"logs/trading_{mode}.log"

    def generate():
        if not log_file.exists():
            yield f"data: {json.dumps(f'[{mode.upper()}] 로그 파일 없음 — 봇을 먼저 시작하세요')}\n\n"
            # 파일이 생길 때까지 대기
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
