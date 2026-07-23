"""
퀀트 시스템 시그널 무인 스캔 → 텔레그램 알림.
GitHub Actions 크론(매일 장마감 후)에서 실행되며, Tab6(퀀트)의
generate_system_signals()를 그대로 호출해 매수/매도 후보를 알려준다.
자동 주문은 하지 않음 — 알림을 보고 사용자가 직접 매매한다.

매수 시그널은 signal_log.json에 기록되어 21일 후 수익률을 자동 추적한다.
"""
import json
import os
import sys
from datetime import date

import app as core
from modules import krx_universe


KRX_SUFFIXES = ('.KS', '.KQ')


def _resolve_universe(raw):
    """UNIVERSE 환경변수 → 티커 목록.

    해석 순서: 고정 프리셋 → 동적 KRX 유니버스("KOSPI 100") → 쉼표구분 티커.
    KRX 쪽을 프리셋 dict 에 넣지 않는 이유는 목록이 매일 달라지기 때문이다.
    """
    if raw in core.UNIVERSE_PRESETS:
        return core.UNIVERSE_PRESETS[raw]
    try:
        dynamic = krx_universe.resolve(raw)
    except Exception as exc:
        # 상장목록 조회 실패를 쉼표구분 티커로 잘못 해석하면, "KOSPI 100" 이
        # 티커 하나짜리 유니버스가 돼 조용히 빈 스캔이 된다.
        print(f"KRX 상장목록 조회 실패 ({raw}): {exc}", file=sys.stderr)
        dynamic = None
    if dynamic:
        return dynamic
    return [t.strip().upper() for t in raw.split(',') if t.strip()]


def krx_tickers(tickers):
    return [t for t in tickers if t.endswith(KRX_SUFFIXES)]


def krx_data_warning(tickers, dart_key=None):
    """KRX 종목이 있는데 DART 키가 없으면 경고 문구, 아니면 None.

    yfinance 는 KRX 종목의 ROE·이익률을 대체로 비워서 보낸다. DART 폴백이
    없으면 퀄리티 원점수가 전 종목 동일값(ROE 0 · 이익률 0 · 발생액 중립)으로
    주저앉고, Z-score 정규화가 그걸 전부 50점으로 만든다 — 4팩터 중 하나가
    통째로 사라진다.

    그런데 가격은 정상적으로 받아지므로 **실패 종목 수는 0** 이다. 알림만
    보면 스캔이 멀쩡해 보인다. 그래서 조용히 넘기지 않고 알림에 싣는다.
    """
    krx = krx_tickers(tickers)
    if not krx:
        return None
    key = os.environ.get('DART_API_KEY', '') if dart_key is None else dart_key
    if key:
        return None
    return (f"⚠️ DART_API_KEY 미설정 — KRX {len(krx)}종목의 ROE·이익률을 "
            f"가져올 수 없어 퀄리티 팩터가 무의미해집니다. 랭킹을 신뢰하지 마세요.")


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ('0', 'false', 'no')


def build_message(tickers, actions, rebal, failed, warning=None):
    actionable = [a for a in actions if '관망' not in a['action'] and '대기' not in a['action']]

    lines = [
        f"🧬 *퀀트 시스템 시그널* ({len(tickers)}종목 스캔)",
        f"매수 {rebal['buy_count']} · 매도/축소 {rebal['sell_count']} · 관망 {rebal['hold_count']}",
        "",
    ]

    # 데이터 품질 경고는 맨 위에 — 아래 랭킹을 믿을지 말지가 먼저다.
    if warning:
        lines.append(warning)
        lines.append("")

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
    capital = float(os.environ.get('CAPITAL', '100000'))
    sector_neutral = _env_bool('SECTOR_NEUTRAL', True)
    factor_timing = _env_bool('FACTOR_TIMING', True)

    tickers = _resolve_universe(universe_raw)
    print(f"유니버스: {universe_raw} ({len(tickers)}종목)")

    data_warning = krx_data_warning(tickers)
    if data_warning:
        print(data_warning, file=sys.stderr)

    factor_weights = None
    if factor_timing:
        factor_weights, env = core.get_factor_timing_weights()
        print(f"팩터 타이밍: {env['regime']} / VIX {env['vix']} / 가중치 {factor_weights}")

    if sector_neutral:
        fdf = core.calc_factor_scores_sectoral(tickers, factor_weights=factor_weights)
    else:
        fdf = core.calc_factor_scores(tickers, factor_weights=factor_weights)

    failed = list(fdf.attrs.get('failed', [])) if fdf is not None else []

    if fdf is None or fdf.empty:
        core.send_telegram(token, chat_id,
            f"🧬 *퀀트 시스템 시그널*\n\n오늘은 분석 가능한 종목이 없습니다 "
            f"({len(tickers)}종목 시도, {len(failed)}종목 실패).")
        print("팩터 분석 결과 없음 — 알림만 발송하고 종료.")
        return

    actions, rebal = core.generate_system_signals(
        tickers, factor_df=fdf, top_n=top_n, capital=capital)

    msg = build_message(tickers, actions, rebal, failed, warning=data_warning)
    ok, err = core.send_telegram(token, chat_id, msg)
    print(f"텔레그램 발송: {'성공' if ok else f'실패 ({err})'}")
    print(msg)

    # ── 매수 시그널 → signal_log.json 저장 ──────────────────────────
    save_signal_log(actions)


def save_signal_log(actions):
    """매수 시그널을 signal_log.json에 기록. 당일 중복은 건너뜀."""
    buy_actions = [a for a in actions if '매수' in a['action']]
    if not buy_actions:
        print("매수 시그널 없음 — signal_log.json 업데이트 생략.")
        return

    log_path = os.path.join(os.path.dirname(__file__), "signal_log.json")
    existing: dict = {}
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding='utf-8') as f:
                existing = json.load(f)
        except Exception as e:
            print(f"signal_log.json 로드 오류 (초기화): {e}")

    signals = existing.get('signals', [])
    today = date.today().isoformat()
    existing_keys = {(s.get('symbol'), s.get('entry_date')) for s in signals}
    added = 0

    for a in buy_actions:
        raw_price = a['price'].replace('$', '').replace('₩', '').replace(',', '')
        try:
            raw_price = float(raw_price)
        except ValueError:
            raw_price = 0.0
        if (a['ticker'], today) not in existing_keys:
            signals.append({
                'symbol':      a['ticker'],
                'entry_date':  today,
                'entry_price': raw_price,
                'action':      a['action'],
                'reason':      a['reason'],
                'source':      'github_actions',
                'return_pct':  None,
            })
            existing_keys.add((a['ticker'], today))
            added += 1

    if added:
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump({'signals': signals}, f, ensure_ascii=False, indent=2)
        print(f"signal_log.json: {added}건 신규 기록 (누적 {len(signals)}건)")
    else:
        print("signal_log.json: 당일 신규 시그널 없음 (중복 스킵).")


if __name__ == "__main__":
    main()
