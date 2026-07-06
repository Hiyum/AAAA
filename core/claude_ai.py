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

        prompt = f"""당신은 금(XAUUSD) 레짐 되돌림(Regime Fade) 전문 AI입니다.
이 전략은 10,665봉 실측 연구에서 도출됐습니다. 금의 본질:
- 레짐(일봉 20SMA 기준)이 전부다. 약세장에서 급등은 되돌아온다
  (실측: 급등 후 4시간 기대값 -0.62 ATR). 강세장은 미러.
- 함정 = 레짐 역행 추격 (약세장 급등 추격 롱이 최악의 손실 구간)
당신의 역할: 나쁜 신호 거부(HOLD) + 좋은 신호에 confidence로
힘 싣기 (confidence가 lot 크기 결정).

종목: {symbol}
현재가: {price}

═══ TradingView 데이터 ═══
{json.dumps(payload, ensure_ascii=False, indent=2)}

═══ 검증 기준 (레짐 되돌림) ═══
1. 신호가 레짐과 일치하는가: regime=bear면 SELL만, bull이면 BUY만 정상.
   역행 신호는 거부.
2. stretch_atr: 스트레치가 클수록(1.0~2.5) 되돌림 여력 큼 → 가산.
   3.0 이상 극단은 뉴스 폭주 가능성 → 감산 또는 HOLD.
3. 가격이 daily_ma에서 이미 크게 먼 상태의 추가 fade는 신중히.
4. rsi가 신호 방향을 지지(매도인데 70+, 매수인데 30-)하면 가산.
5. ATR 대비 비정상 스파이크 캔들(뉴스)이면 HOLD.
6. 참고: 검증 데이터가 약세장 구간이라 bear+SELL은 실측 검증됨(WR 71%).
   bull+BUY(미러)는 논리적 대칭이나 미검증 → confidence 상한 0.8.
7. 데이터에 없는 필드는 무시.
8. autonomous=true인 경우: 당신이 유일한 판단자. 피처가 명확하면
   HOLD 봉에서도 스스로 진입 결정 가능 (자율 진입은 confidence 0.7+).
   명확하지 않으면 반드시 HOLD.
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
