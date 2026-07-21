"""
전략 백테스트 모듈
=================================================================
paper_trade_runner.py 와 동일한 매매 규칙을 과거 데이터로 시뮬레이션.
매일 트레일링 스톱 점검, 월별 리밸런싱(21거래일), 거래비용 반영.

IC 분석(factor_validator.py)과 달리 실제 포지션 진입·청산을 시뮬레이션하여
더 현실적인 성과 지표를 제공합니다.

체결 타이밍: 모든 매매 판단(팩터 점수, 트레일링 스톱 트리거)은 당일 종가
기준으로 내리되, 실제 체결은 다음 거래일 시가로 미룬다(D+1 시가 체결).
modules/risk_management.py::run_backtest_sized와 동일한 규칙 — 판단에 쓴
바로 그 종가로 같은 날 체결하면 룩어헤드가 된다.
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional


def _fetch_ohlcv(tickers: list, start: str, end: str) -> dict:
    """각 티커의 OHLCV DataFrame 반환: {ticker: DataFrame}"""
    result = {}
    for tk in tickers:
        try:
            yahoo_sym = tk.replace(".", "-")
            raw = yf.download(yahoo_sym, start=start, end=end,
                              progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            if not raw.empty and "Close" in raw.columns and len(raw) >= 60:
                result[tk] = raw.dropna(subset=["Open", "High", "Low", "Close"])
        except Exception:
            pass
    return result


def _compute_factor_scores(ohlcv_dict: dict, as_of_date) -> dict:
    """
    as_of_date 기준 단순 모멘텀+저변동성 팩터 점수 (look-ahead 없음).
    반환: {ticker: composite_score}
    """
    rows = []
    for tk, df in ohlcv_dict.items():
        sub = df[df.index <= as_of_date]
        if len(sub) < 64:
            continue
        close  = sub["Close"]
        mom_3m = float((close.iloc[-1] / close.iloc[-63] - 1) * 100)
        mom_1m = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 22 else 0.0
        vol_21 = float(close.pct_change().iloc[-21:].std() * np.sqrt(252) * 100) if len(close) >= 22 else 100.0
        rows.append({"ticker": tk, "mom_3m": mom_3m, "mom_1m": mom_1m, "vol": vol_21})

    if len(rows) < 3:
        return {}

    df_sc = pd.DataFrame(rows).set_index("ticker")
    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df_sc[col].mean(), df_sc[col].std()
        df_sc[f"z_{col}"] = (df_sc[col] - mu) / (sigma + 1e-9)
    mu, sigma = df_sc["vol"].mean(), df_sc["vol"].std()
    df_sc["z_low_vol"] = -(df_sc["vol"] - mu) / (sigma + 1e-9)

    df_sc["composite_z"] = (0.27 * df_sc["z_mom_3m"]
                             + 0.18 * df_sc["z_mom_1m"]
                             + 0.18 * df_sc["z_low_vol"])
    mn, mx = df_sc["composite_z"].min(), df_sc["composite_z"].max()
    df_sc["score"] = 20 + (df_sc["composite_z"] - mn) / (mx - mn + 1e-9) * 60
    return df_sc["score"].to_dict()


def _compute_metrics(equity_curve: pd.Series, trades: list,
                      initial_capital: float = 10_000_000) -> dict:
    """백테스트 성과 지표 계산."""
    if equity_curve.empty or len(equity_curve) < 2:
        return {}

    rets   = equity_curve.pct_change().dropna()
    mean_r = float(rets.mean())
    std_r  = float(rets.std())
    sharpe = (mean_r / (std_r + 1e-9)) * np.sqrt(252)

    peak   = equity_curve.cummax()
    dd     = (equity_curve / peak - 1) * 100
    max_dd = float(dd.min())

    total_ret = (float(equity_curve.iloc[-1]) / initial_capital - 1) * 100
    n_days    = len(equity_curve)
    ann_ret   = ((1 + total_ret / 100) ** (252 / max(n_days, 1)) - 1) * 100

    completed = [t for t in trades if t.get("return_pct") is not None]
    win_rate  = (sum(1 for t in completed if t["return_pct"] > 0) / len(completed) * 100
                 if completed else 0.0)
    avg_hold  = float(np.mean([t.get("hold_days", 0) for t in completed])) if completed else 0.0

    return {
        "total_return_pct":  round(total_ret, 2),
        "annualized_return": round(ann_ret, 2),
        "sharpe_ratio":      round(sharpe, 2),
        "max_drawdown_pct":  round(max_dd, 2),
        "n_trades":          len(completed),
        "win_rate_pct":      round(win_rate, 1),
        "avg_hold_days":     round(avg_hold, 1),
        "n_days":            n_days,
    }


def run_strategy_backtest(
    tickers: list,
    lookback_years: int = 3,
    rebal_days: int = 21,
    top_n: int = 5,
    max_positions: int = 10,
    buy_score_min_pctile: float = 0.6,
    trail_stop_pct: float = 10.0,
    portfolio_dd_stop_pct: float = 15.0,
    target_vol_pct: float = 0.15,
    cost_bps: float = 10.0,
    initial_capital: float = 10_000_000,
    weights: Optional[dict] = None,
) -> dict:
    """
    전략 백테스트 실행.

    Parameters
    ----------
    tickers                : 유니버스 티커 목록
    lookback_years         : 백테스트 기간 (년)
    rebal_days             : 리밸런싱 간격 (거래일)
    top_n                  : 매수 후보 상위 N개
    max_positions          : 최대 동시 포지션 수
    buy_score_min_pctile   : 매수 최소 점수 퍼센타일 (0~1)
    trail_stop_pct         : 트레일링 스톱 %
    portfolio_dd_stop_pct  : 포트폴리오 드로다운 한도 %
    target_vol_pct         : 변동성 기반 사이징 목표 연율화 변동성
    cost_bps               : 왕복 거래비용 (베이시스 포인트)
    initial_capital        : 초기 자본금
    weights                : 팩터 가중치 override dict (미사용, 확장용)

    Returns
    -------
    {
      "equity_curve": pd.Series(index=날짜, values=자산),
      "trades":       list[dict],
      "metrics":      dict,
      "start_date":   str,
      "end_date":     str,
    }
    """
    end_dt    = datetime.now()
    start_dt  = end_dt - timedelta(days=lookback_years * 365 + 90)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str   = end_dt.strftime("%Y-%m-%d")

    ohlcv_dict = _fetch_ohlcv(tickers, start_str, end_str)
    if len(ohlcv_dict) < 3:
        return {"equity_curve": pd.Series(dtype=float), "trades": [],
                "metrics": {}, "start_date": start_str, "end_date": end_str}

    all_closes = pd.DataFrame(
        {tk: df["Close"] for tk, df in ohlcv_dict.items()}
    ).dropna(how="all")
    dates   = all_closes.index
    min_idx = 64

    capital     = float(initial_capital)
    positions   = {}   # {ticker: {"shares", "entry_price", "peak", "entry_date"}}
    equity_log  = {}
    trades      = []
    peak_equity = float(initial_capital)
    dd_blocked  = False
    # D+1 시가체결 큐: 전일 종가로 내린 판단을 다음 거래일 시가로 미뤄서 체결
    pending_sells = []   # [(ticker, reason)]
    pending_buys  = []   # [(ticker, ann_vol)]

    for day_idx, today in enumerate(dates):
        if day_idx < min_idx:
            equity_log[today] = capital
            continue

        # ── 전일 판단분 체결: 오늘 시가로 실행 (룩어헤드 방지) ──
        still_pending_sells = []
        for tk, reason in pending_sells:
            if tk not in positions:
                continue
            op    = ohlcv_dict[tk]["Open"]
            avail_open = op[op.index <= today]
            if avail_open.empty or avail_open.index[-1] != today:
                still_pending_sells.append((tk, reason))
                continue
            sell_price = float(avail_open.iloc[-1])
            pos      = positions.pop(tk)
            cost     = pos["shares"] * sell_price * (cost_bps / 10000)
            capital += pos["shares"] * sell_price - cost
            trades.append({
                "ticker":      tk,
                "entry_date":  str(pos["entry_date"].date()),
                "exit_date":   str(today.date()),
                "entry_price": round(pos["entry_price"], 4),
                "exit_price":  round(sell_price, 4),
                "return_pct":  round((sell_price / pos["entry_price"] - 1) * 100, 2),
                "hold_days":   (today - pos["entry_date"]).days,
                "reason":      reason,
            })
        pending_sells = still_pending_sells

        still_pending_buys = []
        for tk, ann_vol in pending_buys:
            if tk in positions:
                continue
            op    = ohlcv_dict[tk]["Open"]
            avail_open = op[op.index <= today]
            if avail_open.empty or avail_open.index[-1] != today:
                still_pending_buys.append((tk, ann_vol))
                continue
            entry_price = float(avail_open.iloc[-1])
            if entry_price <= 0:
                continue
            notional = capital * (target_vol_pct / ann_vol) / max_positions
            notional = max(capital * 0.01, min(capital * 0.20, notional))
            if capital < notional * 1.05:
                continue
            cost    = notional * (cost_bps / 10000 / 2)
            shares  = (notional - cost) / entry_price
            capital -= notional
            positions[tk] = {
                "shares":      shares,
                "entry_price": entry_price,
                "peak":        entry_price,
                "entry_date":  today,
            }
        pending_buys = still_pending_buys

        # ── 평가액 계산 (오늘 종가 기준 마킹 — 체결가와 무관, 룩어헤드 아님) ──
        port_value = capital
        for tk, pos in positions.items():
            cs = ohlcv_dict[tk]["Close"]
            avail = cs[cs.index <= today]
            if not avail.empty:
                port_value += pos["shares"] * float(avail.iloc[-1])

        peak_equity = max(peak_equity, port_value)
        dd_pct      = (port_value / peak_equity - 1) * 100
        dd_blocked  = portfolio_dd_stop_pct > 0 and dd_pct < -portfolio_dd_stop_pct
        equity_log[today] = port_value

        # 트레일링 스톱 (매일 점검, 오늘 종가로 판단 → 체결은 다음 거래일 시가로 예약)
        for tk, pos in list(positions.items()):
            cs = ohlcv_dict[tk]["Close"]
            avail = cs[cs.index <= today]
            if avail.empty:
                continue
            cur_price  = float(avail.iloc[-1])
            pos["peak"] = max(pos["peak"], cur_price)
            trail_line = pos["peak"] * (1 - trail_stop_pct / 100)
            if cur_price < trail_line and not any(t == tk for t, _ in pending_sells):
                pending_sells.append((tk, "trail_stop"))

        # 리밸런싱 주기 체크
        if day_idx % rebal_days != 0:
            continue
        if dd_blocked:
            continue

        scores = _compute_factor_scores(ohlcv_dict, today)
        if len(scores) < 3:
            continue

        sorted_tks = sorted(scores, key=lambda t: scores[t], reverse=True)
        buy_set    = set(sorted_tks[:top_n])
        score_vals = sorted(scores.values())
        threshold  = score_vals[int(len(score_vals) * buy_score_min_pctile)]

        # 매도 예약: 순위 이탈 종목 (오늘 종가로 판단 → 체결은 다음 거래일 시가)
        for tk in list(positions.keys()):
            if tk in buy_set:
                continue
            if any(t == tk for t, _ in pending_sells):
                continue  # 이미 트레일링 스톱으로 매도 예약됨
            pending_sells.append((tk, "rebal_sell"))

        # 매수 예약: 오늘 종가 기준 팩터 점수로 후보 선정 → 체결은 다음 거래일 시가
        n_slots = max_positions - len(positions) - len(pending_buys)
        for tk in sorted_tks[:top_n]:
            if n_slots <= 0:
                break
            if tk in positions or scores.get(tk, 0) < threshold:
                continue
            if any(t == tk for t, _ in pending_buys):
                continue
            cs      = ohlcv_dict[tk]["Close"]
            ret_s   = cs[cs.index <= today].pct_change().dropna().tail(21)
            ann_vol = max(float(ret_s.std() * np.sqrt(252)) if len(ret_s) >= 5 else 0.20, 0.05)
            pending_buys.append((tk, ann_vol))
            n_slots -= 1

    # 잔여 포지션 청산
    final_date = dates[-1]
    for tk, pos in list(positions.items()):
        cs    = ohlcv_dict[tk]["Close"]
        avail = cs[cs.index <= final_date]
        if avail.empty:
            continue
        sell_price = float(avail.iloc[-1])
        cost       = pos["shares"] * sell_price * (cost_bps / 10000)
        capital   += pos["shares"] * sell_price - cost
        trades.append({
            "ticker":      tk,
            "entry_date":  str(pos["entry_date"].date()),
            "exit_date":   str(final_date.date()),
            "entry_price": round(pos["entry_price"], 4),
            "exit_price":  round(sell_price, 4),
            "return_pct":  round((sell_price / pos["entry_price"] - 1) * 100, 2),
            "hold_days":   (final_date - pos["entry_date"]).days,
            "reason":      "end_of_backtest",
        })
    equity_log[final_date] = capital

    equity_curve = pd.Series(equity_log).sort_index()
    metrics      = _compute_metrics(equity_curve, trades, initial_capital)

    return {
        "equity_curve": equity_curve,
        "trades":       trades,
        "metrics":      metrics,
        "start_date":   start_str,
        "end_date":     end_str,
    }


def compare_with_without_cost(tickers: list, cost_bps: float = 10.0, **kwargs) -> dict:
    """
    거래비용 포함/미포함으로 두 번 백테스트하여 수수료 부담을 정량화.
    """
    with_cost    = run_strategy_backtest(tickers, cost_bps=cost_bps,    **kwargs)
    without_cost = run_strategy_backtest(tickers, cost_bps=0.0,         **kwargs)
    return {
        "with_cost":     {"metrics": with_cost["metrics"]},
        "without_cost":  {"metrics": without_cost["metrics"]},
        "cost_drag_pct": round(
            without_cost["metrics"].get("total_return_pct", 0)
            - with_cost["metrics"].get("total_return_pct", 0),
            2,
        ),
    }
