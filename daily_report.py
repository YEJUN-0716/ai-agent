"""
Alpaca 일별 포지션 리포트
=================================================================
GitHub Actions daily-report.yml의 alpaca-report 잡에서 ubuntu-latest로 실행.
Alpaca 페이퍼 트레이딩은 현재 중단 상태 — 계좌 스냅샷만 발송.

환경변수:
  ALPACA_API_KEY    (필수)
  ALPACA_SECRET_KEY (필수)
  ALPACA_MODE       paper(기본) / live
  TELEGRAM_TOKEN    (필수)
  TELEGRAM_CHAT_ID  (필수)
"""
import os
import sys
from datetime import datetime, timezone

import requests

_ALPACA_MODE = os.environ.get("ALPACA_MODE", "paper").strip().lower()
_ALPACA_BASE = ("https://api.alpaca.markets" if _ALPACA_MODE == "live"
                else "https://paper-api.alpaca.markets")

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")


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


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Alpaca 포지션 리포트  {today}")

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

    mode_tag = "[LIVE]" if _ALPACA_MODE == "live" else "[PAPER]"
    lines = [
        f"*📋 Alpaca 포지션 리포트* `{today}` {mode_tag}",
        f"총 자산 `${equity:,.2f}`  현금 `${cash:,.2f}`",
        "_※ Alpaca 페이퍼 트레이딩 중단 중 — 잔여 포지션 현황_",
    ]

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

    msg = "\n".join(lines)
    print(msg)
    send_tg(msg)


if __name__ == "__main__":
    main()
