from typing import Dict, Any
from config import Config


class RiskManager:

    def __init__(self):
        self.mode = Config.DEFAULT_RISK_MODE          # "auto" or "manual"
        self.risk_per_trade = Config.DEFAULT_RISK_PER_TRADE
        self.daily_loss_limit = Config.DEFAULT_DAILY_LOSS_LIMIT
        self.daily_loss_so_far = 0.0
        self.initial_balance = 0.0
        self.fixed_lot = 0.0   # 0이면 자동계산, 0보다 크면 고정 lot 사용

    def set_mode(self, mode: str, risk_per_trade: float = None,
                 daily_loss_limit: float = None, fixed_lot: float = None):
        self.mode = mode
        if risk_per_trade is not None:
            self.risk_per_trade = risk_per_trade
        if daily_loss_limit is not None:
            self.daily_loss_limit = daily_loss_limit
        if fixed_lot is not None:
            self.fixed_lot = fixed_lot

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

    # 신뢰도 → lot 배수 등급표 (Claude AI confidence 기반)
    # 확신이 클수록 과감하게, 단 하드캡(계좌의 5% 리스크) 안에서만.
    # "100% 보장"은 존재하지 않으므로 무제한 베팅은 절대 하지 않음.
    CONFIDENCE_TIERS = [
        (0.92, 3.0),   # 초고신뢰 (드물어야 정상)
        (0.85, 2.0),   # 고신뢰
        (0.75, 1.5),   # 중상
        (0.65, 1.0),   # 기본
        (0.00, 0.5),   # 저신뢰 → 절반
    ]
    MAX_RISK_HARD_CAP = 0.05   # 어떤 경우에도 한 거래 리스크는 잔고의 5% 이하

    def confidence_multiplier(self, confidence: float) -> float:
        for threshold, mult in self.CONFIDENCE_TIERS:
            if confidence >= threshold:
                return mult
        return 0.5

    def calculate_lot_size(self, account_balance: float, entry_price: float,
                           stop_loss: float, confidence: float = 0.65,
                           contract_size: float = 100000.0) -> float:
        """
        리스크 금액 기반 lot 계산.
        1 lot 손실액 = 손절거리 × 계약크기(외환 100,000 / 금 100)
        ※ 이전 버전은 금 기준(×100) 고정이라 외환에서 1000배 과대 주문 버그 있었음.
        """
        mult = self.confidence_multiplier(confidence)

        # 고정 lot 모드: 고정값 × 신뢰도 배수
        if self.fixed_lot > 0:
            return round(max(0.01, min(self.fixed_lot * mult, 10.0)), 2)

        # 리스크 % 모드: (기본 리스크 × 신뢰도 배수), 하드캡 5%
        risk_pct = min(self.get_risk_per_trade(account_balance) * mult, self.MAX_RISK_HARD_CAP)
        risk_amount = account_balance * risk_pct
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0 or contract_size <= 0:
            return 0.01
        risk_per_lot = sl_distance * contract_size
        lot = risk_amount / risk_per_lot
        return max(0.0, lot)   # 최소단위 반올림은 엔진이 종목 스펙으로 처리

    def validate_effective_risk(self, account_balance: float, entry_price: float,
                                stop_loss: float, lot: float,
                                contract_size: float) -> Dict[str, Any]:
        """
        브로커 최소 lot 강제 상향 '이후'의 실효 리스크 검증.
        소액 계좌($100)에서 volume_min=0.01이 이론 lot(0.002)을 덮어써
        거래당 리스크가 10%+로 폭주하던 산술 붕괴를 여기서 차단한다.
        실효 리스크 > 하드캡이면 진입 거부가 정답 — 조용한 초과 리스크는 금지.
        """
        sl_distance = abs(entry_price - stop_loss)
        risk_usd = sl_distance * contract_size * lot
        if account_balance <= 0:
            return {"ok": False, "risk_usd": risk_usd, "risk_pct": 1.0,
                    "reason": "잔고 조회 실패"}
        risk_pct = risk_usd / account_balance
        if risk_pct > self.MAX_RISK_HARD_CAP:
            return {
                "ok": False, "risk_usd": round(risk_usd, 2), "risk_pct": risk_pct,
                "reason": (f"최소 주문단위 리스크 {risk_pct*100:.1f}% > 하드캡 "
                           f"{self.MAX_RISK_HARD_CAP*100:.0f}% - 진입 거부 "
                           f"(손절폭 ${sl_distance:.2f} × {lot} lot, 잔고 ${account_balance:.0f})"),
            }
        return {"ok": True, "risk_usd": round(risk_usd, 2), "risk_pct": risk_pct, "reason": ""}

    def check_drawdown(self, current_equity: float, peak_equity: float) -> Dict[str, Any]:
        """Max Drawdown 하드캡 — 계좌 생존의 최후 방어선 (시간 청산 대신 도입)"""
        from config import Config
        cap = getattr(Config, "MAX_DRAWDOWN_PCT", 0.25)
        if peak_equity <= 0 or current_equity <= 0:
            return {"breached": False, "dd_pct": 0.0}
        dd = (peak_equity - current_equity) / peak_equity
        return {"breached": dd >= cap, "dd_pct": dd, "cap": cap}

    def can_trade(self, account_balance: float) -> Dict[str, Any]:
        if self.initial_balance <= 0:
            return {"allowed": True, "reason": ""}

        loss_pct = (self.initial_balance - account_balance) / self.initial_balance
        # auto 모드도 5% 고정 대신 설정값 사용 (소액 계좌: 한 번 손실에 하루가 끝나지 않게)
        limit = self.daily_loss_limit

        if self.mode == "auto":
            if account_balance < 20:
                return {"allowed": False, "reason": "잔고 부족 (최소 $20)"}

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
            "fixed_lot": self.fixed_lot,
        }
