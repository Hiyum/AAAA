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

        prompt = f"""당신은 금(XAUUSD) 추세 트레이딩 전문 AI입니다 (Trend Rider).
전략: ① 아시안 레인지 돌파(ORB) ② 추세 눌림목 재돌파(PULLBACK) 두 가지로 진입,
승자는 트레일링으로 며칠이고 태우고 패자는 당일 자릅니다.
Pine이 이미 신호(action)를 보냈고, 당신의 역할은 나쁜 신호를 거르고(HOLD)
좋은 신호에 힘을 실어주는 것(높은 confidence → 큰 lot)입니다.

종목: {symbol}
현재가: {price}

═══ TradingView 분석 데이터 ═══
{json.dumps(payload, ensure_ascii=False, indent=2)}

═══ 검증 기준 (금 추세) ═══
1. 기본적으로 Pine 신호(action)를 존중. 확실한 반대 근거가 있을 때만 HOLD.
2. market_structure가 신호 방향과 일치(매수=bullish/매도=bearish) → 가산.
   불일치면 강하게 감산 (역추세 진입은 이 전략의 손실 주범)
3. adx 25+ = 추세 강함 → 가산 / 15 미만 = 횡보 → 감산 또는 HOLD
4. entry_type=ORB: 돌파 방향이 아시안 레인지에서 자연스러운지
   entry_type=PULLBACK: 추세 지속 국면인지 (adx와 함께 판단)
5. rsi 극단(매수인데 80+, 매도인데 20-)이면 추격 위험 → 감산
6. ATR 대비 비정상 단일 캔들(뉴스 스파이크)이면 HOLD
7. in_session=false면 HOLD
8. 애매하면 confidence를 낮추는 것이 정답입니다.

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
