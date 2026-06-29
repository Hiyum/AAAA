import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "trading-system-secret")

    MT5_LOGIN = os.getenv("MT5_LOGIN", "")
    MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER = os.getenv("MT5_SERVER", "")

    # Risk management defaults
    DEFAULT_RISK_MODE = "auto"       # "auto" or "manual"
    DEFAULT_RISK_PER_TRADE = 0.02    # 2% per trade (manual mode)
    DEFAULT_DAILY_LOSS_LIMIT = 0.05  # 5% daily loss limit (manual mode)

    # Supported symbols (MT5 registered)
    # ※ 브로커마다 외환 종목명이 다를 수 있음 (EURUSD / EURUSD# / EURUSD.r 등)
    #   MT5 Market Watch에서 정확한 이름 확인 후 맞추세요.
    PRIORITY_SYMBOLS = ["EURUSD"]

    # True: 모든 분석/진입/청산/주시를 TradingView+Claude AI가 담당 (MT5는 주문 실행만)
    # False: MT5 자체 차트 스캔 전략도 병행
    TRADINGVIEW_ONLY = True

    # ── 데이트레이딩 설정 (당일 청산 / 세션 거래) ──────────────
    # 거래 세션 (UTC 기준). 런던+뉴욕 = 07:00~20:00
    TRADE_SESSION_START_HOUR = 7    # UTC
    TRADE_SESSION_END_HOUR = 20     # UTC
    # 당일 청산 시각 (UTC). 이 시각에 모든 포지션 자동 청산 (오버나이트 금지)
    DAILY_FLATTEN_HOUR = 20         # UTC
    ENFORCE_DAY_CLOSE = True        # 당일 청산 강제 여부

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
