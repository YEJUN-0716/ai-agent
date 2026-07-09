"""
일별 P&L 리포트
==================
GitHub Actions cron으로 매일 23:00 UTC 자동 실행.
팩터 계산 없이 Alpaca 포지션 조회 + equity_log.json 읽기만 하므로 1분 이내 완료.

환경변수:
  ALPACA_API_KEY    (필수)
  ALPACA_SECRET_KEY (필수)
  ALPACA_MODE       paper(기본) / live
  TELEGRAM_TOKEN    (필수)
  TELEGRAM_CHAT_ID  (필수)
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import requests

_ALPACA_MODE = os.environ.get("ALPACA_MODE", "paper").strip().lower()
_ALPACA_BASE = ("https://api.alpaca.markets" if _ALPACA_MODE == "live"
                else "https://paper-api.alpaca.markets")

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
EQUITY_LOG_FILE = "equity_log.json"


def _h():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def alpaca_get(path: str):
    r = requests.get(f"{_ALPACA_BASE}{path}", headers=_h(), timeout=15)
    r.raise_for_status()
    return r.json()


def send_tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 환경변수 없음 — 발송 생략")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )
    print("[TG] 발송 성공" if resp.status_code == 200 else f"[TG 오류] {resp.status_code}")


def load_equity_log() -> list:
    if os.path.exists(EQUITY_LOG_FILE):
        try:
            with open(EQUITY_LOG_FILE) as f:
                return json.load(f).get("records", [])
        except Exception:
            pass
    return []


def calc_perf(records: list) -> dict:
    if len(records) < 2:
        return {}
    equities   = [r["equity"]          for r in records]
    spy_prices = [r.get("spy_price")   for r in records]

    port_rets = [(equities[i] / equities[i-1] - 1) for i in range(1, len(equities))]
    mean_r = float(np.mean(port_rets))
    std_r  = float(np.std(port_rets))
    sharpe = (mean_r / (std_r + 1e-9)) * np.sqrt(252) if std_r > 0 else 0.0

    peak_e, max_dd = equities[0], 0.0
    for e in equities:
        peak_e = max(peak_e, e)
        max_dd = min(max_dd, (e / peak_e - 1) * 100)

    total_ret = (equities[-1] / equities[0] - 1) * 100
    spy_valid = [p for p in spy_prices if p]
    spy_ret   = (spy_valid[-1] / spy_valid[0] - 1) * 100 if len(spy_valid) >= 2 else 0.0

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if records[-1].get("date") == today_str and len(equities) >= 2:
        today_ret: float | None = (equities[-1] / equities[-2] - 1) * 100
    else:
        today_ret = None  # paper-trade가 오늘 실행 안 됐으면 당일 P&L 표시 안 함

    return {
        "today_ret":    round(today_ret, 2) if today_ret is not None else None,
        "total_return": round(total_ret, 2),
        "spy_return":   round(spy_ret, 2),
        "alpha":        round(total_ret - spy_ret, 2),
        "sharpe":       round(sharpe, 2),
        "max_dd":       round(max_dd, 2),
        "n_days":       len(records),
    }


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"일별 P&L 리포트  {today}")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[오류] Alpaca 환경변수 없음"); sys.exit(1)

    try:
        acct = alpaca_get("/v2/account")
    except Exception as e:
        print(f"[오류] 계정 조회 실패: {e}"); sys.exit(1)

    equity = float(acct.get("equity", 0))
    cash   = float(acct.get("cash", 0))

    try:
        positions = alpaca_get("/v2/positions")
    except Exception as e:
        positions = []
        print(f"[경고] 포지션 조회 실패: {e}")

    records = load_equity_log()
    perf    = calc_perf(records)

    mode_tag = "[LIVE]" if _ALPACA_MODE == "live" else "[PAPER]"
    lines = [
        f"*📊 일별 P&L 리포트* `{today}` {mode_tag}",
        f"총 자산 `${equity:,.2f}`  현금 `${cash:,.2f}`",
    ]

    # 당일 수익률
    if perf and perf.get("today_ret") is not None:
        emoji = "📈" if perf["today_ret"] >= 0 else "📉"
        lines.append(f"{emoji} 당일 *{perf['today_ret']:+.2f}%*")
    elif perf:
        lines.append("⚠️ 오늘 페이퍼트레이드 미실행 — 당일 수익률 없음")

    # 포지션별 손익 (수익률 내림차순)
    if positions:
        lines.append(f"\n*보유 포지션 {len(positions)}개*")
        for p in sorted(positions,
                        key=lambda x: float(x.get("unrealized_plpc", 0) or 0),
                        reverse=True):
            sym     = p["symbol"]
            qty     = p["qty"]
            cur     = float(p.get("current_price",   0) or 0)
            pnl     = float(p.get("unrealized_pl",   0) or 0)
            pnl_pct = float(p.get("unrealized_plpc", 0) or 0) * 100
            sign    = "+" if pnl >= 0 else ""
            lines.append(
                f"  `{sym}` {qty}주  ${cur:.2f}  "
                f"{sign}{pnl_pct:.1f}% (${sign}{pnl:.0f})"
            )
    else:
        lines.append("\n보유 포지션 없음")

    # 누적 성과
    if perf and perf["n_days"] >= 2:
        lines.append(
            f"\n*누적 성과 ({perf['n_days']}일)*\n"
            f"  수익률 {perf['total_return']:+.1f}%  "
            f"SPY {perf['spy_return']:+.1f}%  "
            f"알파 {perf['alpha']:+.1f}%\n"
            f"  샤프 {perf['sharpe']:.2f}  최대낙폭 {perf['max_dd']:.1f}%"
        )

    msg = "\n".join(lines)
    print(msg)
    send_tg(msg)


if __name__ == "__main__":
    main()
