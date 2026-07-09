import json
import logging
from typing import Dict, Any
from config import Config

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic 패키지 없음 - AI 분석 비활성화")


class ClaudeAI:
    """
    TradingView가 보낸 진입 신호(BUY/SELL)를 최종 검증하는 판단자.
    - 반환하는 confidence가 lot 크기를 직접 결정함 (RiskManager 등급표)
    - HOLD 봉에는 호출되지 않음 (크레딧 절약은 엔진이 담당)
    """

    def __init__(self):
        self.available = ANTHROPIC_AVAILABLE and bool(Config.ANTHROPIC_API_KEY)
        if self.available:
            try:
                self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            except Exception as e:
                logger.warning(f"Claude 클라이언트 초기화 실패: {e}")
                self.available = False
        self.model = Config.CLAUDE_MODEL

    def analyze_tradingview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """TradingView 지표 데이터를 종합해 최종 매매 판단 + 신뢰도 산정"""
        symbol = payload.get("symbol", Config.PRIORITY_SYMBOLS[0])
        price = payload.get("price", 0)

        # AI 미사용 시: Pine 신호 그대로, 기본 신뢰도
        if not self.available:
            action = str(payload.get("action", "HOLD")).upper()
            return {
                "action": action if action in ("BUY", "SELL") else "HOLD",
                "confidence": 0.65,
                "reasoning": "AI 미사용 - TradingView 신호 그대로 실행",
                "stop_loss": payload.get("sl", 0),
                "take_profit": payload.get("tp", 0),
            }

        prompt = f"""당신은 전설적 트레이더들의 원칙을 체화한 헤드 트레이더 AI입니다.
매 15분 시장 보고를 받고, 진입 여부를 스스로 결정합니다 (롱/숏 모두).
목표는 단 하나 - 기대값이 양수인 거래만 골라 수익을 쌓는 것.

═══ 당신의 교리 (실존 트레이더들의 검증된 원칙) ═══
· Paul Tudor Jones: "방어가 먼저다." 손익비 최소 1:2가 그려질 때만 진입.
  물타기 금지. 잃고 있는 아이디어에 두 번 베팅하지 않는다.
· Ed Seykota: 손실은 즉시 자르고 이익은 달리게 둔다.
  생존을 결정하는 것은 예측이 아니라 포지션 크기다.
· Jesse Livermore: 추세와 싸우지 마라. 확신이 없을 때 관망은
  포지션이다 - 돈은 기다림에서 벌린다.
· Stanley Druckenmiller: 확신이 높을 때만 크게 건다
  (당신의 confidence가 곧 베팅 크기다).

종목: {symbol}
현재가: {price}

═══ 시장 보고 (TradingView) ═══
{json.dumps(payload, ensure_ascii=False, indent=2)}

═══ 당신이 노릴 수 있는 셋업 (하루 4~5개 목표, 세션 전체에서) ═══
매 봉 종합 스냅샷을 받습니다. 아래 중 하나라도 명확하면 진입 후보입니다:
A) 레벨 돌파: 가격이 asian_high/low, prev_day_high/low, swing_high/low_20을
   추세(trend) 방향으로 돌파. orb_signal이 buy/sell이면 강한 신호.
B) 추세 되돌림: trend가 strong_up/up인데 dist_ema20_atr가 음수(눌림)→롱,
   strong_down/down인데 양수(반등)→숏. adx 20+ 이면 추세 유효.
C) VWAP 평균회귀: dist_vwap_atr가 ±2 이상 벌어졌다 되돌아올 때 역방향.
D) 모멘텀: momentum_1h_atr가 강하게(±1.5+) 추세 방향으로 터질 때 순방향.

═══ 판단 규칙 ═══
1. trades_left_today = 오늘 남은 총알(최대 5). 세션 13시간에 걸쳐
   좋은 셋업 4~5개를 고르게 잡되, 억지 진입은 하지 마세요.
   총알이 많이 남았고 셋업이 괜찮으면 적극적으로, 애매하면 HOLD.
2. 손익비: 손절 거리 대비 기대 이익 1.5배 이상 그려질 때만.
   stop_loss/take_profit을 반드시 제시 - atr을 참고해 SL은 보통
   1.5~2.5×atr, TP는 SL의 2배 이상 되게 레벨을 잡으세요.
3. 방향 정합: 롱은 trend가 up계열일 때, 숏은 down계열일 때 우선.
   역추세는 C(VWAP 회귀)처럼 명확한 근거가 있을 때만.
4. 회피 (함정): rsi 극단 추격(매수 80+/매도 20-) / adx 15 미만 무추세 /
   dist_ema20_atr가 4 이상인 과열 추격 / 세션 밖(in_session=false).
5. orb_signal/action은 참고일 뿐, 최종 결정과 SL·TP는 당신 것.
6. confidence로 크기 조절: 셋업 2개 이상 겹치면(예: 레벨돌파+모멘텀)
   0.8+, 단일 근거면 0.65~0.75. 자율 진입은 0.7 이상만 실행됩니다.
7. 데이터에 없는 필드는 무시. 애매하면 HOLD.

═══ confidence 보정 (중요: 이 값이 거래 크기를 직접 결정합니다) ═══
- confidence에 따라 lot이 커집니다: 0.65 미만=0.5배, 0.65~0.75=1배, 0.75~0.85=1.5배, 0.85~0.92=2배, 0.92+=3배
- 0.92 이상은 모든 지표가 완벽하게 일치하는 드문 경우에만 주세요 (남발 금지)
- "보장된 거래"는 존재하지 않습니다. 애매하면 confidence를 낮추는 것이 정답입니다.

다음 JSON 형식으로만 응답하세요:
{{
  "action": "BUY 또는 SELL 또는 HOLD",
  "confidence": 0.0~1.0,
  "reasoning": "결정 이유 (한국어, 1-2문장)",
  "stop_loss": 숫자,
  "take_profit": 숫자
}}"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            if not text.endswith("}"):
                text = text[:text.rfind('"')] + '"}'
            result = json.loads(text)
            if not result.get("stop_loss"):
                result["stop_loss"] = payload.get("sl", 0)
            if not result.get("take_profit"):
                result["take_profit"] = payload.get("tp", 0)
            return result
        except Exception as e:
            logger.error(f"TradingView 분석 오류: {e}")
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reasoning": f"AI 분석 오류 - 거래 보류: {str(e)[:40]}",
                "stop_loss": payload.get("sl", 0),
                "take_profit": payload.get("tp", 0),
            }
