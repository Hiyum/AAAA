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

        prompt = f"""당신은 금(XAUUSD) 스마트머니(SMC) 트레이딩 전문 AI입니다.
Pine이 7개 합류점(Market Structure/BOS/Liquidity Sweep/CVD/Order Flow/VWAP/POC)
점수제로 신호를 보냈습니다. 당신의 역할: 나쁜 신호 거부(HOLD) +
좋은 신호에 confidence로 힘 싣기 (confidence가 lot 크기 결정).

종목: {symbol}
현재가: {price}

═══ TradingView SMC 피처 데이터 ═══
{json.dumps(payload, ensure_ascii=False, indent=2)}

═══ 검증 기준 (SMC 합류점) ═══
1. score(합류점 수)가 6~7이면 강한 신호 → confidence 상향. 4~5는 보통.
2. market_structure와 bos가 신호 방향 일치 → 가산. 반대 구조면 강감산.
3. liquidity_sweep이 신호 방향(매수=bullish 스윕)이면 최상급 셋업 → 가산.
   (스윕 후 진입 = 기관이 유동성 잡고 반전하는 자리)
4. cvd와 order_flow(-1~1)가 방향 일치 → 가산. 역행(매수인데 flow 음수) → 감산.
5. 가격 vs vwap/poc: 매수는 위, 매도는 아래가 정상. 크게 괴리(추격)면 감산.
6. ATR 대비 비정상 스파이크 캔들 → HOLD.
7. in_session=false → HOLD.
8. 참고: 이 시장 데이터에서 롱은 역사적으로 약했음(PF 0.6~0.8).
   롱은 더 엄격하게, 숏은 기준 충족 시 과감하게.
9. 애매하면 confidence를 낮추는 것이 정답.

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
