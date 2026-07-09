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
    # GOLD 단독 - AI 헤드 트레이더가 금만 거래
    # ※ 브로커 표기 확인 필수 (Market Watch에서 GOLD# 인지 GOLD 인지)
    PRIORITY_SYMBOLS = ["GOLD#"]

    # ── Claude AI 호출 정책 ─────────────────────────────────────
    # AI_CONFIRM_ENTRIES:
    #   True  = Pine BUY/SELL 신호를 AI가 최종 검증 + confidence로 lot 결정
    #   False = AI 생략, Pine 신호 즉시 실행 (크레딧 0)
    AI_CONFIRM_ENTRIES = True

    # AI_AUTONOMOUS (AI 헤드 트레이더 모드) - 활성:
    #   Pine은 매 봉 시장 데이터 보고만, 진입 판단은 Claude AI가 전담.
    #   비용: 15분봉 세션 기준 약 50회/일 ≈ 월 $10~15 크레딧
    #   ⚠ AI 재량 판단은 백테스트가 정의상 불가능 → 데모 실측이 유일한 검증.
    # ⚠ 데모 실행할 때만 True. False면 신호 봉에만 AI 호출(크레딧 절약).
    #   True면 세션 중 매 봉 호출(하루 ~50회) - 거래 안 해도 크레딧 소모됨.
    AI_AUTONOMOUS = False

    # AI 헤드 트레이더: 일일 거래 예산 (하루 4~5발)
    # 프로는 기회를 고른다 - 남은 총알 수가 AI에게 전달되어
    # "아껴 쏘는" 저격수 판단을 유도. 소진 시 그날 신규 진입 차단.
    MAX_TRADES_PER_DAY = 5

    # ── 데이트레이딩 설정 (당일 청산 / 세션 거래) ──────────────
    # 거래 세션 (UTC 기준). 런던+뉴욕 = 07:00~20:00
    TRADE_SESSION_START_HOUR = 7    # UTC
    TRADE_SESSION_END_HOUR = 20     # UTC
    # 데이트레이딩 규율: 세션(07-20 UTC)만 진입 + 20시 전 포지션 정리
    # (AI 자율 호출도 세션에만 발생 → 비용 통제)
    DAILY_FLATTEN_HOUR = 20         # UTC
    ENFORCE_DAY_CLOSE = True
    # 시간 손절 안전망: '손실 중인' 포지션만 대상 (승자는 예외 - 계속 태움)
    MAX_POSITION_MINUTES = 600

    # ── Equity Guard: 엣지 사망 감지기 ──────────────────────────
    # 국면 의존 전략의 프로 운용법: 전략이 언제 죽는지 시스템이 감지.
    # 최근 성적이 기준 아래로 떨어지면 자동매매를 자동 중단하고 알림.
    # (재개는 대시보드에서 수동으로 - 사람이 국면을 확인한 뒤)
    EQUITY_GUARD_WINDOW = 20          # 최근 N개 청산 거래로 평가
    EQUITY_GUARD_MIN_PF = 0.8         # 롤링 PF가 이 밑이면 중단
    EQUITY_GUARD_MAX_CONSEC_LOSS = 6  # 연속 손실 N회면 중단

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    LOG_FILE = "logs/trades.json"
    CLAUDE_MODEL = "claude-sonnet-4-6"
