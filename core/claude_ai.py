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
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            # 불완전한 JSON 복구 시도
            if not text.endswith("}"):
                text = text[:text.rfind('"')] + '"}'  # 마지막 완전한 필드까지만 사용
            return json.loads(text)
        except json.JSONDecodeError:
            # JSON 파싱 실패 시 텍스트에서 action 키워드 직접 추출
            import re
            text_lower = text.lower() if 'text' in dir() else ""
            if '"action": "close"' in text_lower or "'close'" in text_lower:
                return {"action": "CLOSE", "reason": "AI 응답 파싱 오류 - CLOSE 감지"}
            if '"action": "move_sl"' in text_lower:
                return {"action": "HOLD", "reason": "AI 응답 파싱 오류 - 홀드 유지"}
            return {"action": "HOLD", "reason": "AI 응답 파싱 오류 - 홀드 유지"}
        except Exception as e:
            logger.error(f"포지션 관리 판단 오류: {e}")
            return {"action": "HOLD", "reason": f"AI 오류 - 홀드 유지: {str(e)[:30]}"}

    def analyze_tradingview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        TradingView webhook으로 받은 지표 데이터를 종합 분석.
        Market Structure + Volume Profile + Liquidity Sweep + CVD + VWAP 등.
        Claude AI가 맥락을 이해해서 최종 매매 판단을 내림.
        """
        symbol = payload.get("symbol", "GOLD#")
        price = payload.get("price", 0)

        # AI 없으면 TradingView가 보낸 action을 그대로 사용
        if not self.available:
            action = str(payload.get("action", "HOLD")).upper()
            return {
                "action": action if action in ("BUY", "SELL") else "HOLD",
                "confidence": 0.6,
                "reasoning": "AI 미사용 - TradingView 신호 그대로 실행",
                "stop_loss": payload.get("sl", 0),
                "take_profit": payload.get("tp", 0),
            }

        prompt = f"""당신은 외환 데이트레이딩 전문 트레이더 AI입니다.
당일 청산(오버나이트 금지) 원칙으로 운용하며, 추세 방향의 눌림목 진입을 노립니다.
TradingView에서 계산한 아래 지표들을 종합하여 최종 매매 결정을 내려주세요.

종목: {symbol}
현재가: {price}

═══ TradingView 분석 데이터 ═══
{json.dumps(payload, ensure_ascii=False, indent=2)}

═══ 분석 기준 (데이트레이딩) ═══
1. 추세 방향(market_structure): bullish면 매수만, bearish면 매도만 고려 (역추세 금지)
2. EMA 정배열(ema_fast vs ema_slow): 추세 방향 확인
3. ADX: 20 이상이어야 추세 유효 (낮으면 횡보 → HOLD)
4. RSI: 추세 방향으로의 되돌림(눌림목) 후 재개 시점인지
5. 세션(in_session): false면 거래 시간 밖 → HOLD
6. 지표들이 일치할수록 강한 신호. 애매하면 보수적으로 HOLD.

다음 JSON 형식으로만 응답하세요:
{{
  "action": "BUY 또는 SELL 또는 HOLD",
  "confidence": 0.0~1.0,
  "reasoning": "결정 이유 (한국어, 2-3문장, 추세/ADX/RSI 근거)",
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
            # 안전장치: SL/TP 없으면 payload 값 또는 기본값
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

    def manage_position_tv(self, symbol: str, action: str, entry_price: float,
                           current_price: float, sl: float, tp: float,
                           profit: float, tv_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        TradingView 지표 데이터로 열린 포지션을 주시.
        MT5 차트가 아니라 TradingView가 보낸 지표(구조/CVD/스윕/VWAP 등)로 판단.
        """
        pnl_pct = ((current_price - entry_price) / entry_price * 100)
        if action == "SELL":
            pnl_pct = -pnl_pct

        # AI 없으면 기본 규칙
        if not self.available:
            if pnl_pct > 0.5 and action == "BUY" and sl < entry_price:
                return {"action": "MOVE_SL", "new_sl": round(entry_price, 5),
                        "reason": "수익 발생 - 손절 본전 이동"}
            return {"action": "HOLD", "reason": f"포지션 유지 | 손익 {pnl_pct:+.2f}%"}

        prompt = f"""당신은 열린 포지션을 관리하는 기관급 트레이더 AI입니다.
MT5 차트가 아니라 TradingView가 실시간 계산해 보낸 아래 지표로 판단하세요.

═══ 현재 포지션 ═══
- 종목: {symbol}
- 방향: {action}
- 진입가: {entry_price}
- 현재가: {current_price}
- 손절가: {sl}
- 목표가: {tp}
- 손익: ${profit:.2f} ({pnl_pct:+.2f}%)

═══ TradingView 실시간 지표 ═══
{json.dumps(tv_data, ensure_ascii=False, indent=2)}

판단 기준:
- 진입 방향과 시장 구조(market_structure)/CVD(cvd)가 여전히 일치하면 유지(HOLD)
- 구조가 반대로 전환되거나 CVD가 역전되면 청산(CLOSE) 고려
- 수익 중이고 추세 유효하면 손절을 본전/유리한 쪽으로 이동(MOVE_SL)

다음 중 하나를 JSON으로만 응답:
1. {{"action": "HOLD", "reason": "이유"}}
2. {{"action": "CLOSE", "reason": "이유"}}
3. {{"action": "MOVE_SL", "new_sl": 숫자, "reason": "이유"}}"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            if not text.endswith("}"):
                text = text[:text.rfind('"')] + '"}'
            return json.loads(text)
        except Exception as e:
            logger.error(f"TV 포지션 주시 오류: {e}")
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
