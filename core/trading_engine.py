import time
import logging
import importlib
import threading
from datetime import datetime
from typing import Callable, Optional, Dict, Any

from core.mt5_connector import MT5Connector
from core.risk_manager import RiskManager
from core.claude_ai import ClaudeAI
from config import Config

logger = logging.getLogger(__name__)


class TradingEngine:

    def __init__(self, log_callback: Callable[[str], None] = None):
        self.mt5 = MT5Connector()
        self.risk = RiskManager()
        self.ai = ClaudeAI()
        self.log_callback = log_callback or print

        self.active = False
        self.scan_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None
        self.watchdog_thread: Optional[threading.Thread] = None
        self.strategy = None
        self.current_symbol = None
        self.trade_history = []
        self.max_open_positions = 1   # 동시 최대 포지션 수
        self.monitor_interval = 30    # 포지션 주시 간격 (초)

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        logger.info(message)
        self.log_callback({"time": timestamp, "message": message, "level": level})

    def load_strategy(self, symbol: str = None):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "scalping_strategy", "strategies/scalping_strategy.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            strategy_class = getattr(module, "ScalpingStrategy", None)
            if strategy_class is None:
                classes = [c for c in dir(module) if not c.startswith("_") and c != "BaseStrategy"]
                if classes:
                    strategy_class = getattr(module, classes[0])

            sym = symbol or Config.PRIORITY_SYMBOLS[0]
            self.strategy = strategy_class(sym)
            self.log(f"전략 로드 완료: {self.strategy.get_name()} ({sym})")
            return True
        except Exception as e:
            self.log(f"전략 로드 실패: {e}", "ERROR")
            return False

    def start(self):
        if self.active:
            return {"success": False, "message": "이미 실행 중"}
        if not self.mt5.connected:
            return {"success": False, "message": "MT5 연결 필요"}

        account = self.mt5.get_account_info()
        self.risk.set_initial_balance(account.get("balance", 0))
        self.load_strategy()

        self.active = True

        # 진입 탐색 스레드
        self.scan_thread = threading.Thread(target=self._scanning_loop, daemon=True)
        self.scan_thread.start()

        # 포지션 주시 스레드
        self.monitor_thread = threading.Thread(target=self._position_monitor_loop, daemon=True)
        self.monitor_thread.start()

        # 스레드 감시 (watchdog)
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.watchdog_thread.start()

        self.log("AI 자동매매 시작 (진입 탐색 + 포지션 주시 동시 실행)", "SUCCESS")
        return {"success": True, "message": "AI 자동매매 시작됨"}

    def stop(self):
        self.active = False
        self.log("AI 자동매매 중단", "WARNING")
        return {"success": True, "message": "자동매매 중단됨"}

    # ── 포지션 주시 루프 (핵심 추가 기능) ──────────────────────

    def _position_monitor_loop(self):
        """
        열린 포지션을 주기적으로 Claude AI가 주시.
        포지션이 여러 개여도 AI 호출은 한 번에 하나씩.
        """
        while self.active:
            try:
                positions = self.mt5.get_open_positions()
                if not positions:
                    time.sleep(self.monitor_interval)
                    continue

                self.log(f"[포지션 주시] 열린 포지션 {len(positions)}개 점검 중...")

                for pos in positions:
                    if not self.active:
                        break

                    symbol = pos["symbol"]
                    ticket = pos["ticket"]
                    action = pos["type"]
                    entry = pos["open_price"]
                    current = pos["current_price"]
                    profit = pos["profit"]
                    sl = pos["sl"]
                    tp = pos["tp"]

                    profit_sign = "+" if profit >= 0 else ""
                    self.log(f"[포지션 주시] #{ticket} {symbol} {action} | 진입:{entry} → 현재:{current} | 손익:{profit_sign}{profit:.2f}$")

                    df = self.mt5.get_ohlcv(symbol, "M5", 100)
                    if df is None:
                        continue

                    decision = self.ai.manage_position(
                        symbol=symbol,
                        action=action,
                        entry_price=entry,
                        current_price=current,
                        sl=sl,
                        tp=tp,
                        profit=profit,
                        df=df,
                    )

                    self.log(f"[AI 포지션 판단] {decision['action']} | {decision['reason']}")

                    if decision["action"] == "CLOSE":
                        self.log(f"[AI 청산 결정] #{ticket} 포지션 청산 중...", "WARNING")
                        result = self.mt5.close_position(ticket)
                        if result["success"]:
                            self.log(f"[청산 완료] #{ticket} | 최종 손익: {profit_sign}{profit:.2f}$", "SUCCESS")
                            self._record_closed(ticket, profit)
                        else:
                            self.log(f"[청산 실패] {result.get('message')}", "ERROR")

                    elif decision["action"] == "MOVE_SL" and decision.get("new_sl"):
                        new_sl = decision["new_sl"]
                        self.log(f"[손절 이동] #{ticket} SL: {sl} → {new_sl}")
                        self.mt5.modify_position(ticket, new_sl, tp)

                time.sleep(self.monitor_interval)

            except Exception as e:
                self.log(f"[포지션 주시 오류] {e}", "ERROR")
                time.sleep(self.monitor_interval)

    def _watchdog_loop(self):
        """스캔/모니터 스레드가 죽으면 자동 재시작"""
        time.sleep(60)  # 시작 후 1분은 대기
        while self.active:
            try:
                if not self.scan_thread.is_alive():
                    self.log("[Watchdog] 스캔 스레드 중단 감지 - 재시작 중...", "WARNING")
                    self.scan_thread = threading.Thread(target=self._scanning_loop, daemon=True)
                    self.scan_thread.start()

                if not self.monitor_thread.is_alive():
                    self.log("[Watchdog] 모니터 스레드 중단 감지 - 재시작 중...", "WARNING")
                    self.monitor_thread = threading.Thread(target=self._position_monitor_loop, daemon=True)
                    self.monitor_thread.start()
            except Exception as e:
                logger.error(f"Watchdog 오류: {e}")
            time.sleep(30)

    def _record_closed(self, ticket: int, profit: float):
        for t in self.trade_history:
            if t.get("ticket") == ticket:
                t["status"] = "CLOSED"
                t["profit"] = round(profit, 2)
                break

    # ── 진입 탐색 루프 ──────────────────────────────────────────

    def _scanning_loop(self):
        scan_count = 0
        while self.active:
            try:
                scan_count += 1
                self.log(f"시장 스캔 #{scan_count} - 종목 분석 중...")
                account = self.mt5.get_account_info()
                balance = account.get("balance", 0)

                can_trade = self.risk.can_trade(balance)
                if not can_trade["allowed"]:
                    self.log(f"거래 중단: {can_trade['reason']}", "WARNING")
                    time.sleep(60)
                    continue

                # 최대 포지션 수 초과 시 신규 진입 차단
                open_positions = self.mt5.get_open_positions()
                if len(open_positions) >= self.max_open_positions:
                    self.log(f"포지션 {len(open_positions)}개 진행 중 (최대 {self.max_open_positions}개) - 신규 진입 대기")
                    time.sleep(30)
                    continue

                market_data = {}
                for sym in Config.PRIORITY_SYMBOLS:
                    self.log(f"{sym} 분석 중...")
                    price = self.mt5.get_current_price(sym)
                    if price is None:
                        self.log(f"{sym} 가격 조회 실패 - 브로커에서 지원하지 않는 종목일 수 있음", "WARNING")
                        continue
                    df = self.mt5.get_ohlcv(sym, "M5", 100)
                    if df is None:
                        continue
                    atr = df['close'].diff().abs().rolling(14).mean().iloc[-1]
                    change_pct = (df['close'].iloc[-1] - df['close'].iloc[-24]) / df['close'].iloc[-24] * 100
                    market_data[sym] = {
                        "current_price": price,
                        "atr": round(atr, 5),
                        "change_pct": round(change_pct, 2),
                        "volume": int(df['volume'].iloc[-1]),
                    }

                if not market_data:
                    self.log("시장 데이터 조회 실패 - 재시도 중...", "WARNING")
                    time.sleep(30)
                    continue

                self.log("최적 종목 선정 중... (Claude AI 분석)")
                best_symbol = self.ai.select_best_symbol(list(market_data.keys()), market_data)
                self.current_symbol = best_symbol
                self.log(f"선택된 종목: {best_symbol}")

                df = self.mt5.get_ohlcv(best_symbol, "M5", 200)
                price = self.mt5.get_current_price(best_symbol)

                if df is None or price is None:
                    time.sleep(10)
                    continue

                self.log(f"{best_symbol} 진입 조건 확인 중...")
                if self.strategy and self.strategy.symbol != best_symbol:
                    self.load_strategy(best_symbol)

                if not self.strategy:
                    time.sleep(10)
                    continue

                signal = self.strategy.analyze(df, price, balance)
                self.log(f"전략 신호: {signal.action} | {signal.reason}")

                if signal.action in ("BUY", "SELL") and signal.confidence >= 0.55:
                    self.log(f"Claude AI 최종 판단 요청 중... ({signal.action} 신호 검증)")
                    ai_decision = self.ai.analyze_market(
                        best_symbol,
                        market_data[best_symbol],
                        {
                            "action": signal.action,
                            "confidence": signal.confidence,
                            "reason": signal.reason,
                            "entry_price": signal.entry_price,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                        }
                    )

                    self.log(f"AI 판단: {ai_decision['action']} | 신뢰도: {ai_decision['confidence']:.0%} | {ai_decision['reasoning']}")

                    if ai_decision["action"] in ("BUY", "SELL") and ai_decision["confidence"] >= 0.6:
                        lot = self.risk.calculate_lot_size(balance, signal.entry_price, signal.stop_loss)
                        self.log(f"주문 실행: {ai_decision['action']} {best_symbol} {lot}lot @ {price}")

                        result = self.mt5.place_order(
                            best_symbol,
                            ai_decision["action"],
                            lot,
                            price,
                            ai_decision.get("stop_loss", signal.stop_loss),
                            ai_decision.get("take_profit", signal.take_profit),
                            comment="ClaudeAI"
                        )

                        if result["success"]:
                            trade_record = {
                                "time": datetime.now().isoformat(),
                                "symbol": best_symbol,
                                "action": ai_decision["action"],
                                "lot": lot,
                                "entry_price": price,
                                "sl": result.get("sl"),
                                "tp": result.get("tp"),
                                "ticket": result.get("ticket"),
                                "reasoning": ai_decision["reasoning"],
                                "profit": 0,
                                "status": "OPEN",
                            }
                            self.trade_history.append(trade_record)
                            self.log(f"주문 완료 #티켓{result.get('ticket')} | 손절: {result.get('sl')} | 목표: {result.get('tp')}", "SUCCESS")
                        else:
                            self.log(f"주문 실패: {result.get('message', '알 수 없는 오류')}", "ERROR")
                    else:
                        self.log(f"AI가 거래 보류 결정: {ai_decision.get('reasoning', '')}")
                else:
                    self.log(f"신호 없음 또는 신뢰도 낮음 ({signal.confidence:.0%}) - 다음 스캔 대기 중...")

                self.log("다음 스캔까지 대기 중... (30초)")
                for _ in range(30):
                    if not self.active:
                        break
                    time.sleep(1)

            except Exception as e:
                try:
                    self.log(f"엔진 오류: {e}", "ERROR")
                except Exception:
                    logger.error(f"엔진 오류 (로그 실패): {e}")
                time.sleep(15)

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
            "strategy": self.strategy.get_name() if self.strategy else "없음",
            "risk_settings": self.risk.get_settings(),
        }

    def reload_strategy(self):
        symbol = self.current_symbol or Config.PRIORITY_SYMBOLS[0]
        return self.load_strategy(symbol)
