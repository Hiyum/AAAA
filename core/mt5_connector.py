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

    def get_symbol_specs(self, symbol: str) -> Dict[str, float]:
        """종목 계약 스펙 (lot 계산에 필수).
        contract_size: 1 lot의 기초자산 수량 (외환 100,000 / 금 보통 100)
        """
        if not self.simulation_mode and MT5_AVAILABLE:
            info = mt5.symbol_info(symbol)
            if info is not None:
                return {
                    "contract_size": float(info.trade_contract_size or 100000.0),
                    "volume_min": float(info.volume_min or 0.01),
                    "volume_max": float(info.volume_max or 100.0),
                    "volume_step": float(info.volume_step or 0.01),
                }
        # 시뮬레이션/조회실패 폴백: 6자리 알파벳이면 외환으로 간주
        is_fx = len(symbol.replace("#", "").replace(".", "")) == 6 and symbol[:6].isalpha()
        return {
            "contract_size": 100000.0 if is_fx else 100.0,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        }

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

        # 심볼을 Market Watch에 등록 (안 되어 있으면 주문 거부됨)
        mt5.symbol_select(symbol, True)

        # 종목이 실제 지원하는 filling mode를 조회해서 그것부터 시도.
        # symbol_info.filling_mode는 비트마스크: 1=FOK, 2=IOC
        info = mt5.symbol_info(symbol)
        filling_modes = []
        if info is not None:
            fm = info.filling_mode
            if fm & 2:   # SYMBOL_FILLING_IOC
                filling_modes.append(mt5.ORDER_FILLING_IOC)
            if fm & 1:   # SYMBOL_FILLING_FOK
                filling_modes.append(mt5.ORDER_FILLING_FOK)
        # 조회 실패/미지원 시 대비해 나머지도 뒤에 추가 (중복 제거)
        for mode in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            if mode not in filling_modes:
                filling_modes.append(mode)

        # ── SL/TP 보정 (코드 10016 Invalid stops 방지) ──────────
        # 브로커의 실제 현재가 + 최소 정지거리(stops level)에 맞춰 손절/목표 조정
        tick = mt5.symbol_info_tick(symbol)
        if info is not None and tick is not None:
            point = info.point or 0.01
            digits = info.digits or 2
            # BUY는 ask에, SELL은 bid에 체결
            exec_price = tick.ask if action == "BUY" else tick.bid
            if exec_price and exec_price > 0:
                price = exec_price
            # 최소 거리 = max(정지거리, 스프레드) * point, 0이면 안전 기본값
            min_dist = max(info.trade_stops_level, info.spread) * point
            if min_dist <= 0:
                min_dist = 20 * point
            min_dist *= 1.5  # 여유 버퍼

            if action == "BUY":
                if sl and sl > 0:
                    sl = min(sl, price - min_dist)
                if tp and tp > 0:
                    tp = max(tp, price + min_dist)
            else:  # SELL
                if sl and sl > 0:
                    sl = max(sl, price + min_dist)
                if tp and tp > 0:
                    tp = min(tp, price - min_dist)

            sl = round(sl, digits) if sl else 0.0
            tp = round(tp, digits) if tp else 0.0
            price = round(price, digits)

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
            # filling mode 문제(10030)가 아니면 더 시도해도 소용없으니 중단
            if result is not None and result.retcode != 10030:
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

        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return {"success": False, "message": f"청산 실패: {pos.symbol} 시세 조회 불가"}
        price = tick.bid if pos.type == 0 else tick.ask

        # 진입과 동일하게 종목 지원 filling mode 자동 감지 (IOC 고정이던 버그 수정)
        info = mt5.symbol_info(pos.symbol)
        filling_modes = []
        if info is not None:
            fm = info.filling_mode
            if fm & 2:
                filling_modes.append(mt5.ORDER_FILLING_IOC)
            if fm & 1:
                filling_modes.append(mt5.ORDER_FILLING_FOK)
        for mode in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            if mode not in filling_modes:
                filling_modes.append(mode)

        result = None
        for filling in filling_modes:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": order_type,
                "position": ticket,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "ClaudeAI-Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                break
            if result is not None and result.retcode != 10030:
                break

        if result is None:
            return {"success": False, "message": f"청산 실패: MT5 응답 없음 ({mt5.last_error()})"}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "message": f"청산 실패 (코드:{result.retcode}): {result.comment}"}

        return {"success": True, "ticket": ticket, "profit": pos.profit}

    def modify_position(self, ticket: int, new_sl: float, new_tp: float) -> Dict[str, Any]:
        """손절가/목표가 수정"""
        if self.simulation_mode:
            return {"success": True, "ticket": ticket, "simulation": True}

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "message": "포지션 없음"}

        pos = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = result.comment if result else str(mt5.last_error())
            return {"success": False, "message": f"수정 실패: {err}"}

        return {"success": True, "ticket": ticket, "new_sl": new_sl, "new_tp": new_tp}
