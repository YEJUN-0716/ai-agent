"""
퀀트 시스템 시그널 무인 스캔 → 텔레그램 알림.
GitHub Actions 크론(매일 장마감 후)에서 실행되며, Tab6(퀀트)의
generate_system_signals()를 그대로 호출해 매수/매도 후보를 알려준다.
자동 주문은 하지 않음 — 알림을 보고 사용자가 직접 매매한다.
"""
import os
import sys

import app as core


def _resolve_universe(raw):
    if raw in core.UNIVERSE_PRESETS:
        return core.UNIVERSE_PRESETS[raw]
    return [t.strip().upper() for t in raw.split(',') if t.strip()]


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ('0', 'false', 'no')


def build_message(tickers, actions, rebal, failed):
    actionable = [a for a in actions if '관망' not in a['action'] and '대기' not in a['action']]

    lines = [
        f"🧬 *퀀트 시스템 시그널* ({len(tickers)}종목 스캔)",
        f"매수 {rebal['buy_count']} · 매도/축소 {rebal['sell_count']} · 관망 {rebal['hold_count']}",
        "",
    ]

    if not actionable:
        lines.append(f"오늘은 매수/매도 시그널 없음 (관망 {rebal['hold_count']}종목) — 자동 스캔은 정상 작동 중.")
    else:
        for a in sorted(actionable, key=lambda x: {'HIGH': 0, 'NORMAL': 1, 'LOW': 2}.get(x['priority'], 3)):
            lines.append(
                f"*{a['ticker']}* {a['action']} ({a['priority']})\n"
                f"  {a['price']} · {a['alloc']} · {a['qty']}\n"
                f"  {a['reason']}"
            )

    if failed:
        lines.append("")
        lines.append(f"⚠️ 분석 실패 {len(failed)}종목: {', '.join(failed)}")

    return "\n".join(lines)


def main():
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    universe_raw = os.environ.get('UNIVERSE', 'S&P 500 대형 30')
    top_n = int(os.environ.get('TOP_N', '5'))
    capital = float(os.environ.get('CAPITAL', '10000'))
    sector_neutral = _env_bool('SECTOR_NEUTRAL', True)
    factor_timing = _env_bool('FACTOR_TIMING', True)

    tickers = _resolve_universe(universe_raw)
    print(f"유니버스: {universe_raw} ({len(tickers)}종목)")

    factor_weights = None
    if factor_timing:
        factor_weights, env = core.get_factor_timing_weights()
        print(f"팩터 타이밍: {env['regime']} / VIX {env['vix']} / 가중치 {factor_weights}")

    if sector_neutral:
        fdf = core.calc_factor_scores_sectoral(tickers, factor_weights=factor_weights)
    else:
        fdf = core.calc_factor_scores(tickers)

    failed = list(fdf.attrs.get('failed', [])) if fdf is not None else []

    if fdf is None or fdf.empty:
        core.send_telegram(token, chat_id,
            f"🧬 *퀀트 시스템 시그널*\n\n오늘은 분석 가능한 종목이 없습니다 "
            f"({len(tickers)}종목 시도, {len(failed)}종목 실패).")
        print("팩터 분석 결과 없음 — 알림만 발송하고 종료.")
        return

    actions, rebal = core.generate_system_signals(
        tickers, factor_df=fdf, top_n=top_n, capital=capital)

    msg = build_message(tickers, actions, rebal, failed)
    ok, err = core.send_telegram(token, chat_id, msg)
    print(f"텔레그램 발송: {'성공' if ok else f'실패 ({err})'}")
    print(msg)


if __name__ == "__main__":
    main()
