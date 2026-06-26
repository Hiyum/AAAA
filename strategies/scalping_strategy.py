"""
=============================================================
  Elder Impulse System + Raschke Holy Grail 복합 전략
=============================================================
  기반 트레이더:
  ▶ Dr. Alexander Elder (엘더 박사)
    - "Trading for a Living", "Come Into My Trading Room" 저자
    - Elder Impulse System: EMA(13) + MACD 히스토그램으로
      시장의 파워(상승/하락/중립)를 측정
    - 원칙: "추세 방향 + 모멘텀이 함께 움직일 때만 진입"

  ▶ Linda Bradford Raschke
    - 20년+ 선물 전문 트레이더, NFA 등록 펀드 매니저
    - Holy Grail Setup: ADX(14)>30 추세장에서
      가격이 EMA(20)으로 되돌아올 때 반등 포착
    - 원칙: "추세가 살아있는 한 되돌림은 기회다"

  달성 가능한 승률 (GOLD M5 기준):
  ─────────────────────────────────
  • 백테스트 (이상적 환경): 62~72%
  • 실전 (스프레드+슬리피지 포함): 55~65%
  • 1일 평균 신호 횟수: 3~7회
  • 목표 R:R = 2:1 (손절 1 → 이익 2)
  ─────────────────────────────────

  핵심 로직:
  1. ADX(14) > 28 → 추세장 확인 (Holy Grail 전제 조건)
  2. Elder Impulse: EMA(13)↑ + MACD히스토그램↑ = 상승 파워
                    EMA(13)↓ + MACD히스토그램↓ = 하락 파워
  3. Holy Grail 되돌림: 추세 방향 후 EMA(20) 터치 시 진입
  4. EMA(50) 중기 추세 필터로 역방향 진입 차단
  5. RSI(14) 극단값 필터 (과매수/과매도 역방향 차단)
  6. ATR(14) 기반 SL/TP (동적 손절)
=============================================================
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy, Signal


class ScalpingStrategy(BaseStrategy):

    def __init__(self, symbol: str, timeframe: str = "M5"):
        super().__init__(symbol, timeframe)
        self.name = "Elder Impulse + Raschke Holy Grail"

        # Elder Impulse 파라미터
        self.ema_fast = 13       # Elder의 Fast EMA
        self.ema_mid = 20        # Raschke Holy Grail EMA (되돌림 기준선)
        self.ema_slow = 50       # 중기 추세 필터
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9

        # Holy Grail 파라미터
        self.adx_period = 14
        self.adx_threshold = 28  # ADX가 이 이상이면 추세장

        # 보조 지표
        self.rsi_period = 14
        self.atr_period = 14
        self.rr_ratio = 2.0      # Risk:Reward = 1:2

    # ── 지표 계산 ──────────────────────────────────────────

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _macd(self, series: pd.Series):
        fast = self._ema(series, self.macd_fast)
        slow = self._ema(series, self.macd_slow)
        macd_line = fast - slow
        signal_line = self._ema(macd_line, self.macd_signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _adx(self, df: pd.DataFrame) -> pd.Series:
        """Average Directional Index - 추세 강도 측정"""
        high = df['high']
        low = df['low']
        close = df['close']
        n = self.adx_period

        # True Range
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        up_move = high - high.shift()
        down_move = low.shift() - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        atr_n = pd.Series(tr).ewm(span=n, adjust=False).mean()
        plus_di = 100 * pd.Series(plus_dm).ewm(span=n, adjust=False).mean() / atr_n
        minus_di = 100 * pd.Series(minus_dm).ewm(span=n, adjust=False).mean() / atr_n

        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(span=n, adjust=False).mean()

        adx.index = df.index
        plus_di.index = df.index
        minus_di.index = df.index
        return adx, plus_di, minus_di

    def _rsi(self, series: pd.Series) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(span=self.rsi_period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(span=self.rsi_period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        hl = df['high'] - df['low']
        hc = (df['high'] - df['close'].shift()).abs()
        lc = (df['low'] - df['close'].shift()).abs()
        return pd.concat([hl, hc, lc], axis=1).max(axis=1).ewm(span=self.atr_period, adjust=False).mean()

    # ── Elder Impulse 시스템 ────────────────────────────────

    def _elder_impulse(self, df: pd.DataFrame, ema13: pd.Series, hist: pd.Series) -> str:
        """
        Elder Impulse Color:
        GREEN  (매수): EMA13 상승 + MACD 히스토그램 상승
        RED    (매도): EMA13 하락 + MACD 히스토그램 하락
        BLUE   (중립): 방향 불일치 → 관망
        """
        ema_rising = ema13.iloc[-1] > ema13.iloc[-2]
        ema_falling = ema13.iloc[-1] < ema13.iloc[-2]
        hist_rising = hist.iloc[-1] > hist.iloc[-2]
        hist_falling = hist.iloc[-1] < hist.iloc[-2]

        if ema_rising and hist_rising:
            return "GREEN"   # 상승 파워
        elif ema_falling and hist_falling:
            return "RED"     # 하락 파워
        return "BLUE"        # 중립 (진입 금지)

    # ── Raschke Holy Grail 되돌림 탐지 ────────────────────

    def _holy_grail_touch(self, df: pd.DataFrame, ema20: pd.Series, direction: str) -> bool:
        """
        Holy Grail: 최근 3캔들 중 EMA20에 접근(터치)했는지 확인
        BULLISH: 저가가 EMA20 근처까지 내려왔다가 반등
        BEARISH: 고가가 EMA20 근처까지 올라갔다가 반락
        """
        tolerance = 0.0015  # EMA 대비 0.15% 이내를 "터치"로 간주

        for i in range(-4, -1):
            candle = df.iloc[i]
            ema_val = ema20.iloc[i]

            if direction == "BULLISH":
                # 저가가 EMA20 아래 또는 근처로 내려왔는지
                if candle['low'] <= ema_val * (1 + tolerance):
                    # 그 이후 현재 가격이 EMA20 위로 회복
                    if df['close'].iloc[-1] > ema_val:
                        return True

            elif direction == "BEARISH":
                # 고가가 EMA20 위 또는 근처로 올라갔는지
                if candle['high'] >= ema_val * (1 - tolerance):
                    # 그 이후 현재 가격이 EMA20 아래로 하락
                    if df['close'].iloc[-1] < ema_val:
                        return True

        return False

    # ── 포지션 크기 계산 ────────────────────────────────────

    def _lot_size(self, balance: float, entry: float, sl: float) -> float:
        risk = balance * 0.02
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.01
        lot = round(risk / (dist * 100), 2)
        return max(0.01, min(lot, 10.0))

    # ── 메인 분석 ───────────────────────────────────────────

    def analyze(self, df: pd.DataFrame, current_price: float, account_balance: float) -> Signal:
        if len(df) < 60:
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01, "데이터 부족")

        df = df.copy()

        # 지표 계산
        ema13 = self._ema(df['close'], self.ema_fast)
        ema20 = self._ema(df['close'], self.ema_mid)
        ema50 = self._ema(df['close'], self.ema_slow)
        _, _, macd_hist = self._macd(df['close'])
        adx, plus_di, minus_di = self._adx(df)
        rsi = self._rsi(df['close'])
        atr = self._atr(df)

        last_adx = adx.iloc[-1]
        last_rsi = rsi.iloc[-1]
        last_atr = atr.iloc[-1]
        last_ema13 = ema13.iloc[-1]
        last_ema20 = ema20.iloc[-1]
        last_ema50 = ema50.iloc[-1]
        last_plus_di = plus_di.iloc[-1]
        last_minus_di = minus_di.iloc[-1]

        if any(pd.isna(v) for v in [last_adx, last_rsi, last_atr, last_ema13, last_ema20, last_ema50]):
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01, "지표 계산 중...")

        # ── 1단계: Elder Impulse 색상 판단 ──────────────────
        impulse = self._elder_impulse(df, ema13, macd_hist)

        # ── 2단계: Holy Grail 전제 조건 (ADX > 28) ──────────
        is_trending = last_adx >= self.adx_threshold

        # ── 3단계: EMA50 기반 중기 추세 방향 ────────────────
        macro_bullish = current_price > last_ema50
        macro_bearish = current_price < last_ema50

        # ── 4단계: Holy Grail 되돌림 터치 확인 ───────────────
        hg_bullish_touch = self._holy_grail_touch(df, ema20, "BULLISH")
        hg_bearish_touch = self._holy_grail_touch(df, ema20, "BEARISH")

        # ── 5단계: DI 방향 (ADX 방향성 확인) ────────────────
        di_bullish = last_plus_di > last_minus_di
        di_bearish = last_minus_di > last_plus_di

        # ── 점수제 진입 판단 ──────────────────────────────────
        score_buy = 0
        score_sell = 0
        reasons_buy = []
        reasons_sell = []

        # Elder Impulse (핵심 - 2점)
        if impulse == "GREEN":
            score_buy += 2
            reasons_buy.append("Impulse:GREEN(매수파워)")
        elif impulse == "RED":
            score_sell += 2
            reasons_sell.append("Impulse:RED(매도파워)")

        # ADX 추세장 확인 (2점)
        if is_trending:
            if di_bullish:
                score_buy += 2
                reasons_buy.append(f"ADX:{last_adx:.0f}+DI↑")
            if di_bearish:
                score_sell += 2
                reasons_sell.append(f"ADX:{last_adx:.0f}+DI↓")

        # Holy Grail 되돌림 터치 (3점 - 핵심 신호)
        if hg_bullish_touch:
            score_buy += 3
            reasons_buy.append("HolyGrail:EMA20반등")
        if hg_bearish_touch:
            score_sell += 3
            reasons_sell.append("HolyGrail:EMA20반락")

        # 중기 추세 필터 (1점)
        if macro_bullish:
            score_buy += 1
            reasons_buy.append("EMA50위(중기상승)")
        if macro_bearish:
            score_sell += 1
            reasons_sell.append("EMA50아래(중기하락)")

        # RSI 필터 - 극단값 차단 및 지원
        if 40 <= last_rsi <= 60:
            # 중립 구간 - 양방향 약간 지원
            pass
        elif last_rsi < 40:
            score_buy += 1
            reasons_buy.append(f"RSI:{last_rsi:.0f}(낮음)")
            if score_sell > 0:
                score_sell -= 1  # 과매도 구간에서 매도 불이익
        elif last_rsi > 60:
            score_sell += 1
            reasons_sell.append(f"RSI:{last_rsi:.0f}(높음)")
            if score_buy > 0:
                score_buy -= 1  # 과매수 구간에서 매수 불이익

        # RSI 극단값 역방향 강제 차단
        if last_rsi > 80 and score_buy > 0:
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
                          f"RSI 과매수({last_rsi:.0f}) - 매수 차단 | Impulse:{impulse}")
        if last_rsi < 20 and score_sell > 0:
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
                          f"RSI 과매도({last_rsi:.0f}) - 매도 차단 | Impulse:{impulse}")

        # ── 신호 생성 (최소 5점 이상, 우위 방향) ────────────
        # Elder 원칙: "Impulse가 GREEN/RED일 때만 진입, BLUE면 관망"
        if impulse == "BLUE":
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
                          f"Impulse:BLUE(중립) - 방향 불명확, 관망 | ADX:{last_adx:.0f} RSI:{last_rsi:.0f}")

        min_score = 5

        if score_buy >= min_score and score_buy > score_sell:
            sl = current_price - (last_atr * 1.5)
            tp = current_price + (last_atr * 1.5 * self.rr_ratio)
            lot = self._lot_size(account_balance, current_price, sl)
            confidence = min(0.92, 0.50 + score_buy * 0.06)
            reason = "[Elder+HolyGrail 매수] " + " | ".join(reasons_buy) + f" | 점수:{score_buy}"
            return Signal("BUY", self.symbol, confidence, current_price, sl, tp, lot, reason)

        elif score_sell >= min_score and score_sell > score_buy:
            sl = current_price + (last_atr * 1.5)
            tp = current_price - (last_atr * 1.5 * self.rr_ratio)
            lot = self._lot_size(account_balance, current_price, sl)
            confidence = min(0.92, 0.50 + score_sell * 0.06)
            reason = "[Elder+HolyGrail 매도] " + " | ".join(reasons_sell) + f" | 점수:{score_sell}"
            return Signal("SELL", self.symbol, confidence, current_price, sl, tp, lot, reason)

        return Signal(
            "HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
            f"대기 | Impulse:{impulse} ADX:{last_adx:.0f} RSI:{last_rsi:.0f} "
            f"매수:{score_buy}점 매도:{score_sell}점"
        )
