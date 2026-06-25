"""
=============================================================
  STRATEGY PLUGIN INTERFACE - 전략 교체 가이드
=============================================================

새로운 매매 전략으로 교체하고 싶을 때:
1. 이 파일(base_strategy.py)은 절대 수정하지 마세요.
2. scalping_strategy.py 파일 내용을 전부 삭제하세요.
3. Claude AI에게 아래 형식에 맞는 새 전략 코드를 요청하세요.
4. 받은 코드를 scalping_strategy.py에 붙여넣기 하세요.
5. 시스템 재시작 없이 대시보드에서 전략 리로드 버튼을 누르면 적용됩니다.

Claude AI에게 전략 요청 예시:
  "다음 트레이더 기술을 BaseStrategy를 상속받는
   Python 클래스로 구현해줘: [전략 설명]"

=============================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    action: str           # "BUY", "SELL", "HOLD"
    symbol: str
    confidence: float     # 0.0 ~ 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    reason: str           # 신호 이유 (로그용)


class BaseStrategy(ABC):
    """
    모든 전략은 이 클래스를 상속받아야 합니다.
    analyze() 메서드 하나만 구현하면 됩니다.
    """

    def __init__(self, symbol: str, timeframe: str = "M5"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.name = self.__class__.__name__

    @abstractmethod
    def analyze(self, df: pd.DataFrame, current_price: float, account_balance: float) -> Signal:
        """
        시장 데이터를 분석하여 매매 신호를 반환합니다.

        Args:
            df: OHLCV 캔들 데이터 (columns: open, high, low, close, volume)
            current_price: 현재 가격
            account_balance: 현재 계좌 잔고

        Returns:
            Signal 객체
        """
        pass

    def get_name(self) -> str:
        return self.name
