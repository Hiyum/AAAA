import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class Backtester:

    def __init__(self, mt5_connector=None):
        self.mt5 = mt5_connector

    def get_historical_data(self, symbol: str, days: int = 30) -> Optional[pd.DataFrame]:
        # MT5 데이터 우선, 실패 시 Yahoo Finance
        if self.mt5 and self.mt5.connected and not self.mt5.simulation_mode:
            df = self.mt5.get_ohlcv(symbol, "M5", days * 288)
            if df is not None and len(df) > 0:
                return df

        return self._fetch_yahoo(symbol, days)

    def _fetch_yahoo(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf

            yf_map = {
                "XAUUSD": "GC=F",
                "GOLD#": "GC=F",
                "XAGUSD": "SI=F",
                "BTCUSD": "BTC-USD",
                "ETHUSD": "ETH-USD",
                "AUDUSD": "AUDUSD=X",
            }
            ticker = yf_map.get(symbol, symbol)
            end = datetime.now()
            start = end - timedelta(days=days)

            df = yf.download(ticker, start=start, end=end, interval="5m", progress=False)
            if df.empty:
                return None

            df.columns = [c.lower() for c in df.columns]
            df.rename(columns={"adj close": "close"}, inplace=True)
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            return df
        except Exception as e:
            logger.error(f"Yahoo Finance 데이터 오류: {e}")
            return None

    def run(self, strategy, symbol: str, days: int = 30,
            initial_balance: float = 10000.0) -> Dict[str, Any]:
        logger.info(f"백테스트 시작: {symbol} | {days}일 | 초기 자금 ${initial_balance:,.0f}")

        df = self.get_historical_data(symbol, days)
        if df is None or len(df) < 50:
            return {"error": "데이터 부족 또는 조회 실패"}

        balance = initial_balance
        trades = []
        open_trade = None
        equity_curve = []

        for i in range(50, len(df)):
            window = df.iloc[:i].copy()
            current_price = float(df['close'].iloc[i])
            bar_time = str(df.index[i])

            equity_curve.append({"time": bar_time, "equity": round(balance, 2)})

            if open_trade:
                action = open_trade["action"]
                sl = open_trade["sl"]
                tp = open_trade["tp"]

                hit_sl = (action == "BUY" and current_price <= sl) or \
                         (action == "SELL" and current_price >= sl)
                hit_tp = (action == "BUY" and current_price >= tp) or \
                         (action == "SELL" and current_price <= tp)

                if hit_sl or hit_tp:
                    exit_price = sl if hit_sl else tp
                    pnl_pips = (exit_price - open_trade["entry"]) if action == "BUY" else (open_trade["entry"] - exit_price)
                    profit = pnl_pips * open_trade["lot"] * 100
                    balance += profit
                    open_trade["exit_price"] = exit_price
                    open_trade["profit"] = round(profit, 2)
                    open_trade["result"] = "WIN" if profit > 0 else "LOSS"
                    open_trade["exit_time"] = bar_time
                    trades.append(open_trade)
                    open_trade = None
                continue

            signal = strategy.analyze(window, current_price, balance)

            if signal.action in ("BUY", "SELL") and signal.confidence >= 0.6:
                lot = max(0.01, min(balance * 0.02 / max(abs(current_price - signal.stop_loss) * 100, 1), 5.0))
                open_trade = {
                    "time": bar_time,
                    "symbol": symbol,
                    "action": signal.action,
                    "entry": current_price,
                    "sl": signal.stop_loss,
                    "tp": signal.take_profit,
                    "lot": round(lot, 2),
                }

        if open_trade:
            last_price = float(df['close'].iloc[-1])
            pnl = (last_price - open_trade["entry"]) if open_trade["action"] == "BUY" else (open_trade["entry"] - last_price)
            profit = pnl * open_trade["lot"] * 100
            open_trade["exit_price"] = last_price
            open_trade["profit"] = round(profit, 2)
            open_trade["result"] = "OPEN"
            trades.append(open_trade)

        return self._summarize(trades, initial_balance, balance, equity_curve, symbol, days)

    def _summarize(self, trades: List[Dict], initial: float, final: float,
                   equity_curve: List, symbol: str, days: int) -> Dict[str, Any]:
        if not trades:
            return {
                "symbol": symbol, "days": days,
                "total_trades": 0, "message": "거래 신호 없음",
                "equity_curve": equity_curve[-100:],
            }

        wins = [t for t in trades if t.get("profit", 0) > 0]
        losses = [t for t in trades if t.get("profit", 0) <= 0]
        total_profit = sum(t.get("profit", 0) for t in trades)
        win_rate = len(wins) / len(trades) * 100

        avg_win = sum(t["profit"] for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t["profit"] for t in losses) / len(losses)) if losses else 1
        profit_factor = (avg_win * len(wins)) / max(avg_loss * len(losses), 0.01)

        max_dd = 0
        peak = initial
        running = initial
        for t in trades:
            running += t.get("profit", 0)
            peak = max(peak, running)
            dd = (peak - running) / peak * 100
            max_dd = max(max_dd, dd)

        return {
            "symbol": symbol,
            "days": days,
            "initial_balance": initial,
            "final_balance": round(final, 2),
            "total_profit": round(total_profit, 2),
            "return_pct": round((final - initial) / initial * 100, 2),
            "total_trades": len(trades),
            "win_rate": round(win_rate, 1),
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "trades": trades[-20:],
            "equity_curve": equity_curve[-200:],
        }
