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

═══ 판단 규칙 ═══
1. trades_left_today = 오늘 남은 총알. 저격수처럼 아껴 쏘세요.
   총알이 적게 남을수록 기준을 더 높이세요. 억지 진입은 최악의 죄악.
2. 진입은 명확한 셋업이 보일 때만:
   - 레인지/레벨 돌파가 추세(market_structure) 방향과 일치
   - 또는 추세 방향으로의 되돌림 완료 지점
   - 손절 거리(sl 참고) 대비 기대 이익이 1.5배 이상 그려질 때
3. mode=GOLD_ORB 보고: 아시안 레인지(asian_high/low) 돌파 맥락.
   range_atr_ratio 1~3 정상 / 4+ 과열. mode=DAYTRADE(AUDUSD):
   rsi2 극단 평균회귀 - 실측상 롱이 강함(WR 77%), 숏은 신중히.
4. 회피 (함정 목록): 뉴스 스파이크성 비정상 캔들 / rsi 극단 추격
   (매수 80+, 매도 20-) / 방향 근거가 지표 1개뿐인 진입 / 세션 밖.
5. Pine의 action이 BUY/SELL이면 참고 의견일 뿐, 최종 결정은 당신 것.
   HOLD 봉에서도 셋업이 명확하면 스스로 진입 가능 (자율 진입은
   confidence 0.7 이상일 때만 실행됨).
6. 데이터에 없는 필드는 무시. 애매하면 HOLD - 내일도 시장은 열린다.

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
