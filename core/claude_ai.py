import json
import logging
from typing import List, Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic 패키지 없음 - AI 분석 비활성화")


class ClaudeAI:

    def __init__(self):
        self.available = ANTHROPIC_AVAILABLE and bool(Config.ANTHROPIC_API_KEY)
        if self.available:
            try:
                self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            except Exception as e:
                logger.warning(f"Claude 클라이언트 초기화 실패: {e}")
                self.available = False
        self.model = Config.CLAUDE_MODEL

    def analyze_market(self, symbol: str, market_summary: Dict[str, Any],
                       signal_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available:
            return {
                "action": signal_data.get("action", "HOLD"),
                "confidence": signal_data.get("confidence", 0.5),
                "reasoning": "AI 분석 불가 (API 키 미설정) - 전략 신호 사용",
                "risk_assessment": "보통",
            }

        prompt = f"""당신은 전문 스캘핑 트레이더 AI입니다. 다음 시장 데이터를 분석하고 매매 결정을 내려주세요.

종목: {symbol}
현재 가격: {market_summary.get('current_price')}
24h 변화율: {market_summary.get('change_pct', 0):.2f}%
거래량: {market_summary.get('volume', 'N/A')}
변동성(ATR): {market_summary.get('atr', 'N/A')}

전략 신호: {signal_data.get('action')}
전략 신뢰도: {signal_data.get('confidence', 0):.0%}
전략 이유: {signal_data.get('reason')}
제안 진입가: {signal_data.get('entry_price')}
제안 손절가: {signal_data.get('stop_loss')}
제안 목표가: {signal_data.get('take_profit')}

다음 JSON 형식으로만 응답하세요:
{{
  "action": "BUY 또는 SELL 또는 HOLD",
  "confidence": 0.0~1.0,
  "reasoning": "결정 이유 (한국어, 2-3문장)",
  "risk_assessment": "낮음 또는 보통 또는 높음",
  "entry_price": 숫자,
  "stop_loss": 숫자,
  "take_profit": 숫자
}}"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            # JSON 블록 추출
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Claude 분석 오류: {e}")
            return {
                "action": signal_data.get("action", "HOLD"),
                "confidence": signal_data.get("confidence", 0.5),
                "reasoning": f"AI 분석 오류 - 전략 신호 사용: {str(e)[:50]}",
                "risk_assessment": "보통",
                "entry_price": signal_data.get("entry_price", 0),
                "stop_loss": signal_data.get("stop_loss", 0),
                "take_profit": signal_data.get("take_profit", 0),
            }

    def select_best_symbol(self, symbols: List[str], market_data: Dict[str, Any]) -> str:
        if not self.available or not symbols:
            return symbols[0] if symbols else "XAUUSD"

        summaries = []
        for sym in symbols:
            data = market_data.get(sym, {})
            summaries.append(f"- {sym}: 변화율 {data.get('change_pct', 0):.2f}%, ATR {data.get('atr', 'N/A')}, 거래량 {data.get('volume', 'N/A')}")

        prompt = f"""스캘핑 트레이더로서 오늘 가장 좋은 거래 기회를 가진 종목을 선택하세요.

후보 종목:
{chr(10).join(summaries)}

종목 이름만 응답하세요 (예: XAUUSD)"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}]
            )
            selected = response.content[0].text.strip().upper()
            return selected if selected in symbols else symbols[0]
        except Exception as e:
            logger.error(f"종목 선택 오류: {e}")
            return symbols[0]

    def manage_position(self, symbol: str, action: str, entry_price: float,
                        current_price: float, sl: float, tp: float,
                        profit: float, df) -> Dict[str, Any]:
        """열린 포지션을 분석해서 홀드/청산/손절이동 결정"""

        pnl_pct = ((current_price - entry_price) / entry_price * 100)
        if action == "SELL":
            pnl_pct = -pnl_pct

        # AI 없으면 기본 규칙으로 판단
        if not self.available:
            # 수익 50% 이상 달성 시 손절을 진입가로 이동 (본전 보호)
            if pnl_pct > 0.5 and action == "BUY" and sl < entry_price:
                return {"action": "MOVE_SL", "new_sl": round(entry_price, 5),
                        "reason": "수익 발생 - 손절을 진입가로 이동 (본전 보호)"}
            # 시장이 완전히 반전된 경우 조기 청산
            recent_close = float(df['close'].iloc[-1])
            if action == "BUY" and recent_close < sl * 1.002:
                return {"action": "CLOSE", "reason": "손절가 근접 - 조기 청산"}
            if action == "SELL" and recent_close > sl * 0.998:
                return {"action": "CLOSE", "reason": "손절가 근접 - 조기 청산"}
            return {"action": "HOLD", "reason": f"포지션 유지 중 | 손익: {pnl_pct:+.2f}%"}

        # 최근 캔들 요약
        recent = df.tail(5)[['open', 'high', 'low', 'close']].round(5).to_dict('records')

        prompt = f"""당신은 열린 포지션을 관리하는 전문 트레이더 AI입니다.

현재 포지션:
- 종목: {symbol}
- 방향: {action}
- 진입가: {entry_price}
- 현재가: {current_price}
- 손절가: {sl}
- 목표가: {tp}
- 현재 손익: ${profit:.2f} ({pnl_pct:+.2f}%)

최근 5개 캔들 (5분봉):
{json.dumps(recent, ensure_ascii=False)}

다음 중 하나를 JSON으로만 응답하세요:

1. 포지션 유지:
{{"action": "HOLD", "reason": "이유"}}

2. 지금 청산:
{{"action": "CLOSE", "reason": "이유"}}

3. 손절가 이동 (수익 보호):
{{"action": "MOVE_SL", "new_sl": 숫자, "reason": "이유"}}"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"포지션 관리 판단 오류: {e}")
            return {"action": "HOLD", "reason": f"AI 오류 - 홀드 유지: {str(e)[:30]}"}

    def summarize_performance(self, trades: List[Dict]) -> str:
        if not self.available or not trades:
            return "거래 내역 없음"

        total = len(trades)
        wins = sum(1 for t in trades if t.get("profit", 0) > 0)
        total_profit = sum(t.get("profit", 0) for t in trades)

        prompt = f"""다음 트레이딩 성과를 분석하고 개선점을 제안해주세요.

총 거래: {total}회
승률: {wins/total*100:.1f}% ({wins}승 {total-wins}패)
총 손익: ${total_profit:.2f}

최근 거래 내역:
{json.dumps(trades[-5:], ensure_ascii=False, indent=2)}

한국어로 3-4문장으로 분석해주세요."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            return f"성과 분석 오류: {str(e)[:50]}"
