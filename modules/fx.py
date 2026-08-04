"""원/달러 환율 — 한 곳에서만 가져온다.

달러 종목을 원화로 환산하는 자리가 여러 개다. 러너의 매수여력 비교, 가상
장부의 평가액, 일일 보고의 총자산. 각자 가져오면 같은 날 보고서마다 다른
환율이 쓰이고, 나중에 숫자가 왜 어긋났는지 추적할 수 없다. 금액 단위가
갈라지는 사고는 이 저장소에서 이미 두 번 났다.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

DEFAULT_KRW_PER_USD = 1400.0


def fetch_krw_per_usd(fallback: float = DEFAULT_KRW_PER_USD) -> float:
    """Yahoo Finance KRW=X로 실시간 원/달러 환율 조회. 실패 시 fallback 반환."""
    try:
        raw = yf.download("KRW=X", period="5d", progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.droplevel(1)
        if not raw.empty:
            rate = float(raw["Close"].dropna().iloc[-1])
            print(f"  [환율] USD/KRW 실시간: {rate:,.1f}원")
            return rate
    except Exception as e:
        print(f"  [환율] 실시간 조회 실패 ({e}) → {fallback:,.0f}원 사용")
    return fallback
