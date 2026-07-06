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
    # 포트폴리오 운용: 검증된 두 전략을 동시에
    #  - GOLD#  : GOLD ORB v5 (12개월 검증, 연 +32.8%, PF 1.155)
    #  - AUDUSD : DayTrade v4.1 (3개월 검증, PF 1.248)
    # ※ 브로커 표기 확인 필수 (GOLD# / GOLD, AUDUSD / AUDUSD# 등)
    PRIORITY_SYMBOLS = ["GOLD#", "AUDUSD"]

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
    # 당일 청산 시각 (UTC). 이 시각에 모든 포지션 자동 청산 (금 오버나이트 갭 방지)
    DAILY_FLATTEN_HOUR = 20         # UTC
    ENFORCE_DAY_CLOSE = True
    # 포지션 최대 보유 시간(분): 초과 시 서버가 직접 청산 (독립 안전망)
    # 돌파 추세는 몇 시간을 태워야 하므로 넉넉히 (20:00 UTC 청산이 상한)
    MAX_POSITION_MINUTES = 600

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
