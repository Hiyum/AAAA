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
    # ※ 브로커 표기 확인 필수 (AUDUSD / AUDUSD# 등)
    PRIORITY_SYMBOLS = ["AUDUSD"]

    # ── Claude AI 호출 정책 ─────────────────────────────────────
    # True  = Pine이 BUY/SELL 신호를 보낼 때만 AI가 최종 검증 + 신뢰도 산정
    #         (신뢰도가 lot 크기 결정. HOLD 봉은 AI 호출 안 함 → 크레딧 절약)
    # False = AI 검증 생략, Pine 신호 즉시 실행 (크레딧 0, 지연 0)
    #         단, 이 경우 lot은 기본 신뢰도(0.7) 기준으로 계산됨
    AI_CONFIRM_ENTRIES = True

    # ── 데이트레이딩 설정 (당일 청산 / 세션 거래) ──────────────
    # 거래 세션 (UTC 기준). 런던+뉴욕 = 07:00~20:00
    TRADE_SESSION_START_HOUR = 7    # UTC
    TRADE_SESSION_END_HOUR = 20     # UTC
    # 당일 청산 시각 (UTC). 이 시각에 모든 포지션 자동 청산
    DAILY_FLATTEN_HOUR = 20         # UTC
    # False = 24시간 거래 (세션 차단/정시 청산 없음)
    # 포지션 장기화는 MAX_POSITION_MINUTES 시간손절이 대신 막음
    ENFORCE_DAY_CLOSE = False
    # 포지션 최대 보유 시간(분): 초과 시 서버가 직접 청산
    # (TradingView/ngrok 신호가 끊겨도 작동하는 독립 안전망. 15분봉 10봉 = 150분)
    MAX_POSITION_MINUTES = 150

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
