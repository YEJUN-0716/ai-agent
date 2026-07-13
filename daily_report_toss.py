"""
토스증권 일별 P&L 리포트
=================================================================
GitHub Actions daily-report.yml의 toss-report 잡에서 self-hosted 러너로 실행.
equity_log.json(paper_trade_runner_toss.py가 기록) + Toss API 현재 포지션.

환경변수:
  TOSS_CLIENT_ID     (필수)
  TOSS_CLIENT_SECRET (필수)
  TOSS_ACCOUNT_SEQ   (필수, 기본 1)
  TELEGRAM_TOKEN     (필수)
  TELEGRAM_CHAT_ID   (필수)
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import requests

from modules.toss_trading import get_account, get_positions

TOSS_CLIENT_ID     = os.environ.get("TOSS_CLIENT_ID", "")
TOSS_CLIENT_SECRET = os.environ.get("TOSS_CLIENT_SECRET", "")
TOSS_ACCOUNT_SEQ   = os.environ.get("TOSS_ACCOUNT_SEQ", "1")
TG_TOKEN           = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "")
EQUITY_LOG_FILE    = "equity_log.json"


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
    equities  = [r["equity"] for r in records]
    port_rets = [(equities[i] / equities[i - 1] - 1) for i in range(1, len(equities))]
    mean_r    = float(np.mean(port_rets))
    std_r     = float(np.std(port_rets))
    sharpe    = (mean_r / (std_r + 1e-9)) * np.sqrt(252) if std_r > 0 else 0.0

    peak_e, max_dd = equities[0], 0.0
    for e in equities:
        peak_e = max(peak_e, e)
        max_dd = min(max_dd, (e / peak_e - 1) * 100)

    total_ret = (equities[-1] / equities[0] - 1) * 100
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_ret = None
    if records[-1].get("date") == today_str and len(equities) >= 2:
        today_ret = (equities[-1] / equities[-2] - 1) * 100

    return {
        "today_ret":    round(today_ret, 2) if today_ret is not None else None,
        "total_return": round(total_ret, 2),
        "sharpe":       round(sharpe, 2),
        "max_dd":       round(max_dd, 2),
        "n_days":       len(records),
    }


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"토스증권 P&L 리포트  {today}")

    if not TOSS_CLIENT_ID or not TOSS_CLIENT_SECRET:
        print("[오류] Toss 환경변수 없음"); sys.exit(1)

    try:
        acct = get_account(TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_ACCOUNT_SEQ)
    except Exception as e:
        print(f"[오류] 계정 조회 실패: {e}"); sys.exit(1)

    equity       = float(acct.get("equity", 0))
    buying_power = float(acct.get("buying_power", 0))

    try:
        positions = get_positions(TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_ACCOUNT_SEQ)
    except Exception as e:
        positions = []
        print(f"[경고] 포지션 조회 실패: {e}")

    records = load_equity_log()
    perf    = calc_perf(records)

    lines = [
        f"*📊 토스증권 P&L 리포트* `{today}` [TOSS]",
        f"총 자산 `{equity:,.0f}원`  매수여력 `{buying_power:,.0f}원`",
    ]

    if perf and perf.get("today_ret") is not None:
        emoji = "📈" if perf["today_ret"] >= 0 else "📉"
        lines.append(f"{emoji} 당일 *{perf['today_ret']:+.2f}%*")
    elif perf:
        lines.append("⚠️ 오늘 페이퍼트레이드 미실행 — 당일 수익률 없음")

    if positions:
        lines.append(f"\n*보유 포지션 {len(positions)}개*")
        for p in sorted(positions,
                        key=lambda x: float(x.get("unrealized_pl", 0) or 0),
                        reverse=True):
            sym     = p["symbol"]
            qty     = p["qty"]
            cur     = float(p.get("current_price",   0) or 0)
            pnl     = float(p.get("unrealized_pl",   0) or 0)
            avg     = float(p.get("avg_entry_price", 0) or 0)
            pnl_pct = ((cur / avg - 1) * 100) if avg > 0 else 0.0
            sign    = "+" if pnl >= 0 else ""
            lines.append(
                f"  `{sym}` {qty}주  {cur:,.0f}원  "
                f"{sign}{pnl_pct:.1f}% ({sign}{pnl:,.0f}원)"
            )
    else:
        lines.append("\n보유 포지션 없음")

    if perf and perf["n_days"] >= 2:
        lines.append(
            f"\n*누적 성과 ({perf['n_days']}일)*\n"
            f"  수익률 {perf['total_return']:+.1f}%  "
            f"샤프 {perf['sharpe']:.2f}  최대낙폭 {perf['max_dd']:.1f}%"
        )

    msg = "\n".join(lines)
    print(msg)
    send_tg(msg)


if __name__ == "__main__":
    main()
