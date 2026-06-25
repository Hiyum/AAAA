from typing import Dict, Any
from config import Config


class RiskManager:

    def __init__(self):
        self.mode = Config.DEFAULT_RISK_MODE          # "auto" or "manual"
        self.risk_per_trade = Config.DEFAULT_RISK_PER_TRADE
        self.daily_loss_limit = Config.DEFAULT_DAILY_LOSS_LIMIT
        self.daily_loss_so_far = 0.0
        self.initial_balance = 0.0

    def set_mode(self, mode: str, risk_per_trade: float = None, daily_loss_limit: float = None):
        self.mode = mode
        if risk_per_trade is not None:
            self.risk_per_trade = risk_per_trade
        if daily_loss_limit is not None:
            self.daily_loss_limit = daily_loss_limit

    def set_initial_balance(self, balance: float):
        self.initial_balance = balance

    def get_risk_per_trade(self, account_balance: float) -> float:
        if self.mode == "auto":
            # 잔고가 클수록 보수적으로
            if account_balance > 50000:
                return 0.01
            elif account_balance > 10000:
                return 0.015
            else:
                return 0.02
        return self.risk_per_trade

    def calculate_lot_size(self, account_balance: float, entry_price: float,
                           stop_loss: float, pip_value: float = 1.0) -> float:
        risk_pct = self.get_risk_per_trade(account_balance)
        risk_amount = account_balance * risk_pct
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0.01
        lot = round(risk_amount / (sl_distance * pip_value * 100), 2)
        return max(0.01, min(lot, 10.0))

    def can_trade(self, account_balance: float) -> Dict[str, Any]:
        if self.initial_balance <= 0:
            return {"allowed": True, "reason": ""}

        loss_pct = (self.initial_balance - account_balance) / self.initial_balance
        limit = self.daily_loss_limit if self.mode == "manual" else 0.05

        if self.mode == "auto":
            if account_balance < 50:
                return {"allowed": False, "reason": "잔고 부족 (최소 $50)"}

        if loss_pct >= limit:
            return {
                "allowed": False,
                "reason": f"일일 손실 한도 초과 ({loss_pct*100:.1f}% / 한도 {limit*100:.1f}%)"
            }

        return {"allowed": True, "reason": ""}

    def record_trade_result(self, profit: float):
        if profit < 0:
            self.daily_loss_so_far += abs(profit)

    def reset_daily(self):
        self.daily_loss_so_far = 0.0

    def get_settings(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "risk_per_trade": self.risk_per_trade,
            "daily_loss_limit": self.daily_loss_limit,
            "daily_loss_so_far": self.daily_loss_so_far,
        }
