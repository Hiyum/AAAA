import os
import json
import time
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List

from core.mt5_connector import MT5Connector
from core.risk_manager import RiskManager
from core.claude_ai import ClaudeAI
from core.database import TradingDB
from config import Config

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    TradingView(분석) → Claude AI(판단) → MT5(집행) — 자가 발전형 파이프라인 v8.

    역할 분리 (레이턴시 설계):
      · 빠른 길 (파이썬, 5초 틱 루프): 보유 포지션 SL 관리, 급변 감지, 대기주문 TTL
      · 느린 길 (Claude API, 15분봉/이벤트): 진입 판단, 긴급 개입, 거래 리뷰

    집행: 시장가 폐기 → 지정가 래더 (스프레드 역이용).
    보유: 시간 강제청산 폐기 → 시장 구조 + MDD 하드캡이 청산을 결정.
    기록: SQLite (신호가/실체결가/슬리피지/레이턴시/AI 판단/리뷰 영구 보존).
    """

    def __init__(self, log_callback: Callable = None):
        self.mt5 = MT5Connector()
        self.risk = RiskManager()
        self.ai = ClaudeAI()
        self.db = TradingDB()
        self.log_callback = log_callback or print

        self.active = False
        self.current_symbol = None
        self.max_units = getattr(Config, "PYRAMID_MAX_UNITS", 2)
        self.latest_tv_data: Dict[str, Any] = {}
        self.latest_payload: Dict[str, Any] = {}     # 최신 시장 보고 (ATR/스윙 소스)
        self.session_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None

        # 동시성/중복 방지
        self._entry_lock = threading.Lock()
        self._last_entry_key = ""
        self._last_entry_time = 0.0
        self._trades_today = 0
        self._trades_day = None

        # 부분익절 상태 (ticket → True). 재시작 시 DB exit_reason으로 복원
        self._took_partial: Dict[int, bool] = {}
        for t in self.db.open_trades():
            if (t.get("exit_reason") or "") == "partial":
                self._took_partial[t["ticket"]] = True

        # 실시간 감시 상태
        self._tick_window: deque = deque(maxlen=60)   # (ts, mid)
        self._last_intervention = 0.0
        self._vmin_cache: Dict[str, float] = {}

    def _symbol_vmin(self, symbol: str) -> float:
        if symbol not in self._vmin_cache:
            self._vmin_cache[symbol] = self.mt5.get_symbol_specs(symbol)["volume_min"]
        return self._vmin_cache[symbol]

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
        balance = account.get("balance", 0)
        self.risk.set_initial_balance(balance)
        self.db.snapshot_equity(balance, account.get("equity", balance))
        self.active = True

        self.log("AI 자동매매 시작 [TradingView → Claude AI → MT5] v8", "SUCCESS")
        self.log("→ 집행: 지정가 래더 (스프레드 역이용) | 보유: 시장 구조가 결정", "INFO")
        self.log(f"→ MDD 하드캡 {getattr(Config, 'MAX_DRAWDOWN_PCT', 0.25)*100:.0f}% | "
                 f"일일 예산 {getattr(Config, 'MAX_TRADES_PER_DAY', 6)}발 | "
                 f"피라미딩 최대 {self.max_units}단", "INFO")

        self.session_thread = threading.Thread(target=self._session_guard_loop, daemon=True)
        self.session_thread.start()
        self.monitor_thread = threading.Thread(target=self._fast_monitor_loop, daemon=True)
        self.monitor_thread.start()
        return {"success": True, "message": "AI 자동매매 시작됨"}

    def stop(self):
        self.active = False
        self.log("AI 자동매매 중단", "WARNING")
        return {"success": True, "message": "자동매매 중단됨"}

    # ── TradingView 이벤트 처리 (단일 진입점) ─────────────────

    def process_tradingview(self, payload: dict) -> dict:
        t0 = time.time()
        symbol = str(payload.get("symbol", Config.PRIORITY_SYMBOLS[0]))
        price = float(payload.get("price", self.mt5.get_current_price(symbol) or 0))
        if price <= 0:
            return {"message": "가격 정보 없음"}

        self.latest_tv_data[symbol] = {"data": payload, "time": datetime.now().isoformat()}
        self.latest_payload = payload
        self.current_symbol = symbol

        if not self.active or not self.mt5.connected:
            return {"message": "자동매매 비활성화 - 신호 무시"}

        action = str(payload.get("action", "HOLD")).upper()

        if action == "CLOSE_ALL":
            return self._close_all_positions("Pine 전량 청산 신호")

        positions = [p for p in self.mt5.get_open_positions() if p["symbol"] == symbol]

        if positions:
            closed = self._apply_exit_signals(positions, payload)
            if closed:
                return closed
            managed = self._manage_stops(positions, payload, price)
            if action in ("BUY", "SELL"):
                # 반대 방향 신호 → 리버설 검토 (백테스트 실측 수익원)
                opposite = [p for p in positions if p["type"] != action]
                if opposite and getattr(Config, "REVERSAL_ENABLED", True):
                    rev = self._check_reversal(symbol, price, payload, action, opposite, t0)
                    if rev:
                        return rev
                # 같은 방향의 '완전히 새로운' 신호 → 피라미딩 검토
                if getattr(Config, "PYRAMID_ENABLED", False):
                    pyr = self._check_pyramid(symbol, price, payload, action, positions, t0)
                    if pyr:
                        return pyr
            return managed

        return self._check_entry(symbol, price, payload, action, t0)

    # ── 진입 ────────────────────────────────────────────────

    def _entry_gates(self, symbol: str, action: str, payload: dict) -> Optional[str]:
        """공통 진입 게이트. 통과하면 None, 막히면 사유 문자열."""
        if not self._in_trading_session():
            return "거래 세션 밖 - 신규 진입 보류"

        entry_key = f"{symbol}|{action}"
        if entry_key == self._last_entry_key and time.time() - self._last_entry_time < 60:
            return "중복 신호 무시 (60초 내 동일 신호)"

        today = datetime.now(timezone.utc).date()
        if self._trades_day != today:
            self._trades_day = today
            self._trades_today = 0
        max_day = getattr(Config, "MAX_TRADES_PER_DAY", 6)
        if self._trades_today >= max_day:
            return f"일일 거래 예산({max_day}발) 소진 - 내일 재개"

        balance_now = self.mt5.get_account_info().get("balance", 0)
        gate = self.risk.can_trade(balance_now)
        if not gate["allowed"]:
            self.log(f"[거래 차단] {gate['reason']}", "WARNING")
            return f"거래 차단: {gate['reason']}"

        # 스프레드 필터: 임펄스 직후 스프레드 폭발 구간 차단
        atr = float(payload.get("atr", 0) or 0)
        tick = self.mt5.get_tick(symbol)
        if atr > 0 and tick and tick["spread"] > atr * getattr(Config, "MAX_SPREAD_ATR_MULT", 0.15):
            return (f"스프레드 과대 ({tick['spread']:.2f} > ATR×"
                    f"{getattr(Config, 'MAX_SPREAD_ATR_MULT', 0.15)}) - 진입 보류")
        return None

    def _check_entry(self, symbol: str, price: float, payload: dict,
                     action: str, t0: float) -> dict:
        autonomous = getattr(Config, "AI_AUTONOMOUS", False)

        if action not in ("BUY", "SELL") and not autonomous:
            return {"message": "신호 없음(HOLD)"}

        blocked = self._entry_gates(symbol, action, payload)
        if blocked:
            return {"message": blocked}

        # 미체결 래더가 살아있으면 새 진입 판단 보류 (이중 노출 방지)
        if self.db.pending_trades():
            return {"message": "대기 지정가 존재 - 새 진입 보류"}

        # ── Claude AI 판단 ──────────────────────────────────
        payload = dict(payload)
        payload["trades_left_today"] = getattr(Config, "MAX_TRADES_PER_DAY", 6) - self._trades_today
        if autonomous:
            payload["autonomous"] = True
            decision = self.ai.analyze_tradingview(payload)
        elif getattr(Config, "AI_CONFIRM_ENTRIES", True):
            self.log(f"[TV 신호] {action} {symbol} @ {price} - Claude AI 종합 판단 중...")
            decision = self.ai.analyze_tradingview(payload)
        else:
            decision = {"action": action, "confidence": 0.7,
                        "reasoning": "Pine 신호 직접 실행 (AI 검증 생략 모드)",
                        "stop_loss": payload.get("sl", 0), "take_profit": payload.get("tp", 0)}

        final = str(decision.get("action", "HOLD")).upper()
        confidence = float(decision.get("confidence", 0))
        executed = False
        try:
            min_conf = 0.7 if (autonomous and action not in ("BUY", "SELL")) else 0.6
            if final in ("BUY", "SELL"):
                self._log_ai_report(decision, confidence)
            if final not in ("BUY", "SELL") or confidence < min_conf:
                if final in ("BUY", "SELL") or action in ("BUY", "SELL"):
                    # 보류도 반드시 로그에 남긴다 (침묵 금지 - 투명성 원칙)
                    why = decision.get("reasoning", "") or decision.get("market_analysis", "")
                    self.log(f"[AI 진입 보류] AI:{final} 신뢰도 {confidence:.0%} "
                             f"(기준 {min_conf:.0%}) | {why}", "WARNING")
                    return {"message": f"AI 진입 보류: {why}"}
                return {"message": "AI 자율 판단: 관망"}

            result = self._execute_entry(symbol, price, payload, decision, t0, unit=1)
            executed = result.get("success", False)
            return result
        finally:
            # 모든 신호+판단을 shadow 기록 (confidence 보정 연구의 원천 데이터)
            self.db.insert_signal(payload, decision, executed=executed)

    def _log_ai_report(self, d: dict, confidence: float):
        """AI 판단 투명성 보고서 (#11) — 터미널과 대시보드 로그에 전문 출력"""
        self.log(f"[AI 시장분석] {d.get('market_analysis', '-')}")
        ev = d.get("key_evidence") or []
        if ev:
            self.log(f"[AI 근거] {' / '.join(str(e) for e in ev)}")
        rf = d.get("risk_factors") or []
        if rf:
            self.log(f"[AI 위험요소] {' / '.join(str(r) for r in rf)}", "WARNING")
        if d.get("long_scenario"):
            self.log(f"[시나리오 롱] {d['long_scenario']}")
        if d.get("short_scenario"):
            self.log(f"[시나리오 숏] {d['short_scenario']}")
        self.log(f"[AI 최종판단] {d.get('action')} | 신뢰도 {confidence:.0%} | {d.get('reasoning', '')}",
                 "SUCCESS")

    def _execute_entry(self, symbol: str, price: float, payload: dict,
                       decision: dict, t0: float, unit: int = 1,
                       size_ratio: float = 1.0, allow_open: bool = False) -> dict:
        """리스크 검증 → 지정가 래더 배치 (시장가 진입 폐기)"""
        final = str(decision["action"]).upper()
        confidence = float(decision.get("confidence", 0))
        atr = float(payload.get("atr", 0) or 0)
        balance = self.mt5.get_account_info().get("balance", 0)
        specs = self.mt5.get_symbol_specs(symbol)

        sl = float(decision.get("stop_loss") or (price * 0.995 if final == "BUY" else price * 1.005))
        tp = float(decision.get("take_profit") or (price * 1.01 if final == "BUY" else price * 0.99))

        # 총 물량 산정 (confidence 반영)
        raw_lot = self.risk.calculate_lot_size(balance, price, sl, confidence=confidence,
                                               contract_size=specs["contract_size"]) * size_ratio
        step = specs["volume_step"]

        # ── 래더 구성 ───────────────────────────────────────
        offsets = getattr(Config, "LIMIT_LADDER_OFFSETS", [0.3, 0.7])
        weights = getattr(Config, "LIMIT_LADDER_WEIGHTS", [0.6, 0.4])
        zone = decision.get("entry_zone") or {}
        z_from, z_to = float(zone.get("from") or 0), float(zone.get("to") or 0)
        use_ladder = getattr(Config, "LIMIT_LADDER_ENABLED", True) and atr > 0

        rungs: List[Dict[str, float]] = []
        if use_ladder:
            if z_from > 0 and z_to > 0 and z_from != z_to:
                lo, hi = min(z_from, z_to), max(z_from, z_to)
                prices = [lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75]
                if final == "SELL":
                    prices = prices[::-1]   # 가까운 rung부터
            else:
                sign = -1 if final == "BUY" else 1
                prices = [price + sign * atr * o for o in offsets]
            for rp, w in zip(prices, weights):
                lot_r = max(specs["volume_min"], int(raw_lot * w / step) * step)
                lot_r = round(min(lot_r, specs["volume_max"]), 2)
                rungs.append({"price": round(rp, 3), "lot": lot_r})
            # 소액 계좌: 두 rung 모두 최소단위로 강제되면 총 리스크 2배 → rung 1개로 축소
            if len(rungs) == 2 and all(r["lot"] <= specs["volume_min"] for r in rungs):
                rungs = [rungs[0]]
        else:
            lot_m = max(specs["volume_min"], int(raw_lot / step) * step)
            rungs = [{"price": price, "lot": round(min(lot_m, specs["volume_max"]), 2)}]

        # ── 실효 리스크 검증 (#6: 소액 계좌 산술 붕괴 차단) ──
        total_risk = 0.0
        for r in rungs:
            check = self.risk.validate_effective_risk(balance, r["price"], sl, r["lot"],
                                                      specs["contract_size"])
            total_risk += check["risk_usd"]
        over_cap = balance > 0 and total_risk / balance > self.risk.MAX_RISK_HARD_CAP
        if over_cap and getattr(Config, "SMALL_ACCOUNT_FIT_SL", True):
            # SL-핏: lot(0.01 바닥)을 못 줄이니 손절 거리를 예산에 맞춘다.
            # 최대 SL 거리 = 리스크 예산($) ÷ (계약크기 × 최소랏)
            vmin = specs["volume_min"]
            budget = balance * self.risk.MAX_RISK_HARD_CAP
            max_dist = budget / (specs["contract_size"] * vmin)
            min_dist = atr * getattr(Config, "MIN_SL_ATR_MULT", 1.0) if atr > 0 else max_dist + 1
            if max_dist < min_dist:
                msg = (f"진입 보류(소액 SL-핏): 예산상 최대 손절폭 ${max_dist:.2f} < "
                       f"노이즈 하한 {getattr(Config, 'MIN_SL_ATR_MULT', 1.0)}×ATR(${min_dist:.2f}) "
                       f"- 지금 변동성에선 ${balance:.0f} 계좌로 안전한 진입 불가, 조용한 구간 대기")
                self.log(f"[SL-핏 보류] {msg}", "WARNING")
                return {"success": False, "message": msg}
            rung_p = rungs[0]["price"]
            sl = rung_p - max_dist if final == "BUY" else rung_p + max_dist
            # 손익비 유지: TP가 조인 SL 기준 1.5R보다 가깝면 1.5R로 확장
            min_tp = rung_p + max_dist * 1.5 if final == "BUY" else rung_p - max_dist * 1.5
            if (final == "BUY" and tp < min_tp) or (final == "SELL" and tp > min_tp):
                tp = min_tp
            rungs = [{"price": rung_p, "lot": vmin}]
            total_risk = max_dist * specs["contract_size"] * vmin
            self.log(f"[SL-핏 적용] 최소랏 {vmin} 1건 | SL을 예산에 맞춰 ${max_dist:.2f}로 "
                     f"조임 (구조 SL 대신) | 리스크 ${total_risk:.2f} = 잔고의 "
                     f"{total_risk/balance*100:.1f}% (하드캡 준수)", "WARNING")
        elif over_cap and getattr(Config, "ALLOW_MIN_LOT_OVERRIDE", False):
            # 소액 데모 전용: 래더를 최소랏 1건으로 축소해 강행 (위험 알고 켠 것)
            rungs = [{"price": rungs[0]["price"], "lot": specs["volume_min"]}]
            total_risk = abs(rungs[0]["price"] - sl) * specs["contract_size"] * specs["volume_min"]
            self.log(f"[최소랏 집행] 구조 SL 유지, 최소랏 {specs['volume_min']} 1건 | "
                     f"이 거래 리스크 ${total_risk:.2f} = 잔고의 "
                     f"{total_risk/balance*100:.1f}% (리스크 제한 해제 상태)", "WARNING")
        elif over_cap and getattr(Config, "REJECT_IF_MIN_LOT_EXCEEDS_CAP", True):
            msg = (f"진입 거부: 래더 총 리스크 ${total_risk:.2f} = 잔고의 "
                   f"{total_risk/balance*100:.1f}% > 하드캡 "
                   f"{self.risk.MAX_RISK_HARD_CAP*100:.0f}% (최소 주문단위 제약)")
            self.log(f"[리스크 거부] {msg}", "WARNING")
            self.log("→ 해결: ① 잔고 $1,000+ 데모 계좌로 교체(권장) 또는 "
                     "② config.py의 ALLOW_MIN_LOT_OVERRIDE=True (소액 데모 전용)", "INFO")
            return {"success": False, "message": msg}

        # ── 주문 배치 ───────────────────────────────────────
        ttl = getattr(Config, "LIMIT_TTL_MINUTES", 30)
        tick = self.mt5.get_tick(symbol) or {}
        placed = []
        with self._entry_lock:
            open_units = len(self.mt5.get_open_positions())
            # allow_open: 리버설 직후(방금 청산해 브로커 목록 갱신이 지연될 수 있음)
            if unit == 1 and not allow_open and open_units >= 1:
                return {"success": False, "message": "락 재확인: 이미 포지션 존재 - 진입 취소"}
            for r in rungs:
                if use_ladder:
                    res = self.mt5.place_limit_order(symbol, final, r["lot"], r["price"],
                                                     sl, tp, expire_minutes=ttl,
                                                     comment=f"ClaudeAI-L{unit}")
                else:
                    res = self.mt5.place_order(symbol, final, r["lot"], r["price"],
                                               sl, tp, comment=f"ClaudeAI-M{unit}")
                if res.get("success"):
                    placed.append((r, res))
                else:
                    self.log(f"[주문 실패] rung @{r['price']}: {res.get('message')}", "ERROR")

        if not placed:
            return {"success": False, "message": "래더 전체 배치 실패"}

        latency_ms = int((time.time() - t0) * 1000)
        for r, res in placed:
            risk_usd = abs(r["price"] - sl) * specs["contract_size"] * r["lot"]
            self.db.insert_trade({
                "ticket": res.get("ticket"), "symbol": symbol, "direction": final,
                "lot": r["lot"], "unit": unit,
                "entry_kind": "limit" if use_ladder else "market",
                "signal_price": price,
                "fill_price": None if use_ladder else res.get("price"),
                "slippage": None if use_ladder else round(
                    (res.get("price", price) - price) * (1 if final == "BUY" else -1), 3),
                "latency_ms": latency_ms,
                "spread_entry": round(tick.get("spread", 0), 3),
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "sl": res.get("sl") or sl, "tp": res.get("tp") or tp,
                "risk_usd": round(risk_usd, 2),
                "confidence": float(decision.get("confidence", 0)),
                "reasoning": decision.get("reasoning", ""),
                "ai_report": json.dumps(decision, ensure_ascii=False),
                "status": "PENDING" if use_ladder else "OPEN",
            })

        self._trades_today += 1
        self._last_entry_key = f"{symbol}|{final}"
        self._last_entry_time = time.time()

        desc = " + ".join(f"{r['lot']}lot@{r['price']}" for r, _ in placed)
        kind = "지정가 래더" if use_ladder else "시장가"
        self.log(f"[{kind} 배치] {final} {symbol} {desc} | SL {sl} TP {tp} | "
                 f"총 리스크 ${total_risk:.2f} | TTL {ttl}분 | 레이턴시 {latency_ms}ms", "SUCCESS")
        return {"success": True, "message": f"{kind} {len(placed)}건 배치",
                "rungs": [r for r, _ in placed]}

    # ── 리버설: 반대 극단 신호 → 청산 + 역방향 재진입 ────────

    def _check_reversal(self, symbol: str, price: float, payload: dict,
                        action: str, opposite: list, t0: float) -> Optional[dict]:
        """
        보유 중 반대 방향 신호(반대편 임펄스/스윕 극단) 도착 시:
        AI가 고신뢰로 동의하면 기존 포지션을 청산하고 즉시 역방향 래더 진입.
        10.5개월 백테스트에서 이 경로(83건, +$294)가 전체 순익의 원천이었다.
        """
        blocked = self._entry_gates(symbol, action, payload)
        if blocked:
            return None

        payload = dict(payload)
        payload["trades_left_today"] = getattr(Config, "MAX_TRADES_PER_DAY", 6) - self._trades_today
        payload["reversal_request"] = {
            "note": "반대 방향 포지션 보유 중. 이 신호가 명확한 반대 극단(임펄스/스윕)이고 "
                    "구조가 실제로 전환됐다고 판단될 때만 리버설을 승인하라. "
                    "일시적 되돌림이면 HOLD (기존 포지션 유지가 기본값).",
            "open_positions": [{"type": p["type"], "profit": p.get("profit", 0)} for p in opposite],
        }
        decision = self.ai.analyze_tradingview(payload)
        conf = float(decision.get("confidence", 0))
        self.db.insert_signal(payload, decision, executed=False)
        min_conf = getattr(Config, "REVERSAL_MIN_CONFIDENCE", 0.75)
        if str(decision.get("action", "")).upper() != action or conf < min_conf:
            self.log(f"[리버설 보류] AI:{decision.get('action', '?')} 신뢰도 {conf:.0%} "
                     f"(기준 {min_conf:.0%}) - 기존 포지션 유지 | {decision.get('reasoning', '')}")
            return {"message": f"리버설 보류 (기존 포지션 유지): {decision.get('reasoning', '')}"}

        self.log(f"[리버설 승인] {opposite[0]['type']} → {action} | 신뢰도 {conf:.0%}", "WARNING")
        self._log_ai_report(decision, conf)
        for pos in opposite:
            r = self.mt5.close_position(pos["ticket"])
            if r.get("success"):
                self._record_closed(pos["ticket"], r.get("profit", pos.get("profit", 0)),
                                    r.get("price"), "reversal")
                self.log(f"[리버설 청산] #{pos['ticket']} {pos['type']} | "
                         f"손익 {r.get('profit', 0):+.2f}$", "SUCCESS")
            else:
                self.log(f"[리버설 청산 실패] #{pos['ticket']}: {r.get('message')} - 재진입 중단", "ERROR")
                return {"message": "리버설 실패: 기존 포지션 청산 불가"}
        return self._execute_entry(symbol, price, payload, decision, t0, unit=1, allow_open=True)

    # ── 피라미딩 (#9) ───────────────────────────────────────

    def _check_pyramid(self, symbol: str, price: float, payload: dict,
                       action: str, positions: list, t0: float) -> Optional[dict]:
        same_dir = [p for p in positions if p["type"] == action]
        if not same_dir or len(positions) >= self.max_units:
            return None
        if self.db.pending_trades():
            return None

        # 조건 1: 기존 포지션이 최소 1R 유리
        base = same_dir[0]
        rec = self.db.get_trade(base["ticket"]) or {}
        risk_usd = rec.get("risk_usd") or 0
        if risk_usd <= 0 or base.get("profit", 0) < risk_usd * getattr(Config, "PYRAMID_MIN_OPEN_R", 1.0):
            return None

        blocked = self._entry_gates(symbol, action, payload)
        if blocked:
            return None

        # 조건 2: AI가 '완전히 새로운 근거'로 고신뢰 동의할 때만
        payload = dict(payload)
        payload["trades_left_today"] = getattr(Config, "MAX_TRADES_PER_DAY", 6) - self._trades_today
        payload["pyramid_request"] = {
            "note": "이미 같은 방향 포지션 보유 중. 완전히 새로운 구조/오더플로우 근거가 "
                    "있을 때만 증량을 승인하라. 단순히 수익 중이라는 이유는 근거가 아니다.",
            "open_profit_r": round(base.get("profit", 0) / risk_usd, 2),
        }
        decision = self.ai.analyze_tradingview(payload)
        conf = float(decision.get("confidence", 0))
        self.db.insert_signal(payload, decision, executed=False)
        if str(decision.get("action", "")).upper() != action or \
           conf < getattr(Config, "PYRAMID_MIN_CONFIDENCE", 0.8):
            self.log(f"[피라미딩 보류] AI:{decision.get('action', '?')} 신뢰도 {conf:.0%} | "
                     f"{decision.get('reasoning', '조건 미달')}")
            return {"message": f"피라미딩 보류: {decision.get('reasoning', '조건 미달')}"}

        self.log(f"[피라미딩 승인] {action} 증량 | 기존 +{payload['pyramid_request']['open_profit_r']}R | "
                 f"신뢰도 {conf:.0%}", "SUCCESS")
        self._log_ai_report(decision, conf)
        return self._execute_entry(symbol, price, payload, decision, t0,
                                   unit=len(positions) + 1,
                                   size_ratio=getattr(Config, "PYRAMID_SIZE_RATIO", 0.5))

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
                    profit = r.get("profit", pos.get("profit", 0))
                    self.log(f"[구조 청산] #{pos['ticket']} {pos['type']} | 손익 {profit:+.2f}$", "SUCCESS")
                    self._record_closed(pos["ticket"], profit, r.get("price"), "structure")
                    closed.append(pos["ticket"])
        return {"message": f"구조 청산: {len(closed)}건", "closed": closed} if closed else None

    # ── SL/TP 관리: 부분익절(0.75R) + 구조 기반 동적 SL/TP ──

    def _manage_stops(self, positions: list, payload: dict, price: float) -> dict:
        """
        Pine 백테스트와 완전히 동일한 규칙 (#7 정합성 동기화):
          ① +0.75R 도달 → 50% 부분익절 + SL 본전 잠금
          ② 이후 '구조 기반' 트레일 (#8): 스윙 저/고점 ± 0.3×ATR.
             가격 추격이 아니라 시장이 만든 구조 뒤에만 SL을 둔다 → 노이즈 허용
          ③ 동적 TP (#7): 추세 정렬 강화 시 TP 연장, 오더플로우 역전 시 TP 당김
        """
        atr = float(payload.get("atr", 0) or 0)
        if atr <= 0:
            return {"message": "ATR 없음 - SL/TP 관리 건너뜀"}

        swing_hi = float(payload.get("swing_hi", 0) or 0)
        swing_lo = float(payload.get("swing_lo", 0) or 0)
        mtf = payload.get("mtf") or {}
        of = payload.get("of") or {}
        adjusted = 0

        for pos in positions:
            ticket = pos["ticket"]
            entry, sl, tp = pos["open_price"], pos["sl"], pos["tp"]
            rec = self.db.get_trade(ticket) or {}
            orig_sl = rec.get("sl") or sl or 0
            risk_dist = abs(entry - orig_sl) if orig_sl > 0 else atr * 2.5
            took = self._took_partial.get(ticket, False)
            new_sl, new_tp = sl, tp

            # 최소랏(0.01)은 절반 청산이 불가능 → 부분익절 생략, 본전 잠금 + 러너 유지
            vmin = self._symbol_vmin(pos["symbol"])
            can_split = pos["volume"] > vmin * 1.5

            if pos["type"] == "BUY":
                # ① 0.75R 부분익절 + 본전 잠금
                if not took and price >= entry + risk_dist * 0.75:
                    if can_split:
                        r = self.mt5.close_position(ticket, volume=pos["volume"] * 0.5)
                        if r.get("success"):
                            self._took_partial[ticket] = True
                            took = True
                            self.db.close_trade(ticket, r.get("profit", 0), r.get("price"),
                                                "partial", partial=True)
                            self.log(f"[TP1 부분익절] #{ticket} 50% 청산 (+0.75R) + 본전 잠금", "SUCCESS")
                    else:
                        self._took_partial[ticket] = True
                        took = True
                        self.log(f"[본전 잠금] #{ticket} +0.75R 도달 - 최소랏이라 부분익절 생략, "
                                 f"SL 본전 이동 + 러너 유지", "SUCCESS")
                    new_sl = max(new_sl or 0, entry)
                # ② 구조 기반 트레일 (스윙 저점 뒤)
                if took and swing_lo > 0:
                    candidate = swing_lo - atr * 0.3
                    if candidate > (new_sl or 0):
                        new_sl = candidate
                # ③ 동적 TP: 상위 추세+델타 정렬이면 연장, 역전이면 당김
                if tp and tp > 0:
                    aligned = mtf.get("h1") == "up" and of.get("delta_dir") == "buy"
                    reversing = of.get("delta_dir") == "sell" and price > entry
                    if aligned and tp - price < atr * 2:
                        new_tp = tp + atr * 1.5
                    elif reversing and took:
                        new_tp = max(price + atr * 0.5, entry + risk_dist * 0.5)
            else:  # SELL
                if not took and price <= entry - risk_dist * 0.75:
                    if can_split:
                        r = self.mt5.close_position(ticket, volume=pos["volume"] * 0.5)
                        if r.get("success"):
                            self._took_partial[ticket] = True
                            took = True
                            self.db.close_trade(ticket, r.get("profit", 0), r.get("price"),
                                                "partial", partial=True)
                            self.log(f"[TP1 부분익절] #{ticket} 50% 청산 (+0.75R) + 본전 잠금", "SUCCESS")
                    else:
                        self._took_partial[ticket] = True
                        took = True
                        self.log(f"[본전 잠금] #{ticket} +0.75R 도달 - 최소랏이라 부분익절 생략, "
                                 f"SL 본전 이동 + 러너 유지", "SUCCESS")
                    new_sl = min(new_sl, entry) if new_sl and new_sl > 0 else entry
                if took and swing_hi > 0:
                    candidate = swing_hi + atr * 0.3
                    if new_sl is None or new_sl <= 0 or candidate < new_sl:
                        new_sl = candidate
                if tp and tp > 0:
                    aligned = mtf.get("h1") == "down" and of.get("delta_dir") == "sell"
                    reversing = of.get("delta_dir") == "buy" and price < entry
                    if aligned and price - tp < atr * 2:
                        new_tp = tp - atr * 1.5
                    elif reversing and took:
                        new_tp = min(price - atr * 0.5, entry - risk_dist * 0.5)

            sl_changed = new_sl is not None and abs((new_sl or 0) - (sl or 0)) > atr * 0.05
            tp_changed = new_tp is not None and abs((new_tp or 0) - (tp or 0)) > atr * 0.05
            if sl_changed or tp_changed:
                r = self.mt5.modify_position(ticket, new_sl or sl, new_tp or tp)
                if r["success"]:
                    adjusted += 1
                    what = []
                    if sl_changed:
                        tag = "본전" if abs((new_sl or 0) - entry) < atr * 0.01 else "구조트레일"
                        what.append(f"SL {sl:.2f}→{new_sl:.2f}({tag})")
                    if tp_changed:
                        what.append(f"TP {tp:.2f}→{new_tp:.2f}(동적)")
                    self.log(f"[SL/TP 조정] #{ticket} {pos['type']} | " + " | ".join(what))

        total = sum(p.get("profit", 0) for p in positions)
        return {"message": f"포지션 {len(positions)}개 관리 (조정 {adjusted}건) | 손익 {total:+.2f}$"}

    # ── 전량 청산 ───────────────────────────────────────────

    def _close_all_positions(self, reason: str = "") -> dict:
        self._cancel_all_pendings(reason)
        positions = self.mt5.get_open_positions()
        if not positions:
            return {"message": "청산할 포지션 없음"}
        self.log(f"[전체 청산] {reason} - {len(positions)}개 청산 중...", "WARNING")
        closed = 0
        for pos in positions:
            r = self.mt5.close_position(pos["ticket"])
            if r["success"]:
                closed += 1
                self._record_closed(pos["ticket"], r.get("profit", pos.get("profit", 0)),
                                    r.get("price"), reason or "manual")
        self.log(f"[전체 청산 완료] {closed}/{len(positions)}개", "SUCCESS")
        return {"message": f"{closed}개 포지션 청산", "closed": closed}

    def _cancel_all_pendings(self, reason: str = ""):
        for t in self.db.pending_trades():
            r = self.mt5.cancel_order(t["ticket"])
            if r.get("success"):
                self.db.cancel_trade(t["ticket"])
                self.log(f"[대기주문 취소] #{t['ticket']} ({reason})")

    def _in_trading_session(self) -> bool:
        hour = datetime.now(timezone.utc).hour
        start, end = Config.TRADE_SESSION_START_HOUR, Config.TRADE_SESSION_END_HOUR
        return (start <= hour < end) if start <= end else (hour >= start or hour < end)

    # ── 유지관리 루프 (60초): MDD/TTL/체결·청산 정합 ─────────

    def _session_guard_loop(self):
        """webhook과 독립적인 안전장치:
        ① MDD 하드캡: 피크 자산 대비 한도 초과 → 전량 청산 + 자동매매 중단
        ② 대기주문 정합: 체결됨(PENDING→OPEN 승격) / 만료됨(취소) 반영
        ③ 청산 정합: 브로커 SL/TP로 닫힌 포지션을 딜 내역에서 복원 기록
        ④ 일일 리셋 + (옵션) 당일 청산
        """
        last_flatten_date = None
        last_reset_date = datetime.now(timezone.utc).date()
        last_snapshot = 0.0

        while self.active:
            try:
                now = datetime.now(timezone.utc)

                # ① MDD 하드캡 (#10: 시간 청산 대신 계좌 생존 방어선)
                if self.mt5.connected:
                    acct = self.mt5.get_account_info()
                    equity = acct.get("equity", 0)
                    if time.time() - last_snapshot > 600 and equity > 0:
                        self.db.snapshot_equity(acct.get("balance", 0), equity)
                        last_snapshot = time.time()
                    dd = self.risk.check_drawdown(equity, max(self.db.peak_equity(), equity))
                    if dd["breached"]:
                        self.log(f"[MDD 하드캡] 피크 대비 -{dd['dd_pct']*100:.1f}% "
                                 f"(한도 {dd['cap']*100:.0f}%) → 전량 청산 + 자동매매 중단", "ERROR")
                        self._close_all_positions("MDD 하드캡")
                        self.active = False
                        break

                # ② 대기주문 정합 (체결/만료)
                self._reconcile_pendings()

                # ③ 브로커측 청산 복원 (SL/TP로 서버 몰래 닫힌 것)
                self._reconcile_closes()

                # ④ 일일 리셋
                if now.date() != last_reset_date:
                    self.risk.reset_daily()
                    balance = self.mt5.get_account_info().get("balance", 0)
                    if balance > 0:
                        self.risk.set_initial_balance(balance)
                    self.log(f"[일일 리셋] 새 거래일 시작 - 기준잔고 ${balance:.2f}")
                    last_reset_date = now.date()

                # (옵션) 당일 청산 — 기본 비활성 (#10)
                if getattr(Config, "ENFORCE_DAY_CLOSE", False):
                    if now.hour == Config.DAILY_FLATTEN_HOUR and last_flatten_date != now.date():
                        if self.mt5.connected and self.mt5.get_open_positions():
                            self._close_all_positions(f"당일 청산({Config.DAILY_FLATTEN_HOUR}:00 UTC)")
                        last_flatten_date = now.date()
            except Exception as e:
                logger.error(f"유지관리 루프 오류: {e}")
            time.sleep(60)

    def _reconcile_pendings(self):
        pendings = self.db.pending_trades()
        if not pendings or not self.mt5.connected:
            return
        live = {o["ticket"] for o in self.mt5.get_pending_orders()}
        positions = {p["ticket"]: p for p in self.mt5.get_open_positions()}
        ttl_sec = getattr(Config, "LIMIT_TTL_MINUTES", 30) * 60
        for t in pendings:
            ticket = t["ticket"]
            if ticket in live:
                # 아직 대기 중 → TTL 초과 시 취소
                try:
                    placed = datetime.fromisoformat(t["entry_time"])
                    if placed.tzinfo is None:
                        placed = placed.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - placed).total_seconds() > ttl_sec:
                        if self.mt5.cancel_order(ticket).get("success"):
                            self.db.cancel_trade(ticket)
                            self.log(f"[TTL 만료] 대기주문 #{ticket} 취소 (미체결 {ttl_sec//60}분)")
                except Exception:
                    pass
                continue
            # 주문이 대기 목록에서 사라짐: 체결 or 브로커 만료
            # MT5: 지정가 체결 시 포지션 티켓 == 주문 티켓 (netting/hedging 공통 관례)
            if ticket in positions:
                fill = positions[ticket]["open_price"]
                self.db.activate_pending(ticket, ticket, fill)
                slip = (fill - (t.get("signal_price") or fill)) * (1 if t["direction"] == "BUY" else -1)
                self.log(f"[지정가 체결] #{ticket} {t['direction']} {t['lot']}lot @ {fill} "
                         f"| 신호가 대비 {slip:+.2f} (음수=개선)", "SUCCESS")
            else:
                self.db.cancel_trade(ticket)
                self.log(f"[대기주문 소멸] #{ticket} 브로커 만료/취소 처리")

    def _reconcile_closes(self):
        if not self.mt5.connected:
            return
        live = {p["ticket"] for p in self.mt5.get_open_positions()}
        for t in self.db.open_trades():
            if t["ticket"] in live:
                continue
            info = self.mt5.get_closed_position_info(t["ticket"])
            if info:
                self.log(f"[브로커 청산 감지] #{t['ticket']} {info['reason'].upper()} "
                         f"| 손익 {info['profit']:+.2f}$", "SUCCESS" if info["profit"] >= 0 else "WARNING")
                self._record_closed(t["ticket"], info["profit"], info["exit_price"], info["reason"])
            else:
                # 딜 내역 조회 실패 — 다음 사이클 재시도 (시뮬레이션 모드면 그냥 닫음)
                if self.mt5.simulation_mode:
                    self._record_closed(t["ticket"], 0, None, "sim")

    # ── 실시간 감시 루프 (5초): 급변 → AI 개입 (#5) ─────────

    def _fast_monitor_loop(self):
        interval = getattr(Config, "FAST_MONITOR_INTERVAL_SEC", 5)
        while self.active:
            try:
                positions = self.mt5.get_open_positions() if self.mt5.connected else []
                if not positions:
                    self._tick_window.clear()
                    time.sleep(interval * 4)
                    continue
                symbol = positions[0]["symbol"]
                tick = self.mt5.get_tick(symbol)
                if tick:
                    now = time.time()
                    self._tick_window.append((now, tick["mid"]))
                    atr = float(self.latest_payload.get("atr", 0) or 0)
                    if atr > 0:
                        past = [m for ts, m in self._tick_window if now - ts <= 60]
                        if len(past) >= 3:
                            move = abs(tick["mid"] - past[0])
                            if move > atr * getattr(Config, "INTERVENTION_MOVE_ATR", 1.2):
                                self._trigger_intervention(positions, tick, move, atr)
            except Exception as e:
                logger.error(f"실시간 감시 오류: {e}")
            time.sleep(interval)

    def _trigger_intervention(self, positions: list, tick: dict, move: float, atr: float):
        cooldown = getattr(Config, "INTERVENTION_COOLDOWN_SEC", 600)
        if time.time() - self._last_intervention < cooldown:
            return
        self._last_intervention = time.time()
        self.log(f"[급변 감지] 60초 내 {move:.2f} 이동 (ATR×{move/atr:.1f}) → AI 긴급 개입 요청", "WARNING")
        for pos in positions:
            context = {
                "move_60s": round(move, 2), "move_atr_ratio": round(move / atr, 2),
                "current_bid": tick["bid"], "current_ask": tick["ask"],
                "spread": round(tick["spread"], 2),
                "latest_market_report": {k: self.latest_payload.get(k)
                                         for k in ("mtf", "of", "impulse", "body_atr", "rsi")},
            }
            d = self.ai.intervention_check(pos, context)
            decision = str(d.get("decision", "HOLD_POSITION")).upper()
            self.log(f"[AI 개입] #{pos['ticket']} → {decision} | {d.get('reasoning', '')}",
                     "WARNING" if decision != "HOLD_POSITION" else "INFO")
            if decision == "EXIT_NOW":
                r = self.mt5.close_position(pos["ticket"])
                if r.get("success"):
                    self._record_closed(pos["ticket"], r.get("profit", pos.get("profit", 0)),
                                        r.get("price"), "intervention")
            elif decision == "TIGHTEN_SL":
                new_sl = float(d.get("new_sl") or 0)
                if new_sl > 0:
                    self.mt5.modify_position(pos["ticket"], new_sl, pos.get("tp") or 0)

    # ── 기록/청산 후처리: 리뷰 + Equity Guard + 자기개선 ────

    def _record_closed(self, ticket: int, profit: float, exit_price: float = None,
                       exit_reason: str = ""):
        self.db.close_trade(ticket, profit, exit_price, exit_reason)
        self._took_partial.pop(ticket, None)
        self.risk.record_trade_result(profit)
        acct = self.mt5.get_account_info()
        self.db.snapshot_equity(acct.get("balance", 0), acct.get("equity", 0))
        self._equity_guard_check()
        # 셀프 리뷰 + 자기개선은 별도 스레드 (webhook 응답 지연 방지)
        threading.Thread(target=self._post_close_review, args=(ticket,), daemon=True).start()

    def _post_close_review(self, ticket: int):
        try:
            trade = self.db.get_trade(ticket)
            if not trade:
                return
            # ① 거래 리뷰 (#12)
            if getattr(Config, "AI_TRADE_REVIEW", True):
                review = self.ai.review_trade(trade)
                if review:
                    self.db.set_review(ticket, json.dumps(review, ensure_ascii=False))
                    self.log(f"[거래 리뷰 #{ticket}] 등급 {review.get('grade', '?')} | "
                             f"진입: {review.get('entry_eval', '-')} | "
                             f"청산: {review.get('exit_eval', '-')}")
                    if review.get("mistake") and review["mistake"] != "없음":
                        self.log(f"[리뷰-개선점] {review['mistake']}", "WARNING")
                    if review.get("next_time"):
                        self.log(f"[리뷰-다음엔] {review['next_time']}")
            # ② N건마다 자기개선 프롬프트 (#14)
            n = self.db.closed_count()
            every = getattr(Config, "SELF_IMPROVE_EVERY_N_TRADES", 10)
            if every > 0 and n > 0 and n % every == 0:
                self.generate_self_improvement()
        except Exception as e:
            logger.error(f"청산 후처리 오류: {e}")

    def generate_self_improvement(self) -> str:
        """누적 성과 자가 평가 → Claude Code용 개선 프롬프트 파일 생성 (#14)"""
        metrics = self.db.compute_metrics()
        if metrics.get("n", 0) == 0:
            return "청산 거래 없음 - 평가 불가"
        self.log(f"[자기 평가] {metrics['n']}거래 | 승률 {metrics['win_rate']}% | "
                 f"PF {metrics['profit_factor']} | 기대값 ${metrics['expectancy_usd']}/거래 | "
                 f"MDD ${metrics['max_drawdown_usd']} | "
                 f"평균 슬리피지 {metrics.get('avg_slippage', '?')}")
        text = self.ai.generate_improvement_prompt(metrics, self.db.recent_trades(30))
        os.makedirs(getattr(Config, "REPORTS_DIR", "reports"), exist_ok=True)
        path = os.path.join(getattr(Config, "REPORTS_DIR", "reports"),
                            f"improve_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.log(f"[개선 프롬프트 생성] {path} — Claude Code에 붙여넣어 시스템을 진화시키세요", "SUCCESS")
        print("\n" + "=" * 60 + f"\n{text}\n" + "=" * 60 + "\n")
        return text

    def _equity_guard_check(self):
        """엣지 사망 감지: 최근 성적이 무너지면 자동매매를 스스로 중단."""
        closed = self.db.closed_profits(limit=getattr(Config, "EQUITY_GUARD_WINDOW", 20) * 3)
        window = getattr(Config, "EQUITY_GUARD_WINDOW", 20)
        max_cl = getattr(Config, "EQUITY_GUARD_MAX_CONSEC_LOSS", 6)
        min_pf = getattr(Config, "EQUITY_GUARD_MIN_PF", 0.8)
        if not closed:
            return
        consec = 0
        for p in reversed(closed):
            if p < 0:
                consec += 1
            else:
                break
        if consec >= max_cl:
            self.log(f"[Equity Guard] 연속 {consec}회 손실 → 자동매매 자동 중단. "
                     f"국면 확인 후 수동 재시작하세요.", "ERROR")
            self.active = False
            return
        recent = closed[-window:]
        if len(recent) >= window:
            wins = sum(p for p in recent if p > 0)
            losses = -sum(p for p in recent if p < 0)
            pf = wins / losses if losses > 0 else 99.0
            if pf < min_pf:
                self.log(f"[Equity Guard] 최근 {window}거래 PF {pf:.2f} < {min_pf} → 자동 중단. "
                         f"엣지가 약해졌습니다.", "ERROR")
                self.active = False

    # ── 상태 ────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        account = self.mt5.get_account_info() if self.mt5.connected else {}
        positions = self.mt5.get_open_positions()
        metrics = self.db.compute_metrics()
        # 대시보드 호환: DB 컬럼 → 구버전 필드명 매핑
        history = [{
            "time": t.get("entry_time"), "symbol": t.get("symbol"),
            "action": t.get("direction"), "lot": t.get("lot"),
            "entry_price": t.get("fill_price") or t.get("signal_price") or 0,
            "sl": t.get("sl"), "tp": t.get("tp"),
            "profit": t.get("profit") or 0, "status": t.get("status"),
            "confidence": t.get("confidence"), "reasoning": t.get("reasoning"),
        } for t in self.db.recent_trades(50)]
        return {
            "active": self.active,
            "connected": self.mt5.connected,
            "account": account,
            "current_symbol": self.current_symbol,
            "open_positions": positions,
            "pending_orders": self.db.pending_trades(),
            "trade_history": history,
            "metrics": metrics,
            "session_profit": metrics.get("net_profit", 0),
            "strategy": "MTF 구조 + 오더플로우 프록시 + 지정가 래더 (v8)",
            "risk_settings": self.risk.get_settings(),
        }
