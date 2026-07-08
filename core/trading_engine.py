import os
import json
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
        # 포트폴리오: 종목당 1개(라우팅 보장), 전체 최대 2 (GOLD#+AUDUSD)
        self.max_open_positions = 2
        self.latest_tv_data: Dict[str, Any] = {}
        self.session_thread: Optional[threading.Thread] = None

        # 동시성/중복 방지
        self._entry_lock = threading.Lock()      # 알람 동시 도착 시 이중 주문 방지
        self._last_entry_key = ""                # 중복 알람 무시용
        self._last_entry_time = 0.0
        self._trades_today = 0                   # 일일 거래 예산 카운터
        self._trades_day = None

        self._load_history()

        # SL/TP 실시간 관리 파라미터 (ATR 배수) - Trend Rider: 넓게 태운다
        self.be_trigger = 1.0    # 이만큼 유리해지면 손절을 본전으로
        self.trail_start = 1.5   # 이만큼 유리해지면 트레일링 시작
        self.trail_dist = 2.0    # 트레일링 간격 (넓게 = 추세 오래 탐)
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

        # 유지관리 루프는 항상 가동 (당일청산 + 일일리셋 + 시간손절 안전망)
        self.session_thread = threading.Thread(target=self._session_guard_loop, daemon=True)
        self.session_thread.start()
        if getattr(Config, "ENFORCE_DAY_CLOSE", False):
            self.log(f"→ 당일 청산 활성화: 매일 {Config.DAILY_FLATTEN_HOUR}:00 UTC 전 포지션 청산", "INFO")
        self.log(f"→ 안전망: 포지션 {getattr(Config, 'MAX_POSITION_MINUTES', 150)}분 초과 시 서버가 직접 청산", "INFO")

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
        autonomous = getattr(Config, "AI_AUTONOMOUS", False)

        # 일반 모드: HOLD 봉은 즉시 반환 (AI 미호출 → 크레딧 절약)
        # 자율 모드: HOLD 봉도 AI에게 넘겨 스스로 진입 판단 (절대 권한)
        if action not in ("BUY", "SELL") and not autonomous:
            return {"message": "신호 없음(HOLD)"}

        if not self._in_trading_session():
            return {"message": "거래 세션 밖 - 신규 진입 보류"}

        # 중복 알람 방지: 같은 종목+방향 신호가 60초 내 재도착하면 무시
        # (TradingView 재전송, ngrok 재연결 시 이중 주문 사고 차단)
        entry_key = f"{symbol}|{action}"
        if entry_key == self._last_entry_key and time.time() - self._last_entry_time < 60:
            return {"message": "중복 신호 무시 (60초 내 동일 신호)"}

        if len(self.mt5.get_open_positions()) >= self.max_open_positions:
            return {"message": f"최대 포지션({self.max_open_positions}) 도달 - 진입 보류"}

        # 일일 거래 예산 (하루 4~5발 - 프로는 기회를 고른다)
        today = datetime.now(timezone.utc).date()
        if self._trades_day != today:
            self._trades_day = today
            self._trades_today = 0
        max_day = getattr(Config, "MAX_TRADES_PER_DAY", 5)
        if self._trades_today >= max_day:
            return {"message": f"일일 거래 예산({max_day}발) 소진 - 내일 재개"}

        # 일일 손실 한도 확인 (한도 초과 시 그날 거래 중단)
        balance_now = self.mt5.get_account_info().get("balance", 0)
        gate = self.risk.can_trade(balance_now)
        if not gate["allowed"]:
            self.log(f"[거래 차단] {gate['reason']}", "WARNING")
            return {"message": f"거래 차단: {gate['reason']}"}

        # Claude AI 검증/자율 판단 (신뢰도가 lot 크기를 결정)
        payload = dict(payload)
        payload["trades_left_today"] = max_day - self._trades_today
        if autonomous:
            payload["autonomous"] = True
            if action in ("BUY", "SELL"):
                self.log(f"[TV 신호] {action} {symbol} @ {price} - AI 자율 판단 중...")
            decision = self.ai.analyze_tradingview(payload)
            if str(decision.get("action", "HOLD")).upper() in ("BUY", "SELL"):
                self.log(f"[AI 자율판단] {decision['action']} | 신뢰도 {decision.get('confidence', 0):.0%} | {decision.get('reasoning', '')}")
        elif getattr(Config, "AI_CONFIRM_ENTRIES", True):
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
        # 자율 진입(Pine 신호 없이 AI 단독)은 더 엄격한 신뢰도 요구
        min_conf = 0.7 if (autonomous and action not in ("BUY", "SELL")) else 0.6
        if final not in ("BUY", "SELL") or confidence < min_conf:
            if final in ("BUY", "SELL") or action in ("BUY", "SELL"):
                return {"message": f"AI 진입 보류: {decision.get('reasoning', '')}"}
            return {"message": "AI 자율 판단: 관망"}

        balance = self.mt5.get_account_info().get("balance", 10000)
        sl = float(decision.get("stop_loss") or (price * 0.995 if final == "BUY" else price * 1.005))
        tp = float(decision.get("take_profit") or (price * 1.01 if final == "BUY" else price * 0.99))

        # 신뢰도 기반 동적 lot: 확신이 클수록 크게, 단 하드캡 안에서
        # 종목 계약크기 반영 (외환 100,000 / 금 100) + 브로커 최소/스텝 단위로 정규화
        specs = self.mt5.get_symbol_specs(symbol)
        raw_lot = self.risk.calculate_lot_size(balance, price, sl, confidence=confidence,
                                               contract_size=specs["contract_size"])
        step = specs["volume_step"]
        lot = max(specs["volume_min"], int(raw_lot / step) * step)
        lot = round(min(lot, specs["volume_max"]), 2)
        risk_now = abs(price - sl) * specs["contract_size"] * lot
        self.log(f"[Lot 산정] 신뢰도 {confidence:.0%} → {lot} lot | 이 거래 최대 리스크 ${risk_now:.2f} (잔고 ${balance:.0f})")

        # 락으로 이중 주문 방지 (AI 응답 대기 중 다른 알람이 도착하는 경우)
        with self._entry_lock:
            if len(self.mt5.get_open_positions()) >= self.max_open_positions:
                return {"message": "락 재확인: 이미 포지션 존재 - 진입 취소"}
            result = self.mt5.place_order(symbol, final, lot, price, sl, tp, comment="ClaudeAI-TV")

        if result["success"]:
            self._trades_today += 1
            self._last_entry_key = f"{symbol}|{final}"
            self._last_entry_time = time.time()
            self.trade_history.append({
                "time": datetime.now().isoformat(), "symbol": symbol, "action": final,
                "lot": lot, "entry_price": price, "sl": result.get("sl"), "tp": result.get("tp"),
                "ticket": result.get("ticket"), "reasoning": decision.get("reasoning", ""),
                "confidence": confidence, "profit": 0, "status": "OPEN", "source": "TradingView",
            })
            self._save_history()
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
        """60초마다 도는 유지관리 루프 (webhook과 독립적인 안전장치):
        ① 당일 청산 시각에 전 포지션 청산
        ② UTC 날짜 변경 시 일일 손실 카운터 리셋 (그날 기준잔고 갱신)
        ③ 포지션 시간 손절: TradingView 신호가 끊겨도(ngrok 다운 등)
           일정 시간 초과 포지션은 서버가 직접 청산
        """
        last_flatten_date = None
        last_reset_date = datetime.now(timezone.utc).date()
        max_hold_sec = getattr(Config, "MAX_POSITION_MINUTES", 150) * 60

        while self.active:
            try:
                now = datetime.now(timezone.utc)

                # ① 당일 청산
                if getattr(Config, "ENFORCE_DAY_CLOSE", False):
                    if now.hour == Config.DAILY_FLATTEN_HOUR and last_flatten_date != now.date():
                        if self.mt5.connected and self.mt5.get_open_positions():
                            self._close_all_positions(f"당일 청산 시각({Config.DAILY_FLATTEN_HOUR}:00 UTC)")
                        last_flatten_date = now.date()

                # ② 일일 리스크 리셋 (새 날 = 새 기준잔고)
                if now.date() != last_reset_date:
                    self.risk.reset_daily()
                    balance = self.mt5.get_account_info().get("balance", 0)
                    if balance > 0:
                        self.risk.set_initial_balance(balance)
                    self.log(f"[일일 리셋] 새 거래일 시작 - 기준잔고 ${balance:.2f}")
                    last_reset_date = now.date()

                # ③ 포지션 시간 손절 (webhook 독립 안전망)
                #    단, '손실 중'인 포지션만. 승자는 며칠이고 태운다 (Trend Rider 규칙)
                if self.mt5.connected:
                    for pos in self.mt5.get_open_positions():
                        if pos.get("profit", 0) >= 0:
                            continue   # 승자/본전은 건드리지 않음
                        opened = self._find_entry_time(pos["ticket"])
                        if opened and (now - opened).total_seconds() > max_hold_sec:
                            self.log(f"[시간 손절] #{pos['ticket']} 손실 상태로 {max_hold_sec//60}분 초과 - 서버 강제 청산", "WARNING")
                            r = self.mt5.close_position(pos["ticket"])
                            if r["success"]:
                                self._record_closed(pos["ticket"], pos.get("profit", 0))
            except Exception as e:
                logger.error(f"유지관리 루프 오류: {e}")
            time.sleep(60)

    def _find_entry_time(self, ticket: int) -> Optional[datetime]:
        for t in self.trade_history:
            if t.get("ticket") == ticket and t.get("status") == "OPEN":
                try:
                    dt = datetime.fromisoformat(t["time"])
                    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                except Exception:
                    return None
        return None

    # ── 기록/상태 ───────────────────────────────────────────

    def _load_history(self):
        """앱 재시작 후에도 거래 내역 유지"""
        try:
            if os.path.exists(Config.LOG_FILE):
                with open(Config.LOG_FILE, "r", encoding="utf-8") as f:
                    self.trade_history = json.load(f)
                logger.info(f"거래 내역 {len(self.trade_history)}건 로드됨")
        except Exception as e:
            logger.warning(f"거래 내역 로드 실패: {e}")
            self.trade_history = []

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
            with open(Config.LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.trade_history[-500:], f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"거래 내역 저장 실패: {e}")

    def _record_closed(self, ticket: int, profit: float):
        for t in self.trade_history:
            if t.get("ticket") == ticket:
                t["status"] = "CLOSED"
                t["profit"] = round(profit, 2)
                break
        self.risk.record_trade_result(profit)
        self._save_history()
        self._equity_guard_check()

    def _equity_guard_check(self):
        """엣지 사망 감지: 최근 성적이 무너지면 자동매매를 스스로 중단.
        국면 의존 전략을 안전하게 운용하는 핵심 장치 -
        '전략이 언제 작동을 멈췄는가'를 사람보다 먼저 알아챈다."""
        closed = [t["profit"] for t in self.trade_history if t.get("status") == "CLOSED"]
        window = getattr(Config, "EQUITY_GUARD_WINDOW", 20)
        max_cl = getattr(Config, "EQUITY_GUARD_MAX_CONSEC_LOSS", 6)
        min_pf = getattr(Config, "EQUITY_GUARD_MIN_PF", 0.8)
        if not closed:
            return

        # ① 연속 손실 검사
        consec = 0
        for p in reversed(closed):
            if p < 0:
                consec += 1
            else:
                break
        if consec >= max_cl:
            self.log(f"[Equity Guard] 연속 {consec}회 손실 감지 → 자동매매 자동 중단. "
                     f"국면이 바뀌었을 수 있습니다. 상황 확인 후 수동으로 재시작하세요.", "ERROR")
            self.active = False
            return

        # ② 롤링 PF 검사
        recent = closed[-window:]
        if len(recent) >= window:
            wins = sum(p for p in recent if p > 0)
            losses = -sum(p for p in recent if p < 0)
            pf = wins / losses if losses > 0 else 99.0
            if pf < min_pf:
                self.log(f"[Equity Guard] 최근 {window}거래 PF {pf:.2f} < {min_pf} → 자동매매 자동 중단. "
                         f"엣지가 약해졌습니다. 상황 확인 후 수동으로 재시작하세요.", "ERROR")
                self.active = False

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
