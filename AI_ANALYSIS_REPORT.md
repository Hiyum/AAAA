# AI 트레이딩 시스템 진단 보고서 — 외부 AI 분석용 (ChatGPT / Gemini / Grok)

> **이 문서의 목적**: 이 보고서만 읽고도 시스템의 구조·전략·실측 데이터·문제점을
> 완전히 이해하고 개선안을 낼 수 있도록 작성된 자기 진단 보고서다.
> 핵심 코드는 생략 없이 전문을 수록했고, 모든 조건·필터에 "왜 존재하는가"와
> "거래 빈도/손익에 미치는 실측 영향"을 달았다. 모든 수치는 실제 백테스트
> (OANDA:XAUUSD 15분봉, 2025-09-01 ~ 2026-07-13, 1,078거래)와 실계좌 운용
> 로그에서 나온 것이다. 추정치는 추정치라고 명시했다.
>
> 작성: Claude (이 시스템의 설계·구현자) / 2026-07-15
> 전체 아키텍처·이력 문서: 저장소의 `HANDOFF_FOR_CHATGPT.md` 참조 (본 보고서는
> "왜 거래가 적고, 왜 잃고, 왜 크게 못 버는가"에 집중한 진단 편이다)

---

## 목차

0. [시스템 1분 요약 + 핵심 실측 수치](#0-시스템-1분-요약)
1. [하루 거래 횟수가 적은 이유 (코드·전략 관점)](#1-하루-거래-횟수가-적은-이유)
2. [진입을 제한하는 조건 전체 목록 (코드 전문 포함)](#2-진입을-제한하는-조건-전체-목록)
3. [각 필터의 거래 횟수 영향 정량화](#3-각-필터의-거래-횟수-영향)
4. [손실이 발생하는 주요 원인](#4-손실이-발생하는-주요-원인)
5. [손실 거래들의 공통 특징 (데이터)](#5-손실-거래들의-공통-특징)
6. [수익을 크게 가져가지 못하는 이유](#6-수익을-크게-가져가지-못하는-이유)
7. [수익 극대화 개선 포인트](#7-수익-극대화-개선-포인트)
8. [승률을 유지하며 거래 횟수를 늘리는 방법](#8-승률-유지-빈도-증가-방법)
9. [리스크 관리의 문제점 (현재 상태 정직 공개)](#9-리스크-관리의-문제점)
10. [백테스트에서 발견한 약점](#10-백테스트에서-발견한-약점)
11. [코드 구조의 문제점](#11-코드-구조의-문제점)
12. [성능 병목](#12-성능-병목)
13. [개선 우선순위 (이유 포함)](#13-개선-우선순위)
14. [부록: 서버 핵심 코드 전문](#14-부록-서버-핵심-코드-전문)

---

# 0. 시스템 1분 요약

**파이프라인**: TradingView Pine Script(15분봉 분석·신호·매 봉 JSON webhook)
→ ngrok → Flask 서버 → Claude AI(진입 검증, confidence 산정) → MT5(지정가 래더 집행).
저장은 SQLite(신호가/실체결가/슬리피지/레이턴시/AI 판단/거래 리뷰 영구 기록).

**전략 핵심 가설** (576일 사전 연구로 확립): 금 15분봉에서 임펄스 캔들을
추격하면 죽고(PF 0.50), 역방향으로 받아치면 산다(PF 1.16). 여기에 v8에서
유동성 스윕(stop hunt) 페이드를 추가.

**집행**: 시장가 폐기. 신호가보다 유리한 쪽에 지정가(±0.3×ATR)를 깔고
2봉(30분) 내 미체결 시 취소. 근거: 페이드 진입 시점 = 스프레드가 가장
벌어지는 순간이므로, 스프레드를 지불하는 쪽에서 받는 쪽으로 전환.

**최신 백테스트 실측 (10.5개월, 1,078거래, 수수료 0.01%+슬리피지 2틱 포함)**:

| 지표 | 값 | 해석 |
|---|---|---|
| 순익 | +$97.47 (자본 $100) | 아래 MDD 참조 — 착시임 |
| 승률 | 49.91% (롱 51.1 / 숏 48.7) | 동전 던지기 수준 |
| Profit Factor | **1.048** | 총이익 2,134 / 총손실 2,037 |
| 거래당 기대값 | **+$0.09** | 사실상 0 |
| 지불 수수료 | **$169.04** | 순익보다 큼 |
| MDD | **-$188.87** | 자본 100의 189% — 실계좌면 파산 |
| Sharpe | -0.397 | 변동 대비 수익 음수 |
| 평균 승리 / 평균 손실 | +$3.98 / -$3.77 | 손익비 1.06 — 꼬리가 없다 |
| 평균 보유 | 16.4봉 (≈4.1시간) | |
| 거래 빈도 | 1,078 ÷ ~314일 ≈ **3.4회/일** | 당시 세션 13시간 제한 하 |

**결정적 발견**: 청산 경로별 손익 분해 —

| 청산 경로 | 건수 | 합계 손익 | 평균 |
|---|---|---|---|
| S-TP1 (숏 0.75R 부분익절) | 252 | -$41.68 | -$0.17 |
| S-RUN (숏 러너 트레일) | 252 | **-$106.01** | -$0.42 |
| L-TP1 (롱 부분익절) | 246 | +$10.54 | +$0.04 |
| L-RUN (롱 러너) | 245 | -$58.87 | -$0.24 |
| **리버설** (반대편 지정가가 포지션을 뒤집음) | 83 | **+$293.71** | **+$3.54** |

**설계된 청산은 전부 합쳐 -$196이고, 설계에 없던 리버설이 +$294를 벌었다.**
즉 이 시스템의 실제 엣지는 "0.75R 익절+트레일"이 아니라 **"반대 극단까지
태우고 거기서 뒤집기"**다. 이 발견은 v8.1에서 정식 기능화됐다(§7).

**실계좌 사건 2건** (2026-07-14~15, $100 실계좌):
1. AI 계층이 오프라인(환경 문제)인 채 Pine 신호가 무검증 직행 →
   D/H1 하락 추세에서 low_sweep 롱 → 5분 만에 SL, **-$13.48**.
   교훈: 조용한 폴백은 금지 → AI 불가 시 진입 보류로 코드 수정 완료.
2. 지정가 체결이 신호가 대비 **-$1.82 개선**된 가격에 잡힘 —
   래더 설계의 첫 실증 (슬리피지가 비용이 아니라 이득으로 기록됨).

**현재 리스크 설정 상태 (중요)**: 사용자 명시 지시로 거래 단위 리스크
제한이 해제됨. $100 잔고 + 최소랏 0.01 + 구조 SL(2.5×ATR) = **거래당
리스크 8~13%**. 남은 방어선은 MDD 하드캡 25%(전량청산+정지), 일일 손실
25%, Equity Guard(연속 6패/롤링 PF<0.8 자동 정지)뿐이다. §9에서 상술.

---

# 1. 하루 거래 횟수가 적은 이유

실측 3.4회/일(세션 13h 기준). 병목은 큰 순서대로:

### 1-1. 구조적 최대 병목: 단일 포지션 + 평균 보유 4.1시간
서버 `max_open_positions` 개념(현재는 피라미딩 2단이 상한)과 Pine의
`strategy.position_size == 0` 진입 조건 때문에, **포지션 보유 중 도착하는
모든 신규 신호는 버려진다** (같은 방향 피라미딩/반대 방향 리버설 검토 제외).
평균 보유가 16.4봉(4.1시간)이므로 하루의 상당 부분이 "진입 불가 시간"이다.
세션 13시간 기준 보유 3~4건 × 4.1시간 = 세션의 대부분이 잠긴다.
**이것 하나가 신호→거래 전환율을 지배한다.**

왜 이 조건이 존재하나: 소액 계좌 마진 한계 + 동시 포지션 간 리스크 중첩
방지 + AI 판단 콘텍스트 단순화. 트레이드오프를 §8에서 다룬다.

### 1-2. 신호 자체의 희소성: bodyK=0.8 임펄스 문턱
```pine
impulseUp = bodyVal >= atrVal * bodyK and close > open   // bodyK = 0.8
impulseDn = -bodyVal >= atrVal * bodyK and close < open
```
15분봉 몸통이 ATR의 80% 이상인 방향성 캔들만 신호다. 실계좌 24시간 로그
표본에서 96봉 중 신호 봉은 13개(≈14%)였다. 사전 연구(576일 그리드서치)에서
bodyK를 0.8→0.6으로 낮추면 거래가 ~3회/일→3.8회/일로 늘고 PF 1.17이
유지됐다(임펄스 단독 기준). 왜 0.8인가: 그리드서치에서 PF와 빈도의 균형점.

### 1-3. 지정가 미체결 (TTL 30분 취소)
시장가였다면 거래가 됐을 신호가, 되돌림이 오프셋(0.3×ATR)까지 안 와서
소멸한다. **미체결률은 아직 실측 전** — v8부터 DB의 `PENDING→CANCELLED`
비율로 측정 가능해졌고, 이것이 빈도 최적화의 첫 데이터가 될 것이다.
왜 존재하나: 스프레드 역이용의 대가. 체결률과 진입가 개선의 트레이드오프.

### 1-4. AI 거부권 (confidence < 0.6 → 보류)
신호가 와도 Claude AI가 HOLD하거나 신뢰도 미달이면 버려진다. 거부율은
초기 운용에서 로그 누락 버그(응답 절단→침묵 보류) 때문에 미측정이었고,
수정 후 이제 `[AI 진입 보류]` 로그와 signals 테이블로 측정 가능하다.

### 1-5. 명시적 상한: 일일 예산 6발
`MAX_TRADES_PER_DAY = 6`. 실측 3.4회/일이므로 현재는 거의 안 걸리지만,
빈도를 늘리면 이 상한이 새 병목이 된다. 왜 존재하나: "총알을 아껴 쏘는"
저격수 규율 유도 + AI에게 `trades_left_today`로 전달되어 과잉거래 억제.

### 1-6. (해제됨) 세션 제한 / 리스크 거부
세션 07-20 GMT 제한은 사용자 지시로 제거(24시간화 — 이론상 빈도 +40%
시간창이지만 아시아 시간대의 신호 밀도·엣지는 미검증). 소액 계좌 리스크
거부도 해제되어 더 이상 빈도를 깎지 않는다.

---

# 2. 진입을 제한하는 조건 전체 목록

신호 발생부터 체결까지의 깔때기(funnel) 전체. **모든 게이트의 코드 원문**:

### 게이트 0: Pine 신호 생성 (tradingview_strategy.pine 104-112행)
```pine
barsSinceExit = strategy.closedtrades > 0 ? bar_index - strategy.closedtrades.exit_bar_index(strategy.closedtrades - 1) : cooldownBars + 1
cooldownOK = barsSinceExit >= cooldownBars    // 청산 후 1봉 대기

impulseUp = bodyVal >= atrVal * bodyK and close > open     // 급등 임펄스
impulseDn = -bodyVal >= atrVal * bodyK and close < open    // 급락 임펄스

shortSignal = inWindow and cooldownOK and (impulseUp or sweepUp)   // 급등/고점스윕 → 숏
longSignal  = inWindow and cooldownOK and (impulseDn or sweepDn)   // 급락/저점스윕 → 롱
```
- `bodyK=0.8`: 임펄스 판정 문턱 (§1-2). **주 빈도 조절 노브.**
- `cooldownBars=1`: 청산 직후 재진입 방지. 존재 이유: 같은 임펄스 연쇄에
  반복 물리는 것 방지. 영향: 미미(1봉=15분).
- `sweepUp/Dn`: v8에서 추가된 두 번째 셋업(스윙 청소 후 되돌림 마감).
  빈도를 늘린 요인 (임펄스 단독 v7 대비 +@).
- `inWindow`: 세션 필터 — 현재 0000-2400으로 사실상 무력화.

### 게이트 1~6: 서버 공통 진입 게이트 (core/trading_engine.py `_entry_gates`)
```python
def _entry_gates(self, symbol: str, action: str, payload: dict) -> Optional[str]:
    """공통 진입 게이트. 통과하면 None, 막히면 사유 문자열."""
    if not self._in_trading_session():
        return "거래 세션 밖 - 신규 진입 보류"          # 현재 0~24시 = 무력

    entry_key = f"{symbol}|{action}"
    if entry_key == self._last_entry_key and time.time() - self._last_entry_time < 60:
        return "중복 신호 무시 (60초 내 동일 신호)"      # TradingView 재전송/ngrok 재연결 사고 방지

    today = datetime.now(timezone.utc).date()
    if self._trades_day != today:
        self._trades_day = today
        self._trades_today = 0
    max_day = getattr(Config, "MAX_TRADES_PER_DAY", 6)
    if self._trades_today >= max_day:
        return f"일일 거래 예산({max_day}발) 소진 - 내일 재개"

    balance_now = self.mt5.get_account_info().get("balance", 0)
    gate = self.risk.can_trade(balance_now)                 # 일일 손실 25% / 최소잔고 $20
    if not gate["allowed"]:
        self.log(f"[거래 차단] {gate['reason']}", "WARNING")
        return f"거래 차단: {gate['reason']}"

    # 스프레드 필터: 임펄스 직후 스프레드 폭발 구간 차단
    atr = float(payload.get("atr", 0) or 0)
    tick = self.mt5.get_tick(symbol)
    if atr > 0 and tick and tick["spread"] > atr * getattr(Config, "MAX_SPREAD_ATR_MULT", 0.15):
        return (f"스프레드 과대 ({tick['spread']:.2f} > ATR×"
                f"{getattr(Config, 'MAX_SPREAD_ATR_MULT', 0.15)}) - 진입 보류")
    return None
```
각 게이트의 존재 이유:
- **60초 중복창**: 과거 실사고(TradingView 알람 재전송 + AI 응답 대기 중
  재도착 → 이중 주문) 대응. 15분봉에서 실질 빈도 영향 0.
- **일일 예산 6발**: §1-5.
- **일일 손실 게이트**: 원래 5%였으나 거래당 리스크가 10%+가 된 현재
  구성에서 한 번 손실에 하루가 끝나는 모순 → 25%로 완화(사용자 지시).
- **스프레드 필터 0.15×ATR**: 페이드 진입 순간=스프레드 최대 순간이라는
  v7 데모 전패의 교훈. ATR $5일 때 스프레드 $0.75 초과 시 보류.
  차단율 미실측(신규 필터) — signals 테이블로 측정 예정.

### 게이트 7: 미체결 래더 존재 시 보류 (`_check_entry` 내)
```python
# 미체결 래더가 살아있으면 새 진입 판단 보류 (이중 노출 방지)
if self.db.pending_trades():
    return {"message": "대기 지정가 존재 - 새 진입 보류"}
```
존재 이유: 이전 신호의 래더가 살아있는데 새 신호로 또 깔면 우발적 이중
노출. 영향: TTL 30분 동안 후속 신호 차단 — **양방향 OCO 설계로 개선 여지**(§8).

### 게이트 8: AI 검증 (fail-safe 포함)
```python
# fail-safe: AI 검증 모드인데 AI가 죽어 있으면 '신호 직접 실행'으로
# 조용히 넘어가지 않고 진입을 보류한다 (검증 없는 거래 금지)
if getattr(Config, "AI_CONFIRM_ENTRIES", True) and not self.ai.available:
    self.log(f"[AI 사용 불가 - 진입 보류] {self.ai.unavailable_reason}", "ERROR")
    return {"message": f"AI 사용 불가로 진입 보류: {self.ai.unavailable_reason}"}
...
final = str(decision.get("action", "HOLD")).upper()
confidence = float(decision.get("confidence", 0))
min_conf = 0.7 if (autonomous and action not in ("BUY", "SELL")) else 0.6
if final not in ("BUY", "SELL") or confidence < min_conf:
    ...
    return {"message": f"AI 진입 보류: {why}"}
```
존재 이유: 실계좌 사건 1(-$13.48)이 증명 — 무검증 신호 직행은 위험.
임계 0.6/0.7의 근거: confidence→lot 등급표의 "기본" 구간 하한. **주의:
이 임계값과 AI confidence의 보정(calibration)은 통계적으로 미검증** (§9).

### 게이트 9: 지정가 래더 배치 (미체결이면 거래 자체가 소멸)
`_execute_entry` 전문은 부록 14-2. 요약: rung1 = 신호가 ± 0.3×ATR (60%),
rung2 = ± 0.7×ATR (40%), TTL 30분, AI가 entry_zone을 주면 그 구간을 우선.

---

# 3. 각 필터의 거래 횟수 영향

| 필터 | 실측/추정 영향 | 근거 |
|---|---|---|
| 단일 포지션 + 평균보유 4.1h | **최대** — 보유 중 신호 전부 소멸 | 평균 16.4봉 보유 × 3.4거래/일 = 세션 대부분 잠김 (실측) |
| bodyK 0.8 임펄스 문턱 | 신호 봉 비율 ≈14% (24h 실측 표본) | 0.6으로 낮추면 +12% 빈도, PF 유지 (576일 그리드서치) |
| 지정가 미체결 (TTL 2봉) | **미실측** — v8 신규 | DB PENDING→CANCELLED 비율로 측정 가능해짐 |
| AI 거부 (conf<0.6) | **미실측** (절단 버그로 초기 데이터 오염) | 수정 후 signals 테이블에 전 판단 기록 중 |
| 스프레드 필터 0.15×ATR | 미실측 — v8 신규 | 임펄스 직후 위주로 차단될 것 (설계 의도) |
| 대기 래더 존재 블록 | TTL 30분 × 발생 횟수만큼 후속 신호 차단 | 구조상 명확, 횟수 미실측 |
| 일일 예산 6발 | 현재 비활성 병목 (3.4<6) | 빈도 증가 시 활성화됨 |
| 중복 60초 창 | ≈0 (15분봉) | 구조상 명확 |
| 쿨다운 1봉 | 미미 | 구조상 명확 |
| 세션 제한 (제거됨) | 제거로 시간창 +85% (13h→24h), 실효 빈도 증가는 미검증 | 아시아 시간대 신호 밀도 데이터 없음 |
| 리스크 거부 (해제됨) | 해제 전 $100 계좌에서 사실상 100% 차단이었음 | 실계좌 로그 (03:30 SELL 72% 거부 사례) |

**분석 시 유의**: "필터를 빼면 빈도가 는다"는 자명하지만, 이 시스템의
사전 연구 결론은 "빈도·승률·손익비는 삼중 트레이드오프"였다(491개 구성
전수조사에서 예외 없음). 필터 제거 제안은 반드시 PF 영향 추정과 함께 할 것.

---

# 4. 손실이 발생하는 주요 원인

중요도 순, 전부 데이터 근거 있음:

### 4-1. 손익비 부재 — 승리가 손실보다 크지 않다 (최대 원인)
평균 승리 +$3.98 vs 평균 손실 -$3.77 (비 1.06), 승률 49.9% → PF 1.048.
50% 승률 자체는 페이드 전략으로 정상이다. **문제는 이길 때 크게 못 이기는
것**이고, 그 원인은 청산 설계다(§6). 리버설 청산(평균 +$3.54/건)이
증명하듯 시장은 큰 움직임을 줬는데 설계가 그걸 0.75R에서 잘랐다.

### 4-2. 비용 — 수수료가 순익의 1.7배
수수료 $169 vs 순익 $97. 거래당 평균 수수료 $0.157 + 슬리피지 2틱 가정.
기대값 $0.09/거래인 전략에서 비용 $0.16/거래는 엣지의 대부분을 먹는다.
1봉짜리 초단타 141건(대부분 구버전 버그 산물, §4-3)이 비용만 태웠다.

### 4-3. (수정 완료) 리버설 후 SL 미초기화 버그
포지션이 리버설로 뒤집힐 때 `stopLvl`이 구 포지션 값으로 남아 새 포지션이
1봉 내 즉사하던 버그. 1봉 이하 거래 141건의 상당수가 이것. v8.1에서
`posSign` 전환 감지로 수정(부록의 Pine 137-151행). **수정 후 재백테스트가
아직 없다 — 수치가 유의미하게 달라질 것.**

### 4-4. 강추세일에 페이드 연속 손절
숏 성적(-$106 S-RUN)이 롱(-$59)보다 나쁘다. 해당 기간 금은 상승 국면이
많았고, 상승 임펄스를 계속 받아치다(숏) 임펄스가 '지속'되는 날 연속으로
찢겼다. 월별로 2025-09(-$78), 2025-10(-$67)이 그 국면. AI 프롬프트에
"상위 프레임 강추세면 페이드 스킵" 지침이 있으나 **AI 거부권의 실효는
미검증**이고, 백테스트(Pine 단독)에는 그 필터가 아예 없다 — **백테스트와
실거래의 신호 품질이 다르다**(백테스트엔 AI 계층이 없음).

### 4-5. 검증 우회 사고 (실계좌, 수정 완료)
AI 오프라인 상태에서 Pine 신호 무검증 직행 → D/H1 하락 추세 역행 롱 →
-$13.48. 시스템적 원인(조용한 폴백)은 fail-safe로 제거. 남는 교훈:
**백테스트에 없는 계층(AI)이 실거래 성패를 좌우한다면, 그 계층이 죽었을 때의
동작이 곧 리스크다.**

### 4-6. 진입 시간대
차트 TZ 기준 08~09시(-$64.1), 14시(-$61.0)가 최악 — 런던/뉴욕 오픈 직후
변동성·스프레드 폭발 구간. 20시(+$83.2), 11~12시(+$155.0)가 최선.
시간대 필터는 현재 없다(사용자가 세션 제한 제거를 지시). 단, 스프레드
필터(0.15×ATR)가 오픈 직후 진입 일부를 걸러줄 것으로 기대(미실측).

---

# 5. 손실 거래들의 공통 특징

1,078건 백테스트 + 실계좌 표본에서 손실 거래의 프로파일:

1. **숏이 더 잃는다**: 숏 548건 승률 48.7%, 합계 손익 기여 -@ (S-RUN
   -$106이 전 경로 중 최악). 상승 국면에서 상승 임펄스 페이드가 주범.
2. **상위 추세 역행**: 실계좌 손실 건(-$13.48)이 전형 — D:down, H1:down에서
   low_sweep 하나 보고 롱. 백테스트엔 MTF 정렬 필터가 없어서 이런 거래가
   그대로 들어갔다.
3. **1봉 초단타 군집(141건)**: 대부분 리버설 SL 버그 산물(수정됨) —
   진입 즉시 청산, 수수료만 지불.
4. **오픈 시간대(08-09, 14시)**: 스프레드/변동성 폭발 구간 진입.
5. **RUN 경로 청산**: 부분익절 후 러너가 타이트한 트레일(스윙±0.3×ATR)에
   걸려 본전 부근에서 털린 뒤, 가격이 원래 방향으로 재개되는 패턴.
   (RUN 손익 = 롱 -$59/숏 -$106으로 TP1보다 나쁨 — 러너 관리가 가치를
   더하기는커녕 깎고 있다.)
6. **고변동성 봉에서 진입**: body_atr가 클수록 SL(2.5×ATR)도 커져 손실
   금액이 커진다. 손실 상위 거래는 ATR 확장 구간에 몰린다(정성 관찰 —
   개별 거래 ATR이 export에 없어 정량화는 DB 축적 후 가능).

---

# 6. 수익을 크게 가져가지 못하는 이유

평균 승리 $3.98 ≈ 평균 손실 $3.77. 오른쪽 꼬리(큰 승리)가 없다. 원인은
청산 설계 그 자체다:

### 6-1. TP1 0.75R × 50%가 수학적으로 승리를 반토막낸다
```
전형적 승리: 물량의 50%를 +0.75R에서 익절 (+0.375R 확정)
             나머지 50%는 본전 잠금 후 트레일
전형적 손실: 물량의 100%가 -1R
→ 러너가 0에서 털리면 승리 = +0.375R vs 손실 = -1R
→ 승률 50%에서 이기려면 러너가 평균 +0.625R 이상 벌어야 하는데,
  실측 RUN 경로 합계는 마이너스(-$165) — 러너가 오히려 까먹는다
```
왜 이렇게 설계했나: v7 시절 "승률을 높여달라"는 요구에 맞춘 구성
(0.75R 조기 익절은 승률을 60%로 올려줬다). **승률을 산 대가로 손익비를
판 것이며, PF 1.048이 그 영수증이다.**

### 6-2. 러너 트레일이 너무 타이트하다
부분익절 후 SL을 `스윙 ± 0.3×ATR`로 따라붙인다 (Pine 155-175행, 서버
`_manage_stops` 동일). 15분봉 스윙(피벗 3,3)은 4.5봉마다 갱신될 수 있는
가까운 구조라, 정상 되돌림에도 러너가 털린다. 결과: RUN 경로 합계 -$165.

### 6-3. 리버설이 증명한 반사실(counterfactual)
반대 극단까지 태워진 83건은 평균 +$3.54를 벌었다. 즉 **큰 움직임은
존재했고, 붙잡는 방법도 존재했다(반대 신호가 뜰 때까지 보유).** 이 발견을
근거로 v8.1에서 리버설을 정식화했다: 서버는 보유 중 반대 신호 도착 시
AI 신뢰도 0.75+ 동의 조건으로 청산+역진입한다(`_check_reversal`, 부록 14-4).

### 6-4. 고정 캐시 사이징 — 복리가 없다
Pine 백테스트는 거래당 $1,500 고정(cash). 이기든 지든 크기가 같아서
자본 성장이 수익을 가속하지 않는다. 실거래는 confidence 기반 리스크%
사이징이 구현돼 있으나 최소랏 바닥(0.01) 때문에 $100 계좌에선 사실상
전 거래 동일 크기다.

---

# 7. 수익 극대화 개선 포인트

우선순위·기대효과 순 (전부 백테스트로 검증 가능한 가설):

1. **TP1 재설계** — 후보: (a) TP1을 0.75R→1.5R로 이동, (b) 부분익절
   비율 50%→25%, (c) TP1 완전 제거 + 리버설/구조 청산만 사용.
   근거: §6-1의 수학. (c)는 리버설 데이터(+$3.54/건)가 직접 지지한다.
   주의: 승률은 내려간다(60%→50% 안팎 예상). 발주자가 승률에 민감하므로
   "승률 vs 손익비" 트레이드오프를 명시하고 제안할 것.
2. **러너 트레일 완화** — 15분 스윙 대신 1시간 스윙(또는 pivotLen 3→6),
   오프셋 0.3→0.5×ATR. RUN 경로 -$165를 양전시키는 것이 목표.
3. **리버설 정식화의 실측 검증** (구현 완료, 데이터 수집 단계) —
   백테스트에서 리버설은 '우연히 살아남은 반대편 지정가'로 실현됐다.
   정식화 후 Pine 재백테스트로 PF 변화 확인 필요. SL 재초기화 버그
   수정(§4-3)과 결합하면 성적이 유의미하게 달라질 것.
4. **MTF 역행 차단을 Pine에도** — 현재 상위 추세 필터는 AI 프롬프트에만
   있다. `useRegime` 스타일로 Pine에 옵션 추가 시 백테스트로 직접 검증
   가능: "D와 H4가 같은 방향일 때 그 방향 페이드 금지" (§5-2의 손실
   프로파일 직격). 숏 -$106 경로의 상당분이 여기 걸릴 것.
5. **시간대 필터 (데이터 기반)** — 08-09시·14시 진입 차단만으로 백테스트
   기준 +$125 개선 여지 (해당 시간 합계 손실). 단, 표본 분할 후 견고성
   확인 필요(과최적화 위험 — 시간대 성적은 국면 따라 뒤집힐 수 있음).
6. **복리 사이징** — Pine을 `strategy.percent_of_equity`로, 실거래는
   잔고 성장에 따라 랏 자동 증가(이미 리스크% 기반이므로 자본만 크면 작동).

---

# 8. 승률 유지 + 빈도 증가 방법

§1의 병목 순서대로 공략하는 것이 정석:

1. **양방향 OCO 대기주문** (단일 포지션 병목 완화 1단계):
   현재는 한 신호의 래더가 살아있으면 후속 신호를 버린다(게이트 7).
   개선: 롱 래더와 숏 래더를 동시에 허용하고 한쪽 체결 시 반대쪽 취소
   (One-Cancels-Other). 백테스트의 리버설 데이터는 사실상 이 구조가
   유효함을 이미 보여줬다. 승률 영향: 중립~긍정 (체결가는 항상 극단쪽).
2. **bodyK 0.8 → 0.7 또는 0.6**: 사전 연구에서 0.6은 빈도 +12%, PF 1.17
   유지 (임펄스 단독 기준). 스윕 셋업이 추가된 v8에서 재검증 필요.
3. **지정가 오프셋 튜닝**: rung1 0.3×ATR은 체결률과 개선폭의 트레이드오프.
   DB에 미체결률이 쌓이면 0.2/0.25/0.3을 데이터로 비교 — 미체결로 버려지는
   유효 신호를 회수하는 것이 공짜 빈도다 (신호 추가 없이 전환율만 상승).
4. **셋업 추가 (독립 엣지)**: 현재 임펄스+스윕. 후보: VWAP 과이탈 회귀
   (가격이 VWAP에서 2×ATR 이상 이탈 시 회귀 진입 — 페이드 철학과 동일
   계열), FVG 되돌림 진입. 각각 별도 백테스트로 PF≥1.1 확인 후 편입.
5. **24시간화의 실효 측정**: 아시아 시간대(00-06시)는 백테스트에 거래가
   거의 없다(세션 제한 때문). 재백테스트로 아시아 신호의 PF를 먼저 확인
   — 스프레드가 넓은 시간대라 페이드에 불리할 수도, 레인지라 유리할 수도.
   **데이터 없이 켜둔 상태이므로 이것이 현재 최대 미검증 변수다.**
6. 일일 예산 6→8 상향은 위 1-5로 빈도가 실제로 늘어난 뒤에.

---

# 9. 리스크 관리의 문제점

**현재 상태를 있는 그대로 공개한다** (외부 AI는 이 상태를 전제로 조언할 것):

### 9-1. 거래 단위 리스크 제한이 해제되어 있다 (사용자 명시 지시)
```python
REJECT_IF_MIN_LOT_EXCEEDS_CAP = False   # 하드캡 초과 거부: 해제
SMALL_ACCOUNT_FIT_SL = False            # SL 조임 모드: 해제
ALLOW_MIN_LOT_OVERRIDE = True           # 최소랏 강행: 활성
```
배경: $100 실계좌 + GOLD 최소랏 0.01(계약 100oz) + 구조 SL(2.5×ATR≈$10~13)
= 거래당 리스크 8~13%. 원설계(하드캡 5% 초과 시 거부)는 이 계좌에서 모든
거래를 차단했고, 대안(SL을 $5로 조이는 SL-핏 모드)도 구현했으나 사용자가
"리스크 제한을 없애라"고 지시해 해제됐다. **켈리 관점에서 승률 50%·손익비
1.06 전략에 13% 베팅은 파산 확률이 지배적이다. 2연패 ≈ MDD 정지선(-25%).**
남은 방어선: MDD 하드캡 25%(전량청산+정지), 일일 손실 25%, Equity Guard.

### 9-2. [버그 — 미수정] 계좌 교체 시 MDD 오작동
`peak_equity()`는 DB의 역대 최고 equity를 쓴다. **다른(더 큰) 계좌로
접속했던 이력이 같은 DB에 있으면, 작은 계좌로 바꾸는 순간 피크 대비
낙폭이 즉시 25%를 넘어 시작하자마자 전량청산+정지가 발동할 수 있다.**
수정 방향: 계좌번호별 피크 분리 또는 세션 시작 시 피크 리셋 옵션.

### 9-3. 인메모리 카운터의 재시작 리셋
일일 거래 카운터(`_trades_today`)와 일일 손실 기준잔고가 서버 재시작 시
초기화된다. 재시작하면 그날 예산 6발이 다시 생기고, 재시작 시점 잔고가
새 기준이 되어 그날 기존 손실이 한도 계산에서 사라진다. DB로 이전 필요.

### 9-4. AI confidence는 미보정 값이다
confidence가 lot 크기(0.5~3배)와 진입 가부(0.6/0.7/0.75/0.8 임계)를
결정하는데, "0.85가 0.65보다 실제로 더 자주 이기는가"의 데이터가 없다.
signals 테이블이 이제 전 판단을 기록하므로 30~50건 쌓이면 보정 곡선을
그릴 수 있다. 그 전까지 confidence 기반 차등 베팅은 근거 없는 레버리지다.

### 9-5. 청산 슬리피지 미기록
진입 슬리피지는 기록하지만(실측 1건: -$1.82 개선) 청산(SL 체결가 vs 설정
SL)의 슬리피지는 기록하지 않는다. SL은 스탑 주문이라 갭에 취약 — 실측
-$13.48 손실은 설정 리스크 $13.20보다 $0.28 나빴다(2.1% 슬리피지).

### 9-6. Equity Guard의 마이그레이션 오염 가능성
구 trades.json(과거 데모의 연속 손실 포함)이 DB로 이관되어, Equity Guard의
연속 손실 카운트가 과거 계좌의 기록과 이어질 수 있다. 계좌/운용 세대 구분
컬럼이 없다.

---

# 10. 백테스트에서 발견한 약점

1. **음수 자본 생존 착시**: MDD -$188 > 자본 $100인데 TradingView는 고정
   $1,500 주문을 계속 넣는다. +97% 헤드라인은 실계좌에서 재현 불가능
   (2025년 10월에 이미 파산). **모든 백테스트 해석에서 이것부터 보정할 것.**
2. **체결 모델 낙관**: TradingView는 limit 가격 '터치'를 체결로 간주한다.
   실제로는 터치 시 큐 뒤에 있어 미체결될 수 있다. 실측 체결률(DB)과
   비교해 보정 필요.
3. **스왑(오버나이트 이자) 미모델링**: v8부터 오버나이트 보유를 허용하는데
   백테스트 비용 모델에 스왑이 없다. 평균 보유 4.1시간이라 영향은 제한적
   이나, 러너를 길게 끌수록(§7 개선안) 이 누락이 커진다.
4. **AI 계층 부재**: 백테스트는 Pine 신호 전량 집행이지만 실거래는 AI가
   거부권을 행사한다. 백테스트 성적 ≠ 실거래 기대 성적 (양방향 모두 가능
   — AI가 나쁜 거래를 걸러줄 수도, 좋은 거래를 걸러버릴 수도).
5. **단일 상품·단일 기간**: XAUUSD 10.5개월. 국면 의존이 실측됨(2026-01
   한 달 +$163이 전체 순익 초과). OOS/워크포워드 분할 없이 전 기간 성적만
   보고 있다.
6. **리버설·SL버그 수정 전 데이터**: 본 보고서의 수치는 v8 기준. v8.1
   수정(SL 재초기화, 리버설 정식화, 24시간화) 반영 재백테스트가 아직 없다.

---

# 11. 코드 구조의 문제점

1. **청산 로직 이중 구현 (드리프트 위험 1순위)**: 동일 규칙(0.75R 부분익절
   +본전+구조트레일)이 Pine(155-175행)과 Python(`_manage_stops`, 부록 14-3)에
   각각 손으로 구현돼 있다. 한쪽만 고치면 백테스트≠실거래가 재발한다
   (실제로 v7에서 발생했던 사고). 해결: 파라미터를 한 곳(config)에서 양쪽으로
   주입하거나, 최소한 CI에서 규칙 상수 일치 검사.
2. **TradingEngine God-class**: 진입/청산/리버설/피라미딩/감시/정합/리뷰가
   한 클래스 ~750줄. 테스트 가능한 단위(EntryPipeline, PositionManager,
   Reconciler)로 분리 필요.
3. **테스트 부재**: 개발 중 시뮬레이션 스크립트로 검증했지만 저장소에
   pytest 스위트가 없다. lot 계산·게이트·리버설·DB 정합은 회귀에 취약.
4. **설정 하드코딩**: config.py 수정+재시작 필요. 재시작은 §9-3 리셋을
   동반하므로 설정 변경 자체가 리스크 이벤트다. 대시보드 핫 리로드 필요.
5. **시뮬레이션 모드의 불충실**: MT5 미설치 환경용 sim 모드가 지정가
   체결/부분청산을 실제로 흉내 내지 않아(즉시 성공 반환), sim으로는
   체결 로직을 검증할 수 없다.
6. **매직 넘버 산재**: 0.75R, 0.3×ATR, 60초, 30분 TTL, 0.6/0.7/0.75/0.8
   임계 등이 코드 곳곳에 리터럴로 있다 (일부만 config).
7. **스레드 감독 부재**: 가드 루프/감시 루프/리뷰 스레드가 daemon으로만
   돌고, 죽어도 아무도 모른다 (예외는 로그만 남기고 루프는 계속이지만
   루프 자체가 죽는 경로에 대한 재기동 없음).
8. **레거시 흔적**: 대시보드가 구 필드명을 요구해 `get_status()`에 매핑
   계층 존재, `get_ohlcv()` 미사용, `data/` 빈 패키지.

---

# 12. 성능 병목

이 시스템의 병목은 CPU가 아니라 **레이턴시와 가용성**이다:

1. **AI 호출이 핫패스에 있다**: 신호 수신→주문까지 AI 왕복 2~21초 실측
   (03:30 사례 21초 — 절단 복구 포함). 지정가 집행이라 시장가 시절만큼
   치명적이지 않지만, 21초면 되돌림의 상당분이 지나간다. 개선: max_tokens
   상향으로 절단 재시도 제거(완료), 프롬프트 축약, 또는 AI를 사전 승인
   (직전 봉 데이터로 미리 판단) 구조로 이동.
2. **ngrok 무료 + 노트북 = 가용성 병목**: 재시작마다 URL 변경(알람 수동
   갱신), 절전 시 전체 정지. 신호 유실은 곧 통계 오염. VPS 이전이 근본책.
3. **경미(현 규모에서 무시 가능)**: SQLite 커밋/조회 빈도, 5초 틱 폴링의
   MT5 IPC, 60초 정합 루프의 O(n) 쿼리, 청산마다 리뷰 API 호출(별도
   스레드라 비차단). 거래 빈도가 10배가 되어도 문제없는 수준.
4. **크레딧 비용**: 24시간화로 자율 모드 시 ~96호출/일. 확인 모드(현재)는
   신호 봉만 호출해 ~13회/일 수준.

---

# 13. 개선 우선순위

| 순위 | 항목 | 이유 |
|---|---|---|
| 1 | **v8.1 재백테스트** (SL버그 수정·리버설 정식화·24시간 반영) | 본 보고서의 모든 수치가 v8 기준 — 수정 반영 성적이 나와야 이후 모든 의사결정의 기준선이 생긴다. 비용 0, 소요 10분 |
| 2 | **계좌 교체 MDD 버그 수정 + 카운터 DB 영속화** (§9-2, 9-3) | 안전장치가 오발/무력화되는 정확성 버그. 실돈 운용 중이므로 즉시 |
| 3 | **깔때기 계측 완성** — 게이트별 차단 카운트, 래더 체결률, AI 거부율을 DB에 집계 | §3의 "미실측" 칸을 채우기 전의 모든 빈도 논쟁은 추측이다. 측정 없이 최적화 없다 |
| 4 | **청산 재설계 백테스트** — TP1 1.5R/25%/제거 × 트레일 완화 매트릭스 (§7-1,2) | 데이터가 지목하는 가장 큰 EV 레버 (설계 청산 -$196 vs 리버설 +$294) |
| 5 | **MTF 역행 차단 Pine 옵션** (§7-4) | 손실 프로파일(숏 -$106, 추세 역행) 직격. Pine에 넣어야 백테스트 검증 가능 |
| 6 | **confidence 보정 곡선** (30~50건 축적 후) | AI 계층이 가치를 더하는지 깎는지 판정 — 크레딧 지출의 정당성 검증 |
| 7 | **양방향 OCO + 오프셋 튜닝** (§8-1,3) | 승률 훼손 없는 빈도 증가 경로. 3번의 체결률 데이터가 선행 조건 |
| 8 | **VPS + (가능하면) 센트 계좌** | 가용성 병목 제거 + $100로 정상 리스크 수학 복원 |
| 9 | pytest 스위트 + 청산 규칙 단일 소스화 (§11-1,3) | 드리프트 재발 방지 — 4·5·7의 변경 작업 전에 안전망 필요 |
| 10 | 엔진 분리 리팩토링, 설정 핫 리로드 | 품질 부채 — 기능이 안정된 뒤 |

**외부 AI에게**: 순위 1·3(측정)보다 4·7(최적화)을 앞세우는 제안은 이
시스템이 이미 두 번 겪은 실수(측정 없는 개선 → 실전 반증)의 반복이다.
반박하려면 측정 없이도 확실한 근거를 제시할 것.

---

# 14. 부록: 서버 핵심 코드 전문

Pine 전략 전문은 저장소 `tradingview_strategy.pine` (216줄, §2 게이트 0과
§6에 핵심부 수록). 이하 Python 핵심 로직 전문:

## 14-1. 리스크/랏 계산 (core/risk_manager.py 핵심부)

```python
# 신뢰도 → lot 배수 등급표 (Claude AI confidence 기반)
CONFIDENCE_TIERS = [
    (0.92, 3.0),   # 초고신뢰 (드물어야 정상)
    (0.85, 2.0),   # 고신뢰
    (0.75, 1.5),   # 중상
    (0.65, 1.0),   # 기본
    (0.00, 0.5),   # 저신뢰 → 절반
]
MAX_RISK_HARD_CAP = 0.05   # (현재 진입 거부 용도로는 미사용 — 해제 상태)

def get_risk_per_trade(self, account_balance: float) -> float:
    if self.mode == "auto":
        if account_balance > 50000:  return 0.01
        elif account_balance > 10000: return 0.015
        else:                          return 0.02
    return self.risk_per_trade

def calculate_lot_size(self, account_balance, entry_price, stop_loss,
                       confidence=0.65, contract_size=100000.0) -> float:
    mult = self.confidence_multiplier(confidence)
    if self.fixed_lot > 0:
        return round(max(0.01, min(self.fixed_lot * mult, 10.0)), 2)
    risk_pct = min(self.get_risk_per_trade(account_balance) * mult, self.MAX_RISK_HARD_CAP)
    risk_amount = account_balance * risk_pct
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance <= 0 or contract_size <= 0:
        return 0.01
    return max(0.0, risk_amount / (sl_distance * contract_size))
    # ↑ 이론 랏. $100 계좌에선 ~0.002가 나오고, 브로커 최소 0.01로 강제
    #   상향되며 리스크가 5~7배 팽창한다 — §9-1의 근원

def can_trade(self, account_balance: float) -> Dict[str, Any]:
    if self.initial_balance <= 0:
        return {"allowed": True, "reason": ""}
    loss_pct = (self.initial_balance - account_balance) / self.initial_balance
    limit = self.daily_loss_limit            # 현재 0.25
    if self.mode == "auto" and account_balance < 20:
        return {"allowed": False, "reason": "잔고 부족 (최소 $20)"}
    if loss_pct >= limit:
        return {"allowed": False, "reason": f"일일 손실 한도 초과 ({loss_pct*100:.1f}%)"}
    return {"allowed": True, "reason": ""}

def check_drawdown(self, current_equity: float, peak_equity: float) -> Dict[str, Any]:
    cap = getattr(Config, "MAX_DRAWDOWN_PCT", 0.25)
    if peak_equity <= 0 or current_equity <= 0:
        return {"breached": False, "dd_pct": 0.0}
    dd = (peak_equity - current_equity) / peak_equity
    return {"breached": dd >= cap, "dd_pct": dd, "cap": cap}
```

## 14-2. 진입 집행 — 지정가 래더 (core/trading_engine.py `_execute_entry`)

```python
def _execute_entry(self, symbol, price, payload, decision, t0,
                   unit=1, size_ratio=1.0, allow_open=False) -> dict:
    """리스크 검증 → 지정가 래더 배치 (시장가 진입 폐기)"""
    final = str(decision["action"]).upper()
    confidence = float(decision.get("confidence", 0))
    atr = float(payload.get("atr", 0) or 0)
    balance = self.mt5.get_account_info().get("balance", 0)
    specs = self.mt5.get_symbol_specs(symbol)

    sl = float(decision.get("stop_loss") or (price * 0.995 if final == "BUY" else price * 1.005))
    tp = float(decision.get("take_profit") or (price * 1.01 if final == "BUY" else price * 0.99))

    raw_lot = self.risk.calculate_lot_size(balance, price, sl, confidence=confidence,
                                           contract_size=specs["contract_size"]) * size_ratio
    step = specs["volume_step"]

    # ── 래더 구성: AI entry_zone 우선, 없으면 ATR 오프셋 ──
    offsets = getattr(Config, "LIMIT_LADDER_OFFSETS", [0.3, 0.7])   # rung1/rung2
    weights = getattr(Config, "LIMIT_LADDER_WEIGHTS", [0.6, 0.4])   # 물량 배분
    zone = decision.get("entry_zone") or {}
    z_from, z_to = float(zone.get("from") or 0), float(zone.get("to") or 0)
    use_ladder = getattr(Config, "LIMIT_LADDER_ENABLED", True) and atr > 0

    rungs = []
    if use_ladder:
        if z_from > 0 and z_to > 0 and z_from != z_to:
            lo, hi = min(z_from, z_to), max(z_from, z_to)
            prices = [lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75]
            if final == "SELL":
                prices = prices[::-1]           # 가까운 rung부터
        else:
            sign = -1 if final == "BUY" else 1  # 페이드: 신호 반대편에 유리하게
            prices = [price + sign * atr * o for o in offsets]
        for rp, w in zip(prices, weights):
            lot_r = max(specs["volume_min"], int(raw_lot * w / step) * step)
            rungs.append({"price": round(rp, 3), "lot": round(min(lot_r, specs["volume_max"]), 2)})
        # 소액: 두 rung 모두 최소단위로 강제되면 리스크 2배 → rung 1개로 축소
        if len(rungs) == 2 and all(r["lot"] <= specs["volume_min"] for r in rungs):
            rungs = [rungs[0]]
    else:
        lot_m = max(specs["volume_min"], int(raw_lot / step) * step)
        rungs = [{"price": price, "lot": round(min(lot_m, specs["volume_max"]), 2)}]

    # ── 실효 리스크 처리 (현재: 해제 상태 — 최소랏 강행 경로 활성) ──
    total_risk = sum(abs(r["price"] - sl) * specs["contract_size"] * r["lot"] for r in rungs)
    over_cap = balance > 0 and total_risk / balance > self.risk.MAX_RISK_HARD_CAP
    if over_cap and getattr(Config, "SMALL_ACCOUNT_FIT_SL", True):
        ...  # SL-핏 모드 (해제됨): SL을 예산에 맞춰 조이고, 1×ATR 미만이면 보류
    elif over_cap and getattr(Config, "ALLOW_MIN_LOT_OVERRIDE", False):
        rungs = [{"price": rungs[0]["price"], "lot": specs["volume_min"]}]
        total_risk = abs(rungs[0]["price"] - sl) * specs["contract_size"] * specs["volume_min"]
        self.log(f"[최소랏 집행] 구조 SL 유지 ... 리스크 제한 해제 상태", "WARNING")
    elif over_cap and getattr(Config, "REJECT_IF_MIN_LOT_EXCEEDS_CAP", True):
        ...  # 진입 거부 (해제됨)

    # ── 주문 배치 (뮤텍스로 이중 진입 방지) ──
    ttl = getattr(Config, "LIMIT_TTL_MINUTES", 30)
    with self._entry_lock:
        open_units = len(self.mt5.get_open_positions())
        if unit == 1 and not allow_open and open_units >= 1:
            return {"success": False, "message": "락 재확인: 이미 포지션 존재 - 진입 취소"}
        for r in rungs:
            res = self.mt5.place_limit_order(symbol, final, r["lot"], r["price"],
                                             sl, tp, expire_minutes=ttl, ...)
            # 실패한 rung은 로그만, 성공 rung은 DB에 PENDING으로 기록
    # 성공 시: 일일 카운터+1, 중복창 키 갱신, 신호가/스프레드/레이턴시 기록
```

## 14-3. 보유 관리 — 부분익절 + 구조 트레일 + 동적 TP (`_manage_stops` 핵심)

```python
def _manage_stops(self, positions, payload, price) -> dict:
    """Pine 백테스트와 동일 규칙:
      ① +0.75R → 50% 부분익절 + SL 본전 (최소랏이면 부분익절 생략, 본전만)
      ② 이후 구조 트레일: 스윙 저/고점 ± 0.3×ATR (가격 추격이 아니라 구조 뒤)
      ③ 동적 TP: 추세·델타 정렬 시 TP 연장 / 오더플로우 역전 시 TP 당김"""
    atr = float(payload.get("atr", 0) or 0)
    swing_hi = float(payload.get("swing_hi", 0) or 0)
    swing_lo = float(payload.get("swing_lo", 0) or 0)
    mtf, of = payload.get("mtf") or {}, payload.get("of") or {}

    for pos in positions:
        rec = self.db.get_trade(pos["ticket"]) or {}
        orig_sl = rec.get("sl") or pos["sl"] or 0
        risk_dist = abs(pos["open_price"] - orig_sl) if orig_sl > 0 else atr * 2.5
        took = self._took_partial.get(pos["ticket"], False)
        vmin = self._symbol_vmin(pos["symbol"])
        can_split = pos["volume"] > vmin * 1.5   # 0.01랏은 절반 청산 불가

        if pos["type"] == "BUY":
            if not took and price >= entry + risk_dist * 0.75:
                if can_split:  self.mt5.close_position(ticket, volume=pos["volume"] * 0.5)
                else:          pass  # 부분익절 생략 - 본전 잠금 + 러너 유지
                new_sl = max(new_sl or 0, entry)              # 본전
            if took and swing_lo > 0:
                new_sl = max(new_sl, swing_lo - atr * 0.3)    # 구조 트레일
            if tp > 0:
                aligned  = mtf.get("h1") == "up" and of.get("delta_dir") == "buy"
                reversing = of.get("delta_dir") == "sell" and price > entry
                if aligned and tp - price < atr * 2:  new_tp = tp + atr * 1.5   # TP 연장
                elif reversing and took:              new_tp = max(price + atr*0.5, entry + risk_dist*0.5)
        # SELL은 완전 대칭. 변경폭 > 0.05×ATR일 때만 브로커에 수정 요청
```

## 14-4. 리버설 (실측 수익원의 정식화, `_check_reversal`)

```python
def _check_reversal(self, symbol, price, payload, action, opposite, t0):
    """보유 중 반대 방향 신호(반대편 임펄스/스윕 극단) 도착 시:
    AI가 고신뢰(0.75+)로 동의하면 기존 포지션 청산 + 즉시 역방향 래더 진입.
    근거: 백테스트에서 이 경로(83건, +$294)가 전체 순익의 원천."""
    blocked = self._entry_gates(symbol, action, payload)
    if blocked: return None
    payload["reversal_request"] = {
        "note": "반대 방향 포지션 보유 중. 이 신호가 명확한 반대 극단이고 구조가 "
                "실제로 전환됐다고 판단될 때만 승인. 일시 되돌림이면 HOLD가 기본값.",
        "open_positions": [{"type": p["type"], "profit": p.get("profit", 0)} for p in opposite]}
    decision = self.ai.analyze_tradingview(payload)
    if str(decision.get("action","")).upper() != action or conf < 0.75:
        return {"message": "리버설 보류 (기존 포지션 유지)"}     # 로그도 남김
    for pos in opposite:
        r = self.mt5.close_position(pos["ticket"])              # 청산 (exit_reason="reversal")
    return self._execute_entry(..., allow_open=True)            # 역방향 래더
```

## 14-5. Equity Guard / MDD (자동 정지 장치)

```python
def _equity_guard_check(self):
    closed = self.db.closed_profits(...)
    # ① 연속 손실 ≥ 6 → active=False (자동매매 정지, 수동 재개만)
    # ② 최근 20거래 롤링 PF < 0.8 → 정지
    # 주의(§9-6): 마이그레이션된 과거 거래와 이어져 셀 수 있음

# 가드 루프(60초)에서:
dd = self.risk.check_drawdown(equity, max(self.db.peak_equity(), equity))
if dd["breached"]:   # 피크 대비 -25%
    self._close_all_positions("MDD 하드캡")   # 전량 청산 + 대기주문 취소
    self.active = False
    # 주의(§9-2): peak_equity가 계좌 교체를 구분 못함 — 미수정 버그
```

## 14-6. DB 스키마 (core/database.py — 측정 인프라)

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER, symbol TEXT, direction TEXT, lot REAL,
    unit INTEGER DEFAULT 1,            -- 피라미딩 단위
    entry_kind TEXT DEFAULT 'market',  -- market | limit
    signal_price REAL,                 -- 신호 시점 가격
    fill_price REAL,                   -- 실체결가
    slippage REAL,                     -- (체결-신호)×방향; 음수 = 개선
    latency_ms INTEGER,                -- 신호 수신 → 주문 배치
    spread_entry REAL,                 -- 진입 시점 실측 스프레드
    entry_time TEXT, sl REAL, tp REAL,
    risk_usd REAL,                     -- 진입 시점 최대 리스크($)
    confidence REAL, reasoning TEXT,
    ai_report TEXT,                    -- AI 투명성 보고서 전문(JSON)
    status TEXT DEFAULT 'OPEN',        -- OPEN|CLOSED|PENDING|CANCELLED
    exit_time TEXT, exit_price REAL,
    exit_reason TEXT,                  -- sl/tp/partial/structure/reversal/intervention/mdd
    profit REAL DEFAULT 0, r_multiple REAL,
    review TEXT                        -- 거래 종료 후 AI 셀프 리뷰(JSON)
);
CREATE TABLE signals (                 -- 전 신호 + AI 판단 (shadow 데이터)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT, symbol TEXT, pine_action TEXT, payload TEXT,
    ai_action TEXT, ai_confidence REAL, ai_reasoning TEXT, executed INTEGER
);
CREATE TABLE equity (id, time, balance REAL, equity REAL);   -- MDD 피크 소스
```

## 14-7. Webhook Payload 스키마 (Pine → 서버, 매 15분봉)

```json
{
  "symbol": "GOLD#", "secret": "(옵션)", "action": "BUY|SELL|HOLD",
  "mode": "GOLD_STRUCT_V8", "price": 4001.26,
  "impulse": "up|down|none", "body_atr": 1.02, "rsi": 38.2,
  "atr": 5.28, "ema200": 4096.6, "vwap": 4069.6,
  "swing_hi": 4073.27, "swing_lo": 3995.1,
  "sweep": "high_sweep|low_sweep|none", "fvg_up": 0, "fvg_dn": 4012.3,
  "mtf": {"d": "down", "h4": "down", "h1": "down", "m30": "down"},
  "of": {"delta": 2858, "delta_dir": "buy", "cvd_slope": "up",
         "delta_div": "none", "buy_ratio": 0.61},
  "in_session": true, "sl": 3988.06, "tp": 4027.66, "timeframe": "15"
}
```
서버가 AI 호출 직전 추가하는 필드: `trades_left_today`(남은 예산),
`autonomous`(자율 모드), `pyramid_request`/`reversal_request`(상황별 지침).
오더플로우(of)는 **1분봉 분해 프록시**다 — TradingView는 가격대별
Bid/Ask 체결량(진짜 Footprint)을 외부로 제공하지 않는다. 절대값이 아닌
방향·다이버전스만 신뢰하도록 AI에 지시되어 있다.

---

*— 보고서 끝. 반박과 데이터 기반 개선안을 환영한다. 특히 §13의 우선순위에
동의하지 않는다면 그 이유가 가장 듣고 싶은 내용이다.*
