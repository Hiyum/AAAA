"""
=============================================================
  ICT + SMC 스캘핑 전략
  (Inner Circle Trader / Smart Money Concepts)
=============================================================
  기반 트레이더:
  - Michael Huddleston (ICT): Order Block, Fair Value Gap, 시장 구조
  - Linda Raschke: 모멘텀 + VWAP 조합
  - Al Brooks: 가격 행동(Price Action) 분석

  핵심 개념:
  1. Market Structure (BOS/CHoCH): 시장 구조 파악
  2. Order Block: 기관 매수/매도 블록 식별
  3. Fair Value Gap (FVG): 가격 공백 구간 탐지
  4. VWAP: 기관 기준 가격선
  5. RSI + ATR: 모멘텀 및 변동성 필터
=============================================================
  교체하려면 이 파일 전체를 삭제하고 새 코드를 붙여넣으세요.
=============================================================
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy, Signal


class ScalpingStrategy(BaseStrategy):

    def __init__(self, symbol: str, timeframe: str = "M5"):
        super().__init__(symbol, timeframe)
        self.name = "ICT/SMC Smart Money Scalping"

        # 파라미터
        self.rsi_period = 14
        self.atr_period = 14
        self.vwap_period = 20
        self.ob_lookback = 10        # Order Block 탐색 구간
        self.fvg_min_size = 0.0002   # FVG 최소 크기 (가격 대비 비율)
        self.rr_ratio = 2.0          # Risk:Reward 비율

    # ── 지표 계산 ──────────────────────────────────────────

    def _rsi(self, series: pd.Series) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        hl = df['high'] - df['low']
        hc = (df['high'] - df['close'].shift()).abs()
        lc = (df['low'] - df['close'].shift()).abs()
        return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(self.atr_period).mean()

    def _vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df['high'] + df['low'] + df['close']) / 3
        vol = df['volume'].replace(0, 1)
        return (typical * vol).rolling(self.vwap_period).sum() / vol.rolling(self.vwap_period).sum()

    # ── 시장 구조 분석 (ICT: BOS / CHoCH) ─────────────────

    def _market_structure(self, df: pd.DataFrame) -> str:
        """최근 스윙 기반 시장 구조 판단"""
        n = 5
        highs = df['high'].rolling(n).max()
        lows = df['low'].rolling(n).min()

        recent_high = highs.iloc[-1]
        prev_high = highs.iloc[-n-1]
        recent_low = lows.iloc[-1]
        prev_low = lows.iloc[-n-1]

        if recent_high > prev_high and recent_low > prev_low:
            return "BULLISH"   # Higher High + Higher Low
        elif recent_high < prev_high and recent_low < prev_low:
            return "BEARISH"   # Lower High + Lower Low
        return "RANGING"

    # ── Order Block 탐지 (ICT 핵심 개념) ──────────────────

    def _find_order_block(self, df: pd.DataFrame, direction: str):
        """
        Order Block: 큰 임팩트 캔들 직전의 마지막 반대 방향 캔들
        기관이 주문을 쌓아둔 가격대 → 가격이 돌아올 때 반응
        """
        lookback = df.iloc[-self.ob_lookback-1:-1]

        if direction == "BULLISH":
            # 강한 상승 캔들 찾기
            big_up = lookback[lookback['close'] > lookback['open']]
            big_up = big_up[(big_up['close'] - big_up['open']) >
                            (lookback['high'] - lookback['low']).mean() * 0.7]
            if big_up.empty:
                return None
            # 그 직전 하락 캔들 = Bullish Order Block
            idx = lookback.index.get_loc(big_up.index[-1])
            if idx == 0:
                return None
            ob_candle = lookback.iloc[idx - 1]
            return {"high": ob_candle['high'], "low": ob_candle['low'], "type": "BULLISH_OB"}

        elif direction == "BEARISH":
            # 강한 하락 캔들 찾기
            big_dn = lookback[lookback['close'] < lookback['open']]
            big_dn = big_dn[(big_dn['open'] - big_dn['close']) >
                            (lookback['high'] - lookback['low']).mean() * 0.7]
            if big_dn.empty:
                return None
            idx = lookback.index.get_loc(big_dn.index[-1])
            if idx == 0:
                return None
            ob_candle = lookback.iloc[idx - 1]
            return {"high": ob_candle['high'], "low": ob_candle['low'], "type": "BEARISH_OB"}

        return None

    # ── Fair Value Gap 탐지 (ICT FVG) ──────────────────────

    def _find_fvg(self, df: pd.DataFrame) -> dict:
        """
        Fair Value Gap: 3개 캔들 사이의 가격 공백
        캔들1 고가 < 캔들3 저가 → Bullish FVG (상승 공백)
        캔들1 저가 > 캔들3 고가 → Bearish FVG (하락 공백)
        """
        results = []
        for i in range(2, min(len(df), 15)):
            c1 = df.iloc[-i-1]
            c3 = df.iloc[-i+1]

            # Bullish FVG
            if c1['high'] < c3['low']:
                gap_size = (c3['low'] - c1['high']) / c1['high']
                if gap_size >= self.fvg_min_size:
                    results.append({
                        "type": "BULLISH_FVG",
                        "high": c3['low'],
                        "low": c1['high'],
                        "size": gap_size,
                    })

            # Bearish FVG
            elif c1['low'] > c3['high']:
                gap_size = (c1['low'] - c3['high']) / c1['low']
                if gap_size >= self.fvg_min_size:
                    results.append({
                        "type": "BEARISH_FVG",
                        "high": c1['low'],
                        "low": c3['high'],
                        "size": gap_size,
                    })

        return results[0] if results else None

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
        if len(df) < 50:
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01, "데이터 부족")

        df = df.copy()
        df['rsi'] = self._rsi(df['close'])
        df['atr'] = self._atr(df)
        df['vwap'] = self._vwap(df)

        last = df.iloc[-1]
        rsi = last['rsi']
        atr = last['atr']
        vwap = last['vwap']

        if pd.isna(rsi) or pd.isna(atr) or pd.isna(vwap):
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01, "지표 계산 중...")

        # 1. 시장 구조 파악
        structure = self._market_structure(df)

        # 2. FVG 탐지
        fvg = self._find_fvg(df)

        # 3. Order Block 탐지
        bullish_ob = self._find_order_block(df, "BULLISH")
        bearish_ob = self._find_order_block(df, "BEARISH")

        # 4. VWAP 위치
        above_vwap = current_price > vwap
        below_vwap = current_price < vwap

        reasons = []
        score_buy = 0
        score_sell = 0

        # ── 매수 조건 점수 계산 ──────────────────────────────
        if structure == "BULLISH":
            score_buy += 2
            reasons.append("시장구조:상승")

        if above_vwap:
            score_buy += 1
            reasons.append("VWAP 위")

        if rsi < 45:
            score_buy += 1
            reasons.append(f"RSI:{rsi:.0f}(낮음)")

        if rsi < 30:
            score_buy += 2
            reasons.append("RSI 과매도")

        if fvg and fvg['type'] == "BULLISH_FVG":
            if fvg['low'] <= current_price <= fvg['high']:
                score_buy += 3
                reasons.append("Bullish FVG 진입구간")

        if bullish_ob:
            if bullish_ob['low'] <= current_price <= bullish_ob['high']:
                score_buy += 3
                reasons.append("Bullish Order Block 접촉")

        # ── 매도 조건 점수 계산 ──────────────────────────────
        if structure == "BEARISH":
            score_sell += 2

        if below_vwap:
            score_sell += 1

        if rsi > 55:
            score_sell += 1
            reasons.append(f"RSI:{rsi:.0f}(높음)")

        if rsi > 70:
            score_sell += 2
            reasons.append("RSI 과매수")

        if fvg and fvg['type'] == "BEARISH_FVG":
            if fvg['low'] <= current_price <= fvg['high']:
                score_sell += 3
                reasons.append("Bearish FVG 진입구간")

        if bearish_ob:
            if bearish_ob['low'] <= current_price <= bearish_ob['high']:
                score_sell += 3
                reasons.append("Bearish Order Block 접촉")

        reason_str = " | ".join(reasons) if reasons else "조건 미충족"

        # ── 신호 생성 (최소 점수 4점 이상) ──────────────────
        if score_buy >= 4 and score_buy > score_sell:
            sl = current_price - (atr * 1.5)
            tp = current_price + (atr * 1.5 * self.rr_ratio)
            lot = self._lot_size(account_balance, current_price, sl)
            confidence = min(0.95, 0.5 + score_buy * 0.07)
            return Signal("BUY", self.symbol, confidence, current_price, sl, tp, lot,
                          f"[ICT/SMC 매수] {reason_str} | 점수:{score_buy}")

        elif score_sell >= 4 and score_sell > score_buy:
            sl = current_price + (atr * 1.5)
            tp = current_price - (atr * 1.5 * self.rr_ratio)
            lot = self._lot_size(account_balance, current_price, sl)
            confidence = min(0.95, 0.5 + score_sell * 0.07)
            return Signal("SELL", self.symbol, confidence, current_price, sl, tp, lot,
                          f"[ICT/SMC 매도] {reason_str} | 점수:{score_sell}")

        return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
                      f"대기 중 | 구조:{structure} | RSI:{rsi:.0f} | 매수점수:{score_buy} 매도점수:{score_sell}")
