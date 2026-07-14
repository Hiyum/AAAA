import json
import logging
from typing import Dict, Any, List
from config import Config

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic 패키지 없음 - AI 분석 비활성화")


def _extract_json(text: str) -> dict:
    """모델 응답에서 JSON 복원 (코드펜스/절단 대응)"""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.replace("json", "", 1).strip()
            if p.startswith("{"):
                text = p
                break
    start = text.find("{")
    if start > 0:
        text = text[start:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 절단 복구: 마지막 완결 지점까지 자르고 닫기
        if not text.endswith("}"):
            cut = text[:text.rfind('"') + 1]
            for suffix in ('"}', '"]}', '"}]}'):
                try:
                    return json.loads(cut + suffix)
                except json.JSONDecodeError:
                    continue
        raise


class ClaudeAI:
    """
    시장 구조를 종합 판단하는 헤드 트레이더 AI.
    - analyze_tradingview : 매 판단 시 MTF+오더플로우 종합 → 투명성 보고서 + 결정
    - intervention_check  : 보유 중 급변 감지 시 즉시 개입 판단 (경량/저지연)
    - review_trade        : 거래 종료 후 셀프 리뷰 (#12)
    - generate_improvement_prompt : 누적 DB 기반 개선 프롬프트 자동 생성 (#14)
    """

    def __init__(self):
        self.unavailable_reason = ""
        if not ANTHROPIC_AVAILABLE:
            self.unavailable_reason = "anthropic 패키지 없음 → 해결: pip install anthropic"
        elif not Config.ANTHROPIC_API_KEY:
            self.unavailable_reason = ("ANTHROPIC_API_KEY가 비어 있음 → 해결: 프로젝트 폴더의 "
                                       ".env 확인 + 그 폴더에서 python app.py 실행")
        self.available = not self.unavailable_reason
        if self.available:
            try:
                self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            except Exception as e:
                logger.warning(f"Claude 클라이언트 초기화 실패: {e}")
                self.unavailable_reason = f"클라이언트 초기화 실패: {e}"
                self.available = False
        self.model = Config.CLAUDE_MODEL

    def _call(self, prompt: str, max_tokens: int = 900) -> str:
        response = self.client.messages.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
        return response.content[0].text

    # ── ① 종합 시장 판단 ────────────────────────────────────

    def analyze_tradingview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = payload.get("symbol", Config.PRIORITY_SYMBOLS[0])
        price = payload.get("price", 0)

        if not self.available:
            action = str(payload.get("action", "HOLD")).upper()
            return {
                "action": action if action in ("BUY", "SELL") else "HOLD",
                "confidence": 0.65,
                "reasoning": "AI 미사용 - TradingView 신호 그대로 실행",
                "stop_loss": payload.get("sl", 0),
                "take_profit": payload.get("tp", 0),
            }

        prompt = f"""당신은 시장 구조를 종합 판단하는 헤드 트레이더 AI입니다.
당신의 유일한 목표는 장기 기대값(EV)과 생존율의 극대화입니다.

═══ 교리 (승률이 아니라 EV) ═══
· 손실은 사업 비용이다. 무손실 추구는 파산으로 가는 지름길이며,
  좋은 진입을 손절당하는 것은 실수가 아니다.
· Paul Tudor Jones: 방어 먼저. 손익비 최소 1:1.5가 그려질 때만 진입.
· Ed Seykota: 손실은 즉시 자르고 이익은 구조가 깨질 때까지 태운다.
· Druckenmiller: 확신이 높을 때만 크게 (confidence = 베팅 크기).
· 애매하면 HOLD. 관망도 포지션이다.

═══ 다중 시간 프레임 해석법 (payload의 mtf 객체) ═══
· d/h4 = 메인 시장 구조(대세). h1/m30 = 현재 추세와 지속 여부.
· 15분 신호가 상위 구조와 정렬되면 confidence를 올리고,
  역행하면 근거가 명확할 때만(청소 후 반전 등) 낮은 confidence로.
· 상위/하위 프레임이 서로 모순되면 그것 자체가 HOLD 신호다.

═══ 오더플로우 해석법 (payload의 of 객체 — 1분 분해 프록시) ═══
· delta(매수량-매도량)와 가격의 다이버전스: 가격은 신고가인데 delta 감소
  → 매수 소진, 페이드 근거 강화.
· cvd_slope: 세션 누적 델타의 방향. 진입 방향과 일치하면 가점.
· sweep: 직전 스윙 고/저점을 꼬리로 청소(liquidity sweep)한 직후의
  역방향 진입은 최상급 셋업.
· fvg: 미충전 Fair Value Gap은 자석이다. TP/되돌림 목표로 활용.
· vwap: 가격이 vwap에서 크게 이탈하면 회귀 압력. 진입/TP 근거.
· 이 데이터는 1분봉 분해 기반 프록시다. 절대값이 아니라 방향·다이버전스만 믿어라.

═══ 핵심 통계 (576일 실측) ═══
금 15분봉에서 임펄스 추격은 PF 0.5로 죽고, 페이드는 PF 1.16으로 산다.
Pine의 impulse=up이면 SELL, down이면 BUY가 기본 가설. 단 body_atr>3.0은
뉴스 폭주 가능성 → 신중히. 상위 프레임이 강추세면 페이드를 건너뛰어라
(임펄스 '지속' 국면에서 페이드는 연속 손절된다).

═══ 판단 규칙 ═══
1. trades_left_today = 남은 총알. 억지 진입 금지, 좋은 셋업만.
2. 진입은 지정가 래더로 실행된다: entry_zone(진입 희망 구간)을 제시하면
   엔진이 그 구간에 지정가를 깐다. SELL이면 현재가 위, BUY면 아래.
3. stop_loss는 가격이 아니라 '논리'가 깨지는 지점: 최근 스윙 고/저
   (payload의 swing_hi/swing_lo) 바깥 + ATR 여유. 노이즈에 안 닿게.
4. take_profit: 구조 목표(vwap, fvg, 반대편 스윙). 손익비 1.5 미만이면 HOLD.
5. confidence가 lot을 결정: 0.65미만=0.5배, 0.65~0.75=1배, 0.75~0.85=1.5배,
   0.85~0.92=2배, 0.92+=3배(모든 프레임+오더플로우 완벽 정렬시에만).
6. 보장된 거래는 없다. 애매하면 confidence를 낮춰라.

종목: {symbol} / 현재가: {price}

═══ 시장 보고 (TradingView) ═══
{json.dumps(payload, ensure_ascii=False, indent=1)}

다음 JSON 형식으로만 응답하세요 (투명성 보고서 필수 — 전 필드 한국어).
각 필드는 간결하게: market_analysis 2문장 이내, 근거/위험 각 항목 1줄,
반드시 reasoning까지 완결된 JSON을 출력하세요:
{{
  "market_analysis": "현재 시장 구조 종합 (2-3문장: MTF 정렬 상태, 오더플로우 상태)",
  "key_evidence": ["근거1", "근거2", "근거3"],
  "risk_factors": ["위험1", "위험2"],
  "long_scenario": "롱 시나리오와 성립 조건 (1문장)",
  "short_scenario": "숏 시나리오와 성립 조건 (1문장)",
  "action": "BUY 또는 SELL 또는 HOLD",
  "confidence": 0.0,
  "entry_zone": {{"from": 0, "to": 0}},
  "stop_loss": 0,
  "take_profit": 0,
  "reasoning": "최종 판단 이유 (1-2문장)"
}}"""

        try:
            # 투명성 보고서(한국어)가 길어 900토큰이면 절단됨 → 여유 있게
            result = _extract_json(self._call(prompt, max_tokens=2000))
            if not result.get("stop_loss"):
                result["stop_loss"] = payload.get("sl", 0)
            if not result.get("take_profit"):
                result["take_profit"] = payload.get("tp", 0)
            return result
        except Exception as e:
            logger.error(f"TradingView 분석 오류: {e}")
            return {
                "action": "HOLD", "confidence": 0.0,
                "reasoning": f"AI 분석 오류 - 거래 보류: {str(e)[:60]}",
                "stop_loss": payload.get("sl", 0),
                "take_profit": payload.get("tp", 0),
            }

    # ── ② 보유 중 긴급 개입 (이벤트 기반, #5) ───────────────

    def intervention_check(self, position: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """급변 감지 시 호출. 결정: HOLD_POSITION | TIGHTEN_SL | EXIT_NOW"""
        if not self.available:
            return {"decision": "HOLD_POSITION", "reasoning": "AI 미사용"}
        prompt = f"""긴급 상황 판단. 보유 포지션에 불리하거나 비정상적인 급변이 감지됐습니다.
당신의 목표는 EV 보호: 패닉 청산도, 희망 고문도 금물. 구조가 깨졌는가만 판단하라.

포지션: {json.dumps(position, ensure_ascii=False)}
상황: {json.dumps(context, ensure_ascii=False)}

JSON만 응답:
{{"decision": "HOLD_POSITION 또는 TIGHTEN_SL 또는 EXIT_NOW",
  "new_sl": 0,
  "reasoning": "한국어 1문장"}}"""
        try:
            return _extract_json(self._call(prompt, max_tokens=250))
        except Exception as e:
            logger.error(f"개입 판단 오류: {e}")
            return {"decision": "HOLD_POSITION", "reasoning": f"오류-유지: {str(e)[:40]}"}

    # ── ③ 거래 종료 후 셀프 리뷰 (#12) ──────────────────────

    def review_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available:
            return {}
        prompt = f"""방금 종료된 거래를 냉정하게 복기하라. 자기변호 금지, 결과론 금지
(좋은 판단이 손실로 끝날 수 있고 나쁜 판단이 수익으로 끝날 수 있다 —
'판단 과정'을 평가하라).

거래 기록:
{json.dumps(trade, ensure_ascii=False, indent=1, default=str)}

JSON만 응답 (전 필드 한국어):
{{
  "entry_eval": "진입 판단 평가 (근거가 유효했나)",
  "exit_eval": "청산 평가 (너무 빨랐나/늦었나/적절했나)",
  "well_done": "잘한 점 1가지",
  "mistake": "실수 또는 개선점 1가지 (없으면 '없음')",
  "market_character": "당시 시장 특징 1문장",
  "next_time": "동일 상황 재발 시 대응 전략 1문장",
  "grade": "A/B/C/D/F (판단 과정 기준)"
}}"""
        try:
            return _extract_json(self._call(prompt, max_tokens=500))
        except Exception as e:
            logger.error(f"거래 리뷰 오류: {e}")
            return {}

    # ── ④ 자기 개선 프롬프트 생성 (#14) ─────────────────────

    def generate_improvement_prompt(self, metrics: Dict[str, Any],
                                    recent_trades: List[Dict[str, Any]]) -> str:
        """
        누적 DB 기반 자기 평가 → 사람이 Claude Code에 붙여넣을 개선 프롬프트 생성.
        코드를 스스로 고치지 않는다 — 개선안을 텍스트로만 출력 (사용자 지침 #14).
        """
        if not self.available:
            return "AI 미사용 - 개선 프롬프트 생성 불가"
        slim = [{k: t.get(k) for k in ("direction", "confidence", "profit", "r_multiple",
                                       "slippage", "latency_ms", "exit_reason", "reasoning")}
                for t in recent_trades[:30]]
        prompt = f"""당신은 자동매매 시스템의 수석 퀀트다. 아래 실측 성과를 분석해
시스템 개선 프롬프트를 작성하라. 과최적화 경계: 특정 기간에만 맞는 파라미터
땜질이 아니라 구조적 결함(집행 비용, 리스크, 로직 모순)을 우선하라.

누적 성과 지표:
{json.dumps(metrics, ensure_ascii=False, indent=1)}

최근 거래 (신뢰도/손익/R/슬리피지/레이턴시/청산사유):
{json.dumps(slim, ensure_ascii=False, indent=1)}

다음 형식의 마크다운으로 출력하라 (코드 수정은 하지 말고 프롬프트만):

# [Claude Code용 개선 Prompt] {{날짜}}
## 1. 현재 문제 (데이터 근거 포함)
## 2. 원인 분석
## 3. 수정할 코드 구조 (파일/함수 단위로 구체적으로)
## 4. 예상 효과 (측정 방법 포함)
## 5. 과최적화 위험 자가 점검"""
        try:
            return self._call(prompt, max_tokens=1500)
        except Exception as e:
            logger.error(f"개선 프롬프트 생성 오류: {e}")
            return f"개선 프롬프트 생성 실패: {e}"
