import time
import logging
from typing import Optional, Dict, Any, List
from config import Config

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 패키지 없음 - 시뮬레이션 모드로 실행")


class MT5Connector:

    def __init__(self):
        self.connected = False
        self.account_info = {}
        self.simulation_mode = not MT5_AVAILABLE

    def connect(self, login: str, password: str, server: str) -> Dict[str, Any]:
        if self.simulation_mode:
            self.connected = True
            self.account_info = {
                "login": int(login) if login else 0,
                "balance": 10000.0,
                "equity": 10000.0,
                "profit": 0.0,
                "currency": "USD",
                "server": server,
                "name": "시뮬레이션 계좌",
            }
            return {"success": True, "message": "시뮬레이션 모드로 연결됨", "account": self.account_info}

        # 먼저 로그인 정보와 함께 초기화 시도
        if not mt5.initialize(login=int(login), password=password, server=server):
            # 실패 시 일반 초기화 후 별도 로그인 시도
            mt5.shutdown()
            if not mt5.initialize():
                return {"success": False, "message": f"MT5 초기화 실패: {mt5.last_error()} - MT5 터미널이 실행 중인지 확인하세요"}
            if not mt5.login(int(login), password=password, server=server):
                return {"success": False, "message": f"MT5 로그인 실패: {mt5.last_error()} - 계좌번호/비밀번호/서버를 확인하세요"}

        info = mt5.account_info()
        if info is None:
            return {"success": False, "message": "계좌 정보 조회 실패"}

        self.connected = True
        self.account_info = {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "profit": info.profit,
            "currency": info.currency,
            "server": info.server,
            "name": info.name,
        }
        return {"success": True, "message": "MT5 연결 성공", "account": self.account_info}

    def disconnect(self):
        if not self.simulation_mode and MT5_AVAILABLE:
            mt5.shutdown()
        self.connected = False
        self.account_info = {}

    def get_account_info(self) -> Dict[str, Any]:
        if self.simulation_mode:
            return self.account_info

        if not self.connected:
            return {}

        info = mt5.account_info()
        if info is None:
            return self.account_info

        self.account_info.update({
            "balance": info.balance,
            "equity": info.equity,
            "profit": info.profit,
        })
        return self.account_info

    def get_current_price(self, symbol: str) -> Optional[float]:
        if self.simulation_mode:
            prices = {"XAUUSD": 2350.0, "BTCUSD": 65000.0, "ETHUSD": 3500.0, "XAGUSD": 28.5}
            return prices.get(symbol, 100.0)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return (tick.bid + tick.ask) / 2

    def get_ohlcv(self, symbol: str, timeframe_str: str, count: int = 200):
        import pandas as pd

        tf_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440,
        }

        if self.simulation_mode:
            import numpy as np
            np.random.seed(42)
            dates = pd.date_range(end=pd.Timestamp.now(), periods=count, freq=f"{tf_map.get(timeframe_str, 5)}min")
            base = 2350.0
            close = base + np.cumsum(np.random.randn(count) * 0.5)
            high = close + np.abs(np.random.randn(count) * 0.3)
            low = close - np.abs(np.random.randn(count) * 0.3)
            open_ = close + np.random.randn(count) * 0.1
            volume = np.random.randint(100, 1000, count).astype(float)
            return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)

        tf_const_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_const_map.get(timeframe_str, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            return None

        import pandas as pd
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

    def place_order(self, symbol: str, action: str, lot: float,
                    price: float, sl: float, tp: float, comment: str = "") -> Dict[str, Any]:
        if self.simulation_mode:
            ticket = int(time.time())
            return {
                "success": True,
                "ticket": ticket,
                "symbol": symbol,
                "action": action,
                "lot": lot,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": comment,
                "simulation": True,
            }

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        # 브로커마다 지원하는 filling mode가 다르므로 순서대로 시도
        filling_modes = [
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
        ]

        result = None
        last_error_msg = ""
        for filling in filling_modes:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 234000,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                break
            last_error_msg = result.comment if result else str(mt5.last_error())

        if result is None:
            return {"success": False, "message": f"MT5 주문 응답 없음: {mt5.last_error()} - MT5 터미널에서 자동매매(알고리즘 트레이딩)를 허용했는지 확인하세요"}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "message": f"주문 실패 (코드:{result.retcode}): {result.comment}"}

        return {
            "success": True,
            "ticket": result.order,
            "symbol": symbol,
            "action": action,
            "lot": lot,
            "price": result.price,
            "sl": sl,
            "tp": tp,
        }

    def get_open_positions(self) -> List[Dict]:
        if self.simulation_mode:
            return []

        positions = mt5.positions_get()
        if positions is None:
            return []

        result = []
        for pos in positions:
            result.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == 0 else "SELL",
                "volume": pos.volume,
                "open_price": pos.price_open,
                "current_price": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
            })
        return result

    def close_position(self, ticket: int) -> Dict[str, Any]:
        if self.simulation_mode:
            return {"success": True, "ticket": ticket, "simulation": True}

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "message": "포지션 없음"}

        pos = position[0]
        order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "AI 청산",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "message": f"청산 실패: {result.comment}"}

        return {"success": True, "ticket": ticket, "profit": pos.profit}
