"""
일별 P&L 리포트
=================================================================
GitHub Actions daily-report.yml의 toss-report 잡에서 실행.
equity_log.json(paper_trade_runner_toss.py가 기록) + 현재 포지션.

러너와 같은 장부(virtual_portfolio.json)를 읽는다. 실주문 브로커는 붙이지
않는다 — 이유는 paper_trade_runner_toss.py 머리말 참조.

보고서가 러너와 다른 계좌를 읽으면 매일 아침 "보유 포지션 없음"만 온다.
실제로 그런 적이 있다(러너는 가상 장부, 보고서는 토스 계좌였다).

환경변수:
  TELEGRAM_TOKEN     (필수)
  TELEGRAM_CHAT_ID   (필수)
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import requests

from modules.fx import fetch_krw_per_usd

from modules import virtual_broker as broker

get_account, get_positions = broker.get_account, broker.get_positions

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
    if resp.status_code == 400 and "parse entities" in resp.text:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    print("[TG] 발송 성공" if resp.status_code == 200 else f"[TG 오류] {resp.text}")


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
    std_r     = float(np.std(port_rets, ddof=1))
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


def pending_buy_block(state: dict) -> list:
    """대기 중인 매수 주문 — 이미 현금을 붙잡고 있지만 아직 주식도 아닌 돈.

    매수여력(`available_krw`)은 이 금액을 이미 뺀 값이라, 대기분을 따로 적지 않으면
    "총자산은 그대로인데 매수여력만 줄어든" 것처럼 보인다. 실제로 예약이 현금을
    넘어선 적이 있어(2026-07-31) 이 숫자는 보고서에 드러나 있어야 한다.
    """
    buys = [o for o in state.get("pending", []) if o.get("side") == "buy"]
    if not buys:
        return []

    total = sum(float(o.get("notional_krw", 0.0)) for o in buys)
    lines = [f"\n*매수대기 {len(buys)}건* `{total:,.0f}원` (매수여력에서 이미 빠진 금액)"]
    for o in sorted(buys, key=lambda x: -float(x.get("notional_krw", 0.0))):
        qty   = o.get("qty")
        limit = o.get("limit_price")
        detail = f"{qty}주" if qty else "시가"
        if limit:
            detail += f" @ ${float(limit):,.2f}"
        lines.append(f"  `{o.get('symbol', '?')}` {detail}  "
                     f"{float(o.get('notional_krw', 0.0)):,.0f}원")
    return lines


def main():
    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mode_tag = "[가상장부]"
    print(f"P&L 리포트  {today}  {mode_tag}")

    # 가상 장부는 달러 보유를 원화로 환산해 평가액을 낸다. 환율을 주입하지
    # 않으면 모듈 기본값(1,400원)이 쓰여 보고서 총자산이 실제와 어긋난다.
    broker.set_fx(fetch_krw_per_usd(
        fallback=float(os.environ.get("KRW_PER_USD", "1400"))
    ))

    try:
        acct = get_account()
    except Exception as e:
        print(f"[오류] 계정 조회 실패: {e}"); sys.exit(1)

    equity       = float(acct.get("equity", 0))
    buying_power = float(acct.get("buying_power", 0))

    try:
        positions = get_positions()
    except Exception as e:
        positions = []
        print(f"[경고] 포지션 조회 실패: {e}")

    records = load_equity_log()
    perf    = calc_perf(records)

    lines = [
        f"*📊 일별 P&L 리포트* `{today}` {mode_tag}",
        f"총 자산 `{equity:,.0f}원`  매수여력 `{buying_power:,.0f}원`",
    ]

    if perf and perf.get("today_ret") is not None:
        emoji = "📈" if perf["today_ret"] >= 0 else "📉"
        lines.append(f"{emoji} 당일 *{perf['today_ret']:+.2f}%*")
    elif perf:
        lines.append("⚠️ 오늘 페이퍼트레이드 미실행 — 당일 수익률 없음")

    # 대기 주문은 장부를 직접 읽는다 — get_account()는 브로커 API 모양을 흉내내는
    # dict 라 예약분 항목이 없다(Alpaca 계정에도 그런 필드는 없다).
    try:
        state = broker.load_state()
        lines += pending_buy_block(state)
        # 장부 자기 점검. 이상이 없으면 아무 줄도 안 붙인다 — 매일 뜨는 "정상"은
        # 아무도 안 읽게 되고, 그러면 진짜 경보도 같이 안 읽힌다.
        problems = broker.check_state(state)
        if problems:
            lines.append(f"\n⚠️ *장부 점검 이상 {len(problems)}건*")
            lines += [f"  · {p}" for p in problems[:5]]
            if len(problems) > 5:
                lines.append(f"  · 외 {len(problems) - 5}건 — `python -m modules.virtual_broker selftest`")
    except Exception as e:
        print(f"[경고] 장부 조회 실패 — 대기·점검 표시 생략: {e}")

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
            # 시세를 못 받으면 현재가가 0 으로 온다. 그대로 계산하면 -100%
            # 라는 없는 손실이 찍힌다. 모르는 값은 모른다고 쓴다.
            if cur <= 0:
                lines.append(f"  `{sym}` {qty}주  시세 조회 실패")
                continue
            pnl_pct = ((cur / avg - 1) * 100) if avg > 0 else 0.0
            sign    = "+" if pnl >= 0 else ""
            is_us   = not sym.isdigit()   # 6자리 숫자=KRX, 그 외=US
            if is_us:
                cur_str = f"${cur:,.2f}"
                pnl_str = f"${pnl:,.2f}"
            else:
                cur_str = f"{cur:,.0f}원"
                pnl_str = f"{pnl:,.0f}원"
            lines.append(
                f"  `{sym}` {qty}주  {cur_str}  "
                f"{sign}{pnl_pct:.1f}% ({sign}{pnl_str})"
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
