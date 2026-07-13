import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from config import Config

logger = logging.getLogger(__name__)


class TradingDB:
    """
    SQLite 영구 저장소 — trades.json(500건 상한, 유실) 대체.

    테이블:
      trades   : 거래 원장. 신호가/실체결가/슬리피지/레이턴시/AI 판단/리뷰까지 전 생애 기록
      signals  : 수신한 모든 TradingView payload + AI 판단 (shadow 데이터 — confidence 보정 연구용)
      equity   : 자산 곡선 스냅샷 (피크 추적 → MDD 하드캡의 데이터 소스)

    스레드 안전: webhook 스레드 + 가드 루프 + 리뷰 스레드가 동시 접근하므로
    단일 커넥션 + 락 (check_same_thread=False).
    """

    def __init__(self, db_file: str = None):
        self.db_file = db_file or Config.DB_FILE
        os.makedirs(os.path.dirname(self.db_file) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate_from_json()

    # ── 스키마 ──────────────────────────────────────────────

    def _create_tables(self):
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket        INTEGER,              -- MT5 포지션 티켓
                symbol        TEXT NOT NULL,
                direction     TEXT NOT NULL,        -- BUY | SELL
                lot           REAL NOT NULL,
                unit          INTEGER DEFAULT 1,    -- 피라미딩 단위 (1=기본, 2=증량)
                entry_kind    TEXT DEFAULT 'market',-- market | limit
                signal_price  REAL,                 -- 신호 시점 가격
                fill_price    REAL,                 -- 실체결가 (슬리피지 계산의 핵심)
                slippage      REAL,                 -- fill - signal (방향 부호 반영)
                latency_ms    INTEGER,              -- 신호 수신 → 체결 왕복
                spread_entry  REAL,                 -- 진입 시점 실측 스프레드
                entry_time    TEXT NOT NULL,
                sl            REAL,
                tp            REAL,
                risk_usd      REAL,                 -- 진입 시점 계산된 최대 리스크($)
                confidence    REAL,
                reasoning     TEXT,
                ai_report     TEXT,                 -- AI 투명성 보고서 전문 (JSON)
                status        TEXT DEFAULT 'OPEN',  -- OPEN | CLOSED | PENDING | CANCELLED
                exit_time     TEXT,
                exit_price    REAL,
                exit_reason   TEXT,                 -- sl/tp/partial/structure/intervention/manual/mdd
                profit        REAL DEFAULT 0,
                r_multiple    REAL,                 -- 실현 손익 / 초기 리스크
                review        TEXT                  -- 거래 종료 후 AI 셀프 리뷰 (JSON)
            );
            CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

            CREATE TABLE IF NOT EXISTS signals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                time          TEXT NOT NULL,
                symbol        TEXT,
                pine_action   TEXT,
                payload       TEXT,                 -- 수신 payload 전문 (JSON)
                ai_action     TEXT,
                ai_confidence REAL,
                ai_reasoning  TEXT,
                executed      INTEGER DEFAULT 0     -- 실제 주문으로 이어졌는가
            );

            CREATE TABLE IF NOT EXISTS equity (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                time    TEXT NOT NULL,
                balance REAL,
                equity  REAL
            );
            """)
            self._conn.commit()

    def _migrate_from_json(self):
        """구버전 trades.json → DB 1회 이관 (trades 테이블이 비어있을 때만)"""
        try:
            n = self._conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            if n > 0 or not os.path.exists(Config.LOG_FILE):
                return
            with open(Config.LOG_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            with self._lock:
                for t in old:
                    self._conn.execute(
                        """INSERT INTO trades (ticket, symbol, direction, lot, signal_price,
                           entry_time, sl, tp, confidence, reasoning, status, profit)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (t.get("ticket"), t.get("symbol", "?"), t.get("action", "?"),
                         t.get("lot", 0), t.get("entry_price"), t.get("time", ""),
                         t.get("sl"), t.get("tp"), t.get("confidence"),
                         t.get("reasoning", ""), t.get("status", "CLOSED"),
                         t.get("profit", 0)))
                self._conn.commit()
            logger.info(f"trades.json → DB 마이그레이션 완료 ({len(old)}건)")
        except Exception as e:
            logger.warning(f"trades.json 마이그레이션 실패(무시): {e}")

    # ── 거래 기록 ───────────────────────────────────────────

    def insert_trade(self, rec: Dict[str, Any]) -> int:
        cols = ("ticket", "symbol", "direction", "lot", "unit", "entry_kind",
                "signal_price", "fill_price", "slippage", "latency_ms", "spread_entry",
                "entry_time", "sl", "tp", "risk_usd", "confidence", "reasoning",
                "ai_report", "status")
        vals = [rec.get(c) for c in cols]
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            self._conn.commit()
            return cur.lastrowid

    def close_trade(self, ticket: int, profit: float, exit_price: float = None,
                    exit_reason: str = "", partial: bool = False):
        """청산 기록. partial=True면 부분익절 — 상태는 OPEN 유지, profit 누적."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, risk_usd, profit FROM trades "
                "WHERE ticket=? AND status IN ('OPEN','PENDING') ORDER BY id DESC LIMIT 1",
                (ticket,)).fetchone()
            if row is None:
                return
            acc_profit = (row["profit"] or 0) + profit
            r_mult = None
            if row["risk_usd"] and row["risk_usd"] > 0:
                r_mult = round(acc_profit / row["risk_usd"], 2)
            if partial:
                self._conn.execute(
                    "UPDATE trades SET profit=?, exit_reason=? WHERE id=?",
                    (round(acc_profit, 2), exit_reason, row["id"]))
            else:
                self._conn.execute(
                    "UPDATE trades SET status='CLOSED', exit_time=?, exit_price=?, "
                    "exit_reason=?, profit=?, r_multiple=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), exit_price, exit_reason,
                     round(acc_profit, 2), r_mult, row["id"]))
            self._conn.commit()

    def cancel_trade(self, ticket: int):
        with self._lock:
            self._conn.execute(
                "UPDATE trades SET status='CANCELLED' WHERE ticket=? AND status='PENDING'",
                (ticket,))
            self._conn.commit()

    def activate_pending(self, order_ticket: int, position_ticket: int, fill_price: float):
        """대기 지정가가 체결됨: PENDING → OPEN, 포지션 티켓/체결가 갱신"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, signal_price, direction FROM trades WHERE ticket=? AND status='PENDING'",
                (order_ticket,)).fetchone()
            if row is None:
                return
            slip = None
            if row["signal_price"]:
                slip = round((fill_price - row["signal_price"]) *
                             (1 if row["direction"] == "BUY" else -1), 3)
            self._conn.execute(
                "UPDATE trades SET status='OPEN', ticket=?, fill_price=?, slippage=? WHERE id=?",
                (position_ticket, fill_price, slip, row["id"]))
            self._conn.commit()

    def set_review(self, ticket: int, review_json: str):
        with self._lock:
            self._conn.execute(
                "UPDATE trades SET review=? WHERE ticket=? AND status='CLOSED'",
                (review_json, ticket))
            self._conn.commit()

    def get_trade(self, ticket: int) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT * FROM trades WHERE ticket=? ORDER BY id DESC LIMIT 1", (ticket,)).fetchone()
        return dict(row) if row else None

    def open_trades(self) -> List[Dict]:
        rows = self._conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        return [dict(r) for r in rows]

    def pending_trades(self) -> List[Dict]:
        rows = self._conn.execute("SELECT * FROM trades WHERE status='PENDING'").fetchall()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 50) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def closed_profits(self, limit: int = 200) -> List[float]:
        rows = self._conn.execute(
            "SELECT profit FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [r["profit"] or 0 for r in rows][::-1]   # 시간 순

    def closed_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='CLOSED'").fetchone()[0]

    # ── 신호 기록 (shadow 데이터) ───────────────────────────

    def insert_signal(self, payload: dict, ai_decision: dict = None, executed: bool = False):
        d = ai_decision or {}
        with self._lock:
            self._conn.execute(
                "INSERT INTO signals (time, symbol, pine_action, payload, ai_action, "
                "ai_confidence, ai_reasoning, executed) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(),
                 str(payload.get("symbol", "?")), str(payload.get("action", "?")),
                 json.dumps(payload, ensure_ascii=False),
                 d.get("action"), d.get("confidence"), d.get("reasoning"),
                 1 if executed else 0))
            self._conn.commit()

    # ── 자산 곡선 / MDD ─────────────────────────────────────

    def snapshot_equity(self, balance: float, equity: float):
        with self._lock:
            self._conn.execute("INSERT INTO equity (time, balance, equity) VALUES (?,?,?)",
                               (datetime.now(timezone.utc).isoformat(), balance, equity))
            self._conn.commit()

    def peak_equity(self) -> float:
        row = self._conn.execute("SELECT MAX(equity) FROM equity").fetchone()
        return row[0] or 0.0

    # ── 성과 지표 (#14 자기 평가의 데이터 소스) ─────────────

    def compute_metrics(self) -> Dict[str, Any]:
        profits = self.closed_profits(limit=100000)
        n = len(profits)
        if n == 0:
            return {"n": 0}
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0
        expectancy = round(sum(profits) / n, 2)
        avg_rr = None
        if wins and losses:
            avg_rr = round((gross_win / len(wins)) / (gross_loss / len(losses)), 2)
        # 자산곡선 MDD / Recovery Factor
        curve, peak, mdd = 0.0, 0.0, 0.0
        for p in profits:
            curve += p
            peak = max(peak, curve)
            mdd = max(mdd, peak - curve)
        net = round(sum(profits), 2)
        recovery = round(net / mdd, 2) if mdd > 0 else None
        r_rows = self._conn.execute(
            "SELECT AVG(r_multiple) FROM trades WHERE status='CLOSED' AND r_multiple IS NOT NULL"
        ).fetchone()
        slip_rows = self._conn.execute(
            "SELECT AVG(slippage), AVG(latency_ms) FROM trades "
            "WHERE status='CLOSED' AND slippage IS NOT NULL").fetchone()
        return {
            "n": n,
            "win_rate": round(len(wins) / n * 100, 1),
            "profit_factor": pf,
            "expectancy_usd": expectancy,
            "avg_rr": avg_rr,
            "avg_r_multiple": round(r_rows[0], 2) if r_rows[0] is not None else None,
            "net_profit": net,
            "max_drawdown_usd": round(mdd, 2),
            "recovery_factor": recovery,
            "avg_slippage": round(slip_rows[0], 3) if slip_rows[0] is not None else None,
            "avg_latency_ms": int(slip_rows[1]) if slip_rows[1] is not None else None,
        }
