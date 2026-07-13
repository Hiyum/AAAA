# AI 자동 GOLD 트레이딩 시스템 (v8)

TradingView(다중 시간 프레임 분석) → Claude AI(구조 판단) → MT5(지정가 집행)

## 빠른 시작

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 아래 값을 입력하세요:

```
ANTHROPIC_API_KEY=your_claude_api_key
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_password
MT5_SERVER=your_broker_server
WEBHOOK_SECRET=원하는_비밀문자열   # Pine 입력의 '웹훅 시크릿'과 동일하게
```

### 3. 실행

```bash
python app.py        # http://localhost:5000
ngrok http 5000      # 외부 URL 발급 (TradingView용)
```

## TradingView 설정

1. GOLD(XAUUSD) **15분 차트**에 `tradingview_strategy.pine` 전체를 붙여넣기
   (Pine Editor 기존 내용 전부 삭제 후)
2. 입력에서 **MT5 종목명**(브로커 표기)과 **웹훅 시크릿**(.env와 동일) 설정
3. 알람 생성: 조건 = 이 전략, 유형 = **"Any alert() function call"**
4. Webhook URL: `https://<ngrok주소>/webhook/tradingview`

## v8 시스템 특징

- **집행**: 시장가 폐기 → 지정가 래더 (스프레드를 지불하지 않고 받는 구조)
- **판단**: 1D/4H/1H/30M 다중 프레임 + 오더플로우 프록시(1분 분해
  delta/CVD/다이버전스) + 스윙/스윕/FVG/VWAP를 Claude AI가 종합
- **보유**: 당일 강제 청산 폐기 — 시장 구조가 청산을 결정.
  단 MDD 하드캡(피크 대비 -25%)은 절대 방어선
- **리스크**: 최소 주문단위 리스크가 하드캡(5%) 초과 시 **진입 거부**
  (소액 계좌 보호), confidence 비례 포지션 크기
- **기록**: SQLite (`logs/trading.db`) — 신호가/실체결가/슬리피지/레이턴시/
  AI 판단/거래 리뷰 영구 보존
- **자가 발전**: 거래 종료마다 AI 셀프 리뷰, 10건마다 성과 자가 평가 +
  `reports/`에 Claude Code용 개선 프롬프트 자동 생성 (`POST /api/self_improve`)

## 프로젝트 구조

```
├── app.py                      # Flask 서버 (웹훅/대시보드/성과 API)
├── config.py                   # 전체 설정
├── tradingview_strategy.pine   # TradingView 전략 v8 (분석+백테스트+알람)
├── core/
│   ├── trading_engine.py       # 파이프라인 (래더/피라미딩/감시/정합)
│   ├── mt5_connector.py        # MT5 (지정가/부분청산/딜 내역)
│   ├── claude_ai.py            # AI 판단/개입/리뷰/자기개선
│   ├── risk_manager.py         # lot 계산/리스크 검증/MDD
│   └── database.py             # SQLite 영구 저장소
├── dashboard/templates/index.html
├── logs/trading.db             # (런타임 생성)
└── reports/                    # 자기개선 프롬프트 (런타임 생성)
```

## 성과 확인

- `GET /api/report` — 승률/PF/기대값/R:R/MDD/Recovery/평균 슬리피지·레이턴시
- `POST /api/self_improve` — 자기 평가 + 개선 프롬프트 즉시 생성

상세 아키텍처와 설계 이력: `HANDOFF_FOR_CHATGPT.md`
