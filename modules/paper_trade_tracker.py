"""
Alpaca 페이퍼 트레이딩 성과 추적
=================================================================
Alpaca REST API에서 직접 데이터를 가져와서
성과 지표(수익률·MDD·Sharpe)를 계산하고 대시보드에 넘겨준다.

endpoint:
  GET /v2/account                 → 현재 자산, 매수여력
  GET /v2/positions               → 현재 보유 포지션
  GET /v2/orders?status=closed    → 체결된 주문 내역
  GET /v2/account/portfolio/history → 일별 equity 곡선
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import requests

PAPER_BASE = "https://paper-api.alpaca.markets"


def _h(key: str, secret: str) -> dict:
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_account_summary(key: str, secret: str) -> dict:
    """계좌 요약: 자산·손익·매수여력."""
    r = requests.get(f"{PAPER_BASE}/v2/account", headers=_h(key, secret), timeout=10)
    r.raise_for_status()
    d = r.json()
    equity   = float(d.get("equity", 0))
    initial  = float(d.get("last_equity", equity))  # 전 영업일 종가 기준
    day_pl   = equity - initial
    day_pl_pct = day_pl / initial * 100 if initial else 0.0
    return {
        "equity":          equity,
        "buying_power":    float(d.get("buying_power", 0)),
        "cash":            float(d.get("cash", 0)),
        "portfolio_value": float(d.get("portfolio_value", equity)),
        "day_pl":          day_pl,
        "day_pl_pct":      round(day_pl_pct, 3),
        "trading_blocked": d.get("trading_blocked", False),
        "status":          d.get("status", "unknown"),
    }


def get_open_positions(key: str, secret: str) -> pd.DataFrame:
    """현재 보유 포지션 → DataFrame."""
    r = requests.get(f"{PAPER_BASE}/v2/positions", headers=_h(key, secret), timeout=10)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)[
        ["symbol", "qty", "avg_entry_price", "current_price",
         "unrealized_pl", "unrealized_plpc", "market_value", "side"]
    ].copy()
    for col in ["qty", "avg_entry_price", "current_price",
                "unrealized_pl", "unrealized_plpc", "market_value"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["unrealized_plpc"] = df["unrealized_plpc"] * 100   # → %
    df = df.rename(columns={
        "symbol": "티커",
        "qty": "수량",
        "avg_entry_price": "평균단가($)",
        "current_price": "현재가($)",
        "unrealized_pl": "미실현손익($)",
        "unrealized_plpc": "수익률(%)",
        "market_value": "평가금액($)",
        "side": "방향",
    })
    return df.sort_values("수익률(%)", ascending=False).reset_index(drop=True)


def get_portfolio_history(key: str, secret: str,
                           period: str = "1M", timeframe: str = "1D") -> pd.DataFrame:
    """
    일별 equity 곡선 → DataFrame(date, equity, profit_loss, return_pct).
    period: '1W' | '1M' | '3M' | '6M' | '1A'
    timeframe: '1D' | '1H'
    """
    params = {"period": period, "timeframe": timeframe, "extended_hours": "false"}
    r = requests.get(f"{PAPER_BASE}/v2/account/portfolio/history",
                     headers=_h(key, secret), params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    timestamps = d.get("timestamp", [])
    equities   = d.get("equity", [])
    pls        = d.get("profit_loss", [])
    if not timestamps:
        return pd.DataFrame()
    df = pd.DataFrame({
        "date":        pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("America/New_York").date,
        "equity":      equities,
        "profit_loss": pls,
    })
    df["equity"] = pd.to_numeric(df["equity"], errors="coerce")
    df["profit_loss"] = pd.to_numeric(df["profit_loss"], errors="coerce")
    df = df.dropna(subset=["equity"]).reset_index(drop=True)
    df["return_pct"] = df["equity"].pct_change() * 100
    return df


def get_closed_orders(key: str, secret: str, limit: int = 200) -> pd.DataFrame:
    """체결된 주문 내역 → DataFrame."""
    params = {"status": "closed", "limit": str(limit), "direction": "desc"}
    r = requests.get(f"{PAPER_BASE}/v2/orders",
                     headers=_h(key, secret), params=params, timeout=15)
    r.raise_for_status()
    orders = r.json()
    if not orders:
        return pd.DataFrame()
    rows = []
    for o in orders:
        rows.append({
            "날짜":        o.get("filled_at", o.get("submitted_at", ""))[:10],
            "티커":        o.get("symbol", ""),
            "방향":        o.get("side", ""),
            "수량":        o.get("filled_qty", o.get("qty", "")),
            "체결가($)":   o.get("filled_avg_price", ""),
            "금액($)":     o.get("notional", ""),
            "상태":        o.get("status", ""),
            "주문타입":    o.get("type", ""),
        })
    df = pd.DataFrame(rows)
    for col in ["수량", "체결가($)", "금액($)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_live_metrics(history_df: pd.DataFrame) -> dict:
    """
    equity 곡선에서 주요 성과지표 계산.
    backtest_metrics와 직접 비교할 수 있도록 같은 키 이름 사용.
    """
    if history_df.empty or len(history_df) < 3:
        return {"error": "데이터 부족"}
    eq = history_df["equity"].dropna()
    if len(eq) < 3:
        return {"error": "equity 데이터 부족"}
    initial = float(eq.iloc[0])
    final   = float(eq.iloc[-1])
    total_return = (final / initial - 1) * 100 if initial > 0 else 0.0

    roll_max = eq.expanding().max()
    mdd = float(((eq - roll_max) / roll_max * 100).min())

    daily_ret = eq.pct_change().dropna()
    if len(daily_ret) > 1 and float(daily_ret.std()) > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    n_days  = len(eq)
    ann_ret = (final / initial) ** (252 / n_days) - 1 if n_days > 1 and initial > 0 else 0.0

    return {
        "total_return_pct":  round(total_return, 2),
        "mdd_pct":           round(mdd, 2),
        "sharpe":            round(sharpe, 3),
        "annualized_ret_pct": round(ann_ret * 100, 2),
        "n_trading_days":    n_days,
        "start_equity":      round(initial, 2),
        "end_equity":        round(final, 2),
    }


def compare_with_benchmark(history_df: pd.DataFrame,
                             bt_total_return: float,
                             bt_sharpe: float,
                             bt_mdd: float) -> dict:
    """
    live 성과 vs 백테스트 성과 비교 테이블 데이터 생성.
    bt_* 값은 app.py 백테스트 결과에서 가져온 값.
    """
    live = compute_live_metrics(history_df)
    if "error" in live:
        return live

    gap_return = live["total_return_pct"] - bt_total_return
    gap_sharpe = live["sharpe"] - bt_sharpe
    gap_mdd    = live["mdd_pct"] - bt_mdd   # MDD는 음수, 더 음수면 실전이 더 나쁨

    return {
        "live":      live,
        "backtest": {
            "total_return_pct": bt_total_return,
            "sharpe":           bt_sharpe,
            "mdd_pct":          bt_mdd,
        },
        "gap": {
            "return_pct": round(gap_return, 2),
            "sharpe":     round(gap_sharpe, 3),
            "mdd_pct":    round(gap_mdd, 2),
        },
        "verdict": (
            "✅ 실전 성과가 백테스트와 비슷하거나 더 좋음 → 전략 유효성 지지"
            if gap_return > -5 and gap_sharpe > -0.3
            else "⚠️ 실전 성과가 백테스트 대비 유의하게 낮음 → 오버피팅 가능성 점검 필요"
        ),
    }
