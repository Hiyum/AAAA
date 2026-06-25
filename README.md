# AI 자동매매 시스템

Claude AI + MetaTrader 5 + TradingView 기반 스캘핑 자동매매 시스템

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
```

### 3. 실행

```bash
python app.py
```

브라우저에서 `http://localhost:5000` 접속

---

## 사용 방법

1. **MT5 연결** - 좌측 사이드바에서 계좌 정보 입력 후 연결
2. **AI 자동매매 시작** - "AI 자동매매 시작" 버튼 클릭
3. **실시간 로그** - 대시보드에서 AI 분석 상황 확인
4. **중단** - "AI 자동매매 중단" 버튼 클릭

---

## TradingView Webhook 설정

TradingView에서 Alert 생성 시 Webhook URL:
```
http://your-server:5000/webhook/tradingview
```

Alert 메시지 형식 (JSON):
```json
{
  "action": "BUY",
  "symbol": "XAUUSD",
  "price": {{close}}
}
```

---

## 전략 교체 방법

1. `strategies/scalping_strategy.py` 파일 내용 삭제
2. Claude AI에게 요청:
   ```
   "다음 트레이더 기술을 BaseStrategy를 상속받는 Python 클래스로
   구현해줘. analyze() 메서드를 구현해야 해: [전략 설명]"
   ```
3. 받은 코드를 `strategies/scalping_strategy.py`에 붙여넣기
4. 대시보드 "전략 관리" 탭에서 "전략 다시 로드" 클릭

---

## 프로젝트 구조

```
├── app.py                      # 웹앱 메인
├── config.py                   # 설정
├── requirements.txt
├── strategies/
│   ├── base_strategy.py        # 전략 인터페이스 (수정 금지)
│   └── scalping_strategy.py    # 교체 가능한 전략 파일
├── core/
│   ├── mt5_connector.py        # MT5 연동
│   ├── claude_ai.py            # Claude AI 분석
│   ├── risk_manager.py         # 위험 관리
│   ├── trading_engine.py       # 매매 엔진
│   └── backtest.py             # 백테스트
└── dashboard/
    └── templates/index.html    # 웹 대시보드
```
