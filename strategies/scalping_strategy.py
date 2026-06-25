"""
=============================================================
  기본 스캘핑 전략 - EMA + RSI + ATR
=============================================================
  교체하려면 이 파일 전체를 삭제하고 새 코드를 붙여넣으세요.
  BaseStrategy 인터페이스만 지키면 됩니다.
=============================================================
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy, Signal


class ScalpingStrategy(BaseStrategy):

    def __init__(self, symbol: str, timeframe: str = "M5"):
        super().__init__(symbol, timeframe)
        self.name = "EMA+RSI Scalping"
        self.ema_fast = 9
        self.ema_slow = 21
        self.rsi_period = 14
        self.rsi_overbought = 70
        self.rsi_oversold = 30
        self.atr_period = 14
        self.risk_reward_ratio = 2.0

    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _calculate_rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(window=period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _calculate_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def _calculate_lot_size(self, account_balance: float, stop_loss_pips: float, risk_pct: float = 0.02) -> float:
        risk_amount = account_balance * risk_pct
        if stop_loss_pips <= 0:
            return 0.01
        lot = round(risk_amount / (stop_loss_pips * 10), 2)
        return max(0.01, min(lot, 10.0))

    def analyze(self, df: pd.DataFrame, current_price: float, account_balance: float) -> Signal:
        if len(df) < self.ema_slow + 5:
            return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01, "데이터 부족")

        df = df.copy()
        df['ema_fast'] = self._calculate_ema(df['close'], self.ema_fast)
        df['ema_slow'] = self._calculate_ema(df['close'], self.ema_slow)
        df['rsi'] = self._calculate_rsi(df['close'], self.rsi_period)
        df['atr'] = self._calculate_atr(df, self.atr_period)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        atr = last['atr']
        rsi = last['rsi']
        ema_fast = last['ema_fast']
        ema_slow = last['ema_slow']
        prev_ema_fast = prev['ema_fast']
        prev_ema_slow = prev['ema_slow']

        ema_cross_up = prev_ema_fast <= prev_ema_slow and ema_fast > ema_slow
        ema_cross_down = prev_ema_fast >= prev_ema_slow and ema_fast < ema_slow

        if ema_cross_up and rsi < self.rsi_overbought:
            sl = current_price - (atr * 1.5)
            tp = current_price + (atr * 1.5 * self.risk_reward_ratio)
            sl_pips = abs(current_price - sl) * 10000
            lot = self._calculate_lot_size(account_balance, sl_pips)
            confidence = min(0.9, 0.5 + (self.rsi_oversold - min(rsi, self.rsi_oversold)) / 100)
            return Signal("BUY", self.symbol, confidence, current_price, sl, tp, lot,
                          f"EMA 골든크로스 | RSI: {rsi:.1f} | ATR: {atr:.5f}")

        elif ema_cross_down and rsi > self.rsi_oversold:
            sl = current_price + (atr * 1.5)
            tp = current_price - (atr * 1.5 * self.risk_reward_ratio)
            sl_pips = abs(sl - current_price) * 10000
            lot = self._calculate_lot_size(account_balance, sl_pips)
            confidence = min(0.9, 0.5 + (min(rsi, self.rsi_overbought) - self.rsi_overbought) / 100 * -1)
            return Signal("SELL", self.symbol, confidence, current_price, sl, tp, lot,
                          f"EMA 데드크로스 | RSI: {rsi:.1f} | ATR: {atr:.5f}")

        return Signal("HOLD", self.symbol, 0.0, current_price, 0, 0, 0.01,
                      f"신호 없음 | EMA Fast: {ema_fast:.5f} | EMA Slow: {ema_slow:.5f} | RSI: {rsi:.1f}")
