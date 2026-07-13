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

    # ── 소액 계좌 안전장치 (리스크 수학 붕괴 방지) ──────────────
    # 브로커 최소 lot(0.01) 강제 상향으로 실효 리스크가 하드캡을 넘으면
    # 그 거래는 '조용히 초과 리스크를 지는' 대신 진입 자체를 거부한다.
    REJECT_IF_MIN_LOT_EXCEEDS_CAP = True

    # ── Max Drawdown 하드캡 (계좌 생존의 최후 방어선) ───────────
    # 피크 자산 대비 이만큼 빠지면: 전 포지션 청산 + 대기주문 취소 + 자동매매 중단
    MAX_DRAWDOWN_PCT = 0.25

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
    AI_AUTONOMOUS = False

    # AI 헤드 트레이더: 일일 거래 예산
    MAX_TRADES_PER_DAY = 6

    # ── 지정가 래더 진입 (스프레드 역이용 — 시장가 폐기) ────────
    # 페이드 진입은 임펄스 '반대편'이므로, 신호가보다 더 유리한 가격에
    # 지정가를 깔아 스프레드/슬리피지를 지불하는 쪽에서 받는 쪽으로 전환.
    # 오프셋은 ATR 배수: SELL이면 신호가 + 오프셋, BUY면 신호가 - 오프셋.
    LIMIT_LADDER_ENABLED = True
    LIMIT_LADDER_OFFSETS = [0.30, 0.70]   # ATR 배수 (rung 1, rung 2)
    LIMIT_LADDER_WEIGHTS = [0.6, 0.4]     # 물량 배분
    LIMIT_TTL_MINUTES = 30                # 미체결 대기주문 자동 취소 (2봉)

    # ── 스프레드 필터 ───────────────────────────────────────────
    # 진입 판단 직전 실측 스프레드가 (ATR × 이 배수)를 넘으면 진입 보류.
    # 임펄스 직후 스프레드 폭발 구간에서의 진입을 기계적으로 차단.
    MAX_SPREAD_ATR_MULT = 0.15

    # ── 피라미딩 ────────────────────────────────────────────────
    # 단순 '수익 중'이 아니라: 같은 방향의 완전히 새로운 신호 + AI 고신뢰
    # + 기존 포지션이 1R 이상 유리할 때만 1회 증량 허용.
    PYRAMID_ENABLED = True
    PYRAMID_MAX_UNITS = 2          # 기본 1 + 증량 1
    PYRAMID_MIN_CONFIDENCE = 0.80
    PYRAMID_MIN_OPEN_R = 1.0       # 기존 포지션 최소 유리폭 (R)
    PYRAMID_SIZE_RATIO = 0.5       # 증량 물량 = 기본 물량 × 이 비율

    # ── 세션/보유 정책 ──────────────────────────────────────────
    # 거래 세션 (UTC 기준). 신규 '진입'만 제한. 보유는 시간 제한 없음.
    TRADE_SESSION_START_HOUR = 7    # UTC
    TRADE_SESSION_END_HOUR = 20     # UTC
    # 당일 강제 청산: 폐기됨(#10 지침). 보유 기간은 시장 구조가 결정.
    # True로 되돌리면 이전처럼 매일 DAILY_FLATTEN_HOUR에 전량 청산.
    ENFORCE_DAY_CLOSE = False
    DAILY_FLATTEN_HOUR = 20         # UTC (ENFORCE_DAY_CLOSE=True일 때만)
    # 시간 손절: 폐기됨(#10 지침). 0 = 비활성. 시장 구조/SL이 청산을 결정.
    MAX_POSITION_MINUTES = 0

    # ── 실시간 감시 (이벤트 기반 AI 개입) ──────────────────────
    # 포지션 보유 중 엔진이 5초 간격으로 틱을 추적하다가
    # 60초 내 가격 변화가 (ATR × 이 배수)를 넘으면 AI에게 즉시 개입 요청.
    FAST_MONITOR_INTERVAL_SEC = 5
    INTERVENTION_MOVE_ATR = 1.2
    INTERVENTION_COOLDOWN_SEC = 600   # AI 개입 호출 최소 간격 (크레딧 보호)

    # ── Equity Guard: 엣지 사망 감지기 ──────────────────────────
    EQUITY_GUARD_WINDOW = 20          # 최근 N개 청산 거래로 평가
    EQUITY_GUARD_MIN_PF = 0.8         # 롤링 PF가 이 밑이면 중단
    EQUITY_GUARD_MAX_CONSEC_LOSS = 6  # 연속 손실 N회면 중단

    # ── 자가 발전 (Self-Review / Self-Improvement) ─────────────
    AI_TRADE_REVIEW = True            # 거래 종료 시 AI 자동 리뷰 생성
    SELF_IMPROVE_EVERY_N_TRADES = 10  # N건 청산마다 개선 프롬프트 자동 생성
    REPORTS_DIR = "reports"

    # ── 저장소 ──────────────────────────────────────────────────
    DB_FILE = "logs/trading.db"       # SQLite (거래/신호/자산곡선 영구 저장)
    LOG_FILE = "logs/trades.json"     # 구버전 원장 (DB로 1회 마이그레이션 후 미사용)

    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    CLAUDE_MODEL = "claude-sonnet-4-6"
