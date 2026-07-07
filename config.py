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
    # 포트폴리오: 검증된 두 구성 동시 가동 (합산 ~4.4회/일)
    #  - AUDUSD: DayTrade v4.1 (실측 4.1회/일, WR 77%, PF 1.248)
    #  - GOLD# : ORB v5 셋업A (실측 0.3회/일, WR 70%, PF 1.909)
    # ※ 브로커 표기 확인 필수 (Market Watch에서)
    PRIORITY_SYMBOLS = ["GOLD#", "AUDUSD"]

    # ── Claude AI 호출 정책 ─────────────────────────────────────
    # AI_CONFIRM_ENTRIES:
    #   True  = Pine BUY/SELL 신호를 AI가 최종 검증 + confidence로 lot 결정
    #   False = AI 생략, Pine 신호 즉시 실행 (크레딧 0)
    AI_CONFIRM_ENTRIES = True

    # AI_AUTONOMOUS (절대 권한 모드):
    #   True  = Pine 신호와 무관하게, 세션 중 매 봉의 지표 데이터를 AI가 보고
    #           스스로 진입 결정 (Pine은 데이터 공급자로 격하)
    #   비용: 15분봉 세션 기준 약 40회/일 ≈ 월 $10~15 크레딧
    #   ⚠ 경고: AI 자율 판단은 백테스트가 '정의상 불가능' →
    #           엣지를 사전 검증할 방법이 없음. 데모에서 규칙 기반과
    #           A/B 비교 후 사용 여부를 결정할 것.
    AI_AUTONOMOUS = False

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
