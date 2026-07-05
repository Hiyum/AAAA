import time
import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any

from core.mt5_connector import MT5Connector
from core.risk_manager import RiskManager
from core.claude_ai import ClaudeAI
from config import Config

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    TradingView(분석) → Claude AI(판단) → MT5(주문) 단일 파이프라인.

    진입:  Pine BUY/SELL 신호 → (옵션) Claude AI 검증 → 신뢰도 기반 lot 산정 → MT5 주문
    청산:  ① Pine 기준선 복귀 신호(exit_long/exit_short) ② MT5 SL/TP ③ 세션 종료 강제 청산
    관리:  매 봉 규칙 기반 SL/TP 실시간 조정 (본전 이동 + ATR 트레일링, LONG/SHORT 대칭)
    """

    def __init__(self, log_callback: Callable = None):
        self.mt5 = MT5Connector()
        self.risk = RiskManager()
        self.ai = ClaudeAI()
        self.log_callback = log_callback or print

        self.active = False
        self.current_symbol = None
        self.trade_history = []
        # 멀티에셋: 종목당 1개씩, 전체 최대 3개 (금/BTC/AUDUSD)
        # 종목당 1개 제한은 process_tradingview의 라우팅이 보장
        # (해당 종목 포지션이 있으면 진입이 아니라 관리로 분기됨)
        self.max_open_positions = 3
        self.latest_tv_data: Dict[str, Any] = {}
        self.session_thread: Optional[threading.Thread] = None

        # SL/TP 실시간 관리 파라미터 (ATR 배수)
        self.be_trigger = 1.0    # 이만큼 유리해지면 손절을 본전으로
        self.trail_start = 1.5   # 이만큼 유리해지면 트레일링 시작
        self.trail_dist = 1.2    # 트레일링 간격
        self.min_adjust = 0.05   # 이보다 작은 변화는 수정 요청 안 함 (브로커 부담 방지)

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        logger.info(message)
        self.log_callback({"time": timestamp, "message": message, "level": level})

    # ── 시작/중지 ───────────────────────────────────────────

    def start(self):
        if self.active:
            return {"success": False, "message": "이미 실행 중"}
        if not self.mt5.connected:
            return {"success": False, "message": "MT5 연결 필요"}

        account = self.mt5.get_account_info()
        self.risk.set_initial_balance(account.get("balance", 0))
        self.active = True

        self.log("AI 자동매매 시작 [TradingView → Claude AI → MT5]", "SUCCESS")
        self.log("→ 분석: TradingView | 판단: Claude AI | 주문·청산: MT5", "INFO")
        self.log("→ 매 봉 SL/TP 자동 관리: 본전 이동 + ATR 트레일링", "INFO")

        if getattr(Config, "ENFORCE_DAY_CLOSE", False):
            self.session_thread = threading.Thread(target=self._session_guard_loop, daemon=True)
            self.session_thread.start()
            self.log(f"→ 당일 청산 활성화: 매일 {Config.DAILY_FLATTEN_HOUR}:00 UTC 전 포지션 청산", "INFO")

        return {"success": True, "message": "AI 자동매매 시작됨"}

    def stop(self):
        self.active = False
        self.log("AI 자동매매 중단", "WARNING")
        return {"success": True, "message": "자동매매 중단됨"}

    # ── TradingView 이벤트 처리 (단일 진입점) ─────────────────

    def process_tradingview(self, payload: dict) -> dict:
        symbol = str(payload.get("symbol", Config.PRIORITY_SYMBOLS[0]))
        price = float(payload.get("price", self.mt5.get_current_price(symbol) or 0))
        if price <= 0:
            return {"message": "가격 정보 없음"}

        self.latest_tv_data[symbol] = {"data": payload, "time": datetime.now().isoformat()}
        self.current_symbol = symbol

        if not self.active or not self.mt5.connected:
            return {"message": "자동매매 비활성화 - 신호 무시"}

        action = str(payload.get("action", "HOLD")).upper()

        # 세션 종료: 전량 청산 (오버나이트 금지)
        if action == "CLOSE_ALL":
            return self._close_all_positions("당일 청산 (세션 종료)")

        positions = [p for p in self.mt5.get_open_positions() if p["symbol"] == symbol]

        if positions:
            # ① Pine 청산 신호 (기준선 복귀) - 백테스트와 동일 규칙
            closed = self._apply_exit_signals(positions, payload)
            if closed:
                return closed
            # ② 규칙 기반 SL/TP 실시간 조정 (AI 미호출)
            return self._manage_stops(positions, payload, price)

        return self._check_entry(symbol, price, payload, action)

    # ── 진입 ────────────────────────────────────────────────

    def _check_entry(self, symbol: str, price: float, payload: dict, action: str) -> dict:
        # HOLD 봉은 즉시 반환 (AI 미호출 → 크레딧 절약)
        if action not in ("BUY", "SELL"):
            return {"message": "신호 없음(HOLD)"}

        if not self._in_trading_session():
            return {"message": "거래 세션 밖 - 신규 진입 보류"}

        if len(self.mt5.get_open_positions()) >= self.max_open_positions:
            return {"message": f"최대 포지션({self.max_open_positions}) 도달 - 진입 보류"}

        # Claude AI 검증 (신뢰도가 lot 크기를 결정)
        if getattr(Config, "AI_CONFIRM_ENTRIES", True):
            self.log(f"[TV 신호] {action} {symbol} @ {price} - Claude AI 검증 중...")
            decision = self.ai.analyze_tradingview(payload)
            self.log(f"[AI 판단] {decision['action']} | 신뢰도 {decision.get('confidence', 0):.0%} | {decision.get('reasoning', '')}")
        else:
            decision = {"action": action, "confidence": 0.7,
                        "reasoning": "Pine 신호 직접 실행 (AI 검증 생략 모드)",
                        "stop_loss": payload.get("sl", 0), "take_profit": payload.get("tp", 0)}
            self.log(f"[TV 신호] {action} {symbol} @ {price} - 직접 실행 (AI 생략)")

        final = str(decision.get("action", "HOLD")).upper()
        confidence = float(decision.get("confidence", 0))
        if final not in ("BUY", "SELL") or confidence < 0.6:
            return {"message": f"AI 진입 보류: {decision.get('reasoning', '')}"}

        balance = self.mt5.get_account_info().get("balance", 10000)
        sl = float(decision.get("stop_loss") or (price * 0.995 if final == "BUY" else price * 1.005))
        tp = float(decision.get("take_profit") or (price * 1.01 if final == "BUY" else price * 0.99))

        # 신뢰도 기반 동적 lot: 확신이 클수록 크게, 단 하드캡 안에서
        lot = self.risk.calculate_lot_size(balance, price, sl, confidence=confidence)
        self.log(f"[Lot 산정] 신뢰도 {confidence:.0%} → {lot} lot (잔고 ${balance:.0f})")

        result = self.mt5.place_order(symbol, final, lot, price, sl, tp, comment="ClaudeAI-TV")
        if result["success"]:
            self.trade_history.append({
                "time": datetime.now().isoformat(), "symbol": symbol, "action": final,
                "lot": lot, "entry_price": price, "sl": result.get("sl"), "tp": result.get("tp"),
                "ticket": result.get("ticket"), "reasoning": decision.get("reasoning", ""),
                "confidence": confidence, "profit": 0, "status": "OPEN", "source": "TradingView",
            })
            self.log(f"[주문 완료] {final} {symbol} {lot}lot @ {price} #티켓{result.get('ticket')}", "SUCCESS")
        else:
            self.log(f"[주문 실패] {result.get('message')}", "ERROR")
        return result

    # ── 청산 신호 처리 ──────────────────────────────────────

    def _apply_exit_signals(self, positions: list, payload: dict) -> Optional[dict]:
        exit_long = str(payload.get("exit_long", "")).lower() in ("true", "1")
        exit_short = str(payload.get("exit_short", "")).lower() in ("true", "1")
        if not (exit_long or exit_short):
            return None

        closed = []
        for pos in positions:
            if (pos["type"] == "BUY" and exit_long) or (pos["type"] == "SELL" and exit_short):
                r = self.mt5.close_position(pos["ticket"])
                if r["success"]:
                    profit = pos.get("profit", 0)
                    sign = "+" if profit >= 0 else ""
                    self.log(f"[기준선 복귀 청산] #{pos['ticket']} {pos['type']} | 손익 {sign}{profit:.2f}$", "SUCCESS")
                    self._record_closed(pos["ticket"], profit)
                    closed.append(pos["ticket"])
        return {"message": f"기준선 복귀 청산: {len(closed)}건", "closed": closed} if closed else None

    # ── SL/TP 실시간 조정 (본전 이동 + ATR 트레일링) ─────────

    def _manage_stops(self, positions: list, payload: dict, price: float) -> dict:
        atr = float(payload.get("atr", 0) or 0)
        if atr <= 0:
            return {"message": "ATR 없음 - SL/TP 관리 건너뜀"}

        mean_tp = float(payload.get("tp", 0) or 0)  # Dynamic TP = 현재 기준선
        adjusted = 0

        for pos in positions:
            entry, sl, tp = pos["open_price"], pos["sl"], pos["tp"]
            new_sl, new_tp = sl, tp

            if pos["type"] == "BUY":
                # 본전 이동: 1×ATR 유리 → 손절을 진입가로 (이익이 손실로 못 변함)
                if price >= entry + atr * self.be_trigger and (sl <= 0 or sl < entry):
                    new_sl = entry
                # 트레일링: 1.5×ATR 이상 유리 → 가격 따라 손절 상향
                if price >= entry + atr * self.trail_start:
                    candidate = price - atr * self.trail_dist
                    new_sl = max(new_sl, candidate)
                # Dynamic TP: 기준선이 이동하면 TP도 갱신
                if mean_tp > 0 and mean_tp > price and abs(mean_tp - tp) > atr * 0.2:
                    new_tp = mean_tp
            else:  # SELL
                if price <= entry - atr * self.be_trigger and (sl <= 0 or sl > entry):
                    new_sl = entry
                if price <= entry - atr * self.trail_start:
                    candidate = price + atr * self.trail_dist
                    new_sl = candidate if new_sl <= 0 else min(new_sl, candidate)
                if mean_tp > 0 and mean_tp < price and abs(mean_tp - tp) > atr * 0.2:
                    new_tp = mean_tp

            sl_changed = abs(new_sl - sl) > atr * self.min_adjust
            tp_changed = abs(new_tp - tp) > atr * self.min_adjust
            if sl_changed or tp_changed:
                r = self.mt5.modify_position(pos["ticket"], new_sl, new_tp)
                if r["success"]:
                    adjusted += 1
                    what = []
                    if sl_changed:
                        tag = "본전" if abs(new_sl - entry) < atr * 0.01 else "트레일"
                        what.append(f"SL {sl:.5f}→{new_sl:.5f}({tag})")
                    if tp_changed:
                        what.append(f"TP {tp:.5f}→{new_tp:.5f}(기준선)")
                    self.log(f"[SL/TP 조정] #{pos['ticket']} {pos['type']} | " + " | ".join(what))

        total = sum(p.get("profit", 0) for p in positions)
        sign = "+" if total >= 0 else ""
        return {"message": f"포지션 {len(positions)}개 관리 (조정 {adjusted}건) | 손익 {sign}{total:.2f}$"}

    # ── 전량 청산 / 세션 ────────────────────────────────────

    def _close_all_positions(self, reason: str = "") -> dict:
        positions = self.mt5.get_open_positions()
        if not positions:
            return {"message": "청산할 포지션 없음"}
        self.log(f"[전체 청산] {reason} - {len(positions)}개 청산 중...", "WARNING")
        closed = 0
        for pos in positions:
            r = self.mt5.close_position(pos["ticket"])
            if r["success"]:
                closed += 1
                self._record_closed(pos["ticket"], pos.get("profit", 0))
        self.log(f"[전체 청산 완료] {closed}/{len(positions)}개", "SUCCESS")
        return {"message": f"{closed}개 포지션 청산", "closed": closed}

    def _in_trading_session(self) -> bool:
        if not getattr(Config, "ENFORCE_DAY_CLOSE", False):
            return True
        hour = datetime.now(timezone.utc).hour
        start, end = Config.TRADE_SESSION_START_HOUR, Config.TRADE_SESSION_END_HOUR
        return (start <= hour < end) if start <= end else (hour >= start or hour < end)

    def _session_guard_loop(self):
        """매일 지정 시각(UTC)에 전 포지션 청산하는 안전장치"""
        last_flatten_date = None
        while self.active:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == Config.DAILY_FLATTEN_HOUR and last_flatten_date != now.date():
                    if self.mt5.connected and self.mt5.get_open_positions():
                        self._close_all_positions(f"당일 청산 시각({Config.DAILY_FLATTEN_HOUR}:00 UTC)")
                    last_flatten_date = now.date()
            except Exception as e:
                logger.error(f"세션 가드 오류: {e}")
            time.sleep(60)

    # ── 기록/상태 ───────────────────────────────────────────

    def _record_closed(self, ticket: int, profit: float):
        for t in self.trade_history:
            if t.get("ticket") == ticket:
                t["status"] = "CLOSED"
                t["profit"] = round(profit, 2)
                break
        self.risk.record_trade_result(profit)

    def get_status(self) -> Dict[str, Any]:
        account = self.mt5.get_account_info() if self.mt5.connected else {}
        positions = self.mt5.get_open_positions()

        session_profit = sum(p.get("profit", 0) for p in positions)
        for t in self.trade_history:
            if t.get("status") == "CLOSED":
                session_profit += t.get("profit", 0)

        return {
            "active": self.active,
            "connected": self.mt5.connected,
            "account": account,
            "current_symbol": self.current_symbol,
            "open_positions": positions,
            "trade_history": self.trade_history[-50:],
            "session_profit": round(session_profit, 2),
            "strategy": "TradingView 분석 + Claude AI 판단 (Scalp MR v2)",
            "risk_settings": self.risk.get_settings(),
        }
