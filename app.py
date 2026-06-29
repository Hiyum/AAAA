import json
import logging
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
from datetime import datetime
import queue
import threading

from core.trading_engine import TradingEngine
from core.backtest import Backtester
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__, template_folder="dashboard/templates", static_folder="static")
app.config["SECRET_KEY"] = Config.SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

log_queue = queue.Queue(maxsize=500)


def broadcast_log(entry: dict):
    log_queue.put(entry)
    socketio.emit("log", entry)


engine = TradingEngine(log_callback=broadcast_log)
backtester = Backtester(mt5_connector=engine.mt5)


# ─── Routes ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/connect", methods=["POST"])
def connect_mt5():
    data = request.json or {}
    login = data.get("login", Config.MT5_LOGIN)
    password = data.get("password", Config.MT5_PASSWORD)
    server = data.get("server", Config.MT5_SERVER)

    if not all([login, password, server]):
        return jsonify({"success": False, "message": "계정 정보를 모두 입력해주세요."})

    result = engine.mt5.connect(str(login), str(password), str(server))
    if result["success"]:
        broadcast_log({"time": datetime.now().strftime("%H:%M:%S"),
                       "message": f"MT5 연결 성공 - 계좌: {login}", "level": "SUCCESS"})
    return jsonify(result)


@app.route("/api/disconnect", methods=["POST"])
def disconnect_mt5():
    engine.mt5.disconnect()
    broadcast_log({"time": datetime.now().strftime("%H:%M:%S"),
                   "message": "MT5 연결 해제", "level": "WARNING"})
    return jsonify({"success": True, "message": "MT5 연결 해제됨"})


@app.route("/api/start", methods=["POST"])
def start_trading():
    result = engine.start()
    return jsonify(result)


@app.route("/api/stop", methods=["POST"])
def stop_trading():
    result = engine.stop()
    return jsonify(result)


@app.route("/api/status")
def get_status():
    return jsonify(engine.get_status())


@app.route("/api/risk", methods=["POST"])
def update_risk():
    data = request.json or {}
    engine.risk.set_mode(
        mode=data.get("mode", "auto"),
        risk_per_trade=float(data.get("risk_per_trade", 0.02)),
        daily_loss_limit=float(data.get("daily_loss_limit", 0.05)),
        fixed_lot=float(data.get("fixed_lot", 0)),
    )
    return jsonify({"success": True, "settings": engine.risk.get_settings()})


@app.route("/api/reload_strategy", methods=["POST"])
def reload_strategy():
    success = engine.reload_strategy()
    return jsonify({"success": success})


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    data = request.json or {}
    symbol = data.get("symbol", "XAUUSD")
    days = int(data.get("days", 30))
    balance = float(data.get("balance", 10000))

    if not engine.strategy:
        engine.load_strategy(symbol)

    if not engine.strategy:
        return jsonify({"error": "전략 로드 실패"})

    result = backtester.run(engine.strategy, symbol, days, balance)
    return jsonify(result)


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    """
    TradingView가 분석한 지표 데이터를 수신 → Claude AI 종합 판단 → MT5 주문.
    TradingView Alert Message 예시(JSON):
    {
      "symbol": "GOLD#",
      "price": {{close}},
      "market_structure": "bullish",
      "poc": 4040.5,
      "liquidity_sweep": "bullish_sweep",
      "cvd": "rising",
      "vwap": 4038.2,
      "rsi": 42,
      "timeframe": "M5"
    }
    """
    secret = request.headers.get("X-Webhook-Secret", "")
    if Config.WEBHOOK_SECRET and secret != Config.WEBHOOK_SECRET:
        return jsonify({"error": "인증 실패"}), 403

    # TradingView는 Content-Type을 text/plain으로 보내므로 request.json이 실패함.
    # force=True로 강제 파싱하고, 그래도 안 되면 raw 본문을 직접 json.loads.
    data = request.get_json(force=True, silent=True)
    if data is None:
        raw = request.get_data(as_text=True).strip()
        try:
            data = json.loads(raw)
        except Exception:
            broadcast_log({
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": f"TradingView 메시지 파싱 실패 (JSON 아님): {raw[:120]}",
                "level": "ERROR"
            })
            return jsonify({"error": "JSON 형식이 아닙니다. TradingView 알람을 'Any alert() function call'로 만들었는지 확인하세요."}), 200

    broadcast_log({
        "time": datetime.now().strftime("%H:%M:%S"),
        "message": f"TradingView 수신: {data.get('symbol', '?')} @ {data.get('price', '?')} | 구조:{data.get('market_structure', '?')} CVD:{data.get('cvd', '?')}",
        "level": "INFO"
    })

    # 진입/주시 모두 엔진이 통합 처리 (포지션 유무에 따라 자동 분기)
    result = engine.process_tradingview(data)
    return jsonify(result)


@app.route("/api/logs")
def get_logs():
    logs = []
    while not log_queue.empty():
        try:
            logs.append(log_queue.get_nowait())
        except queue.Empty:
            break
    return jsonify(logs)


# ─── SocketIO ──────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("status", engine.get_status())


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    print("=" * 50)
    print("  AI 자동매매 시스템 시작")
    print("  http://localhost:5000")
    print("=" * 50)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
