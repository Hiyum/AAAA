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
    # GOLD 단일 집중 (Trend Rider v6)
    # ※ 브로커 표기 확인 필수 (GOLD# 인지 GOLD 인지 Market Watch에서)
    PRIORITY_SYMBOLS = ["GOLD#"]

    # ── Claude AI 호출 정책 ─────────────────────────────────────
    # True  = Pine이 BUY/SELL 신호를 보낼 때만 AI가 최종 검증 + 신뢰도 산정
    # False = AI 검증 생략, Pine 신호 즉시 실행 (크레딧 0, 지연 0)
    #
    # ※ 현재 False인 이유 (크레딧 손익분기 계산):
    #   $100 계좌의 거래당 기대이익 ~$0.3 < AI 호출 비용
    #   → 이 규모에선 AI 검증이 본전을 못 찾음 (사용자 지적이 맞음)
    #   계좌가 $1,000+ 로 크면 True로 되돌리는 것을 권장
    AI_CONFIRM_ENTRIES = False

    # ── 데이트레이딩 설정 (당일 청산 / 세션 거래) ──────────────
    # 거래 세션 (UTC 기준). 런던+뉴욕 = 07:00~20:00
    TRADE_SESSION_START_HOUR = 7    # UTC
    TRADE_SESSION_END_HOUR = 20     # UTC
    # 승자 오버나이트 정책 (Trend Rider v6):
    #  - 서버 정시 청산 없음 (Pine이 20:00 GMT에 '패자만' CLOSE_ALL 전송)
    #  - 승자(본전 잠금)는 며칠이고 트레일링으로 계속 보유
    DAILY_FLATTEN_HOUR = 20         # UTC (Pine 판정 시각과 동일)
    ENFORCE_DAY_CLOSE = False
    # 시간 손절 안전망: '손실 중인' 포지션만 대상 (승자는 예외 - 계속 태움)
    MAX_POSITION_MINUTES = 600

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
