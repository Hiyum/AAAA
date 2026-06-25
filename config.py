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
    PRIORITY_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "XAGUSD"]

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
