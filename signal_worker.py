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
from modules import analyst_log, analyst_team, krx_universe, price_panel


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


def _plan_fmt_price(ticker, value):
    """플랜 라인용 가격 포맷 — KRX 는 ₩ 정수, 그 외 $ 소수 2자리."""
    if ticker.endswith(('.KS', '.KQ')):
        return f"₩{value:,.0f}"
    return f"${value:.2f}"


def _plan_line(ticker, plan):
    """유효 플랜 → '진입 X~Y · 손절 Z · 목표 W (R:R)' 한 줄. 없으면 None."""
    if not plan or not plan.get('valid') or not plan.get('targets'):
        return None
    e = plan['entry']
    rr = plan['rr'][0]
    rr_s = f" (R:R {rr:.1f})" if rr else ""
    return (f"진입 {_plan_fmt_price(ticker, e['low'])}~{_plan_fmt_price(ticker, e['high'])}"
            f" · 손절 {_plan_fmt_price(ticker, plan['stop'])}"
            f" · 목표 {_plan_fmt_price(ticker, plan['targets'][0])}{rr_s}")


def build_message(tickers, actions, rebal, failed, warning=None, plans=None):
    plans = plans or {}
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
            block = (
                f"*{a['ticker']}* {a['action']} ({a['priority']})\n"
                f"  {a['price']} · {a['alloc']} · {a['qty']}\n"
                f"  {a['reason']}"
            )
            # 매수 신호엔 롱 트레이드 플랜 라인을 붙인다 (있을 때만)
            p = plans.get(a['ticker'])
            if '매수' in a['action'] and p and p.get('direction') == 'long':
                pl = _plan_line(a['ticker'], p)
                if pl:
                    block += f"\n  📐 {pl}"
            lines.append(block)

    # 숏 관찰 — 신호 엔진은 롱 전용이라 숏은 여기서만 '분석용'으로 표시한다.
    shorts = [(tk, p) for tk, p in plans.items()
              if p.get('valid') and p.get('direction') == 'short']
    if shorts:
        lines.append("")
        lines.append("🔻 *숏 관찰* (분석용 · 자동주문 아님)")
        for tk, p in sorted(shorts, key=lambda x: -abs(x[1].get('bias_score', 0)))[:5]:
            lines.append(f"*{tk}* 숏 ({p['confidence']})\n  {_plan_line(tk, p)}")

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
        # env['regime'] 은 텔레그램에 찍는 표시용 문장이다. 기록에 넣을 국면은
        # market_regime_slug() 가 따로 낸다 — 아래 기록 호출 참고.
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

    # ── 트레이드 플랜(진입/손절/목표 라인) — 롱은 신호에 붙이고 숏은 관찰 목록 ──
    plans = {}
    try:
        from datetime import datetime, timedelta

        from modules.trade_plan import MIN_BARS, build_trade_plan
        _, _ohlcv = price_panel.load_panel(
            tickers, datetime.now() - timedelta(days=ANALYST_PANEL_DAYS), datetime.now())
        for _tk, _pdf in (_ohlcv or {}).items():
            if _pdf is not None and len(_pdf) >= MIN_BARS:
                plans[_tk] = build_trade_plan(_pdf)
    except Exception as e:
        print(f"[경고] 트레이드 플랜 생성 실패 — 라인 생략: {e}", file=sys.stderr)

    msg = build_message(tickers, actions, rebal, failed, warning=data_warning, plans=plans)
    ok, err = core.send_telegram(token, chat_id, msg)
    print(f"텔레그램 발송: {'성공' if ok else f'실패 ({err})'}")
    print(msg)

    # ── 애널리스트 점수 기록 (성적표 재료) ──────────────────────────
    # 위 regime 은 텔레그램에 찍는 표시용 문장이라 기록에 쓰지 않는다.
    record_analyst_scores(tickers, market_regime_slug(tickers))

    # ── 매수 시그널 → signal_log.json 저장 ──────────────────────────
    save_signal_log(actions)


ANALYST_PANEL_DAYS = 400        # 12M 모멘텀 + 기술지표 워밍업에 필요한 달력일
ANALYST_MIN_BARS = 60           # 이보다 짧으면 ICT·기술지표가 의미 없다

# 기록 전용 실행의 기본 유니버스. IC 재측정에 쓴 것과 같은 유니버스여야
# 성적표의 단면이 프로덕션 판단과 같은 폭을 갖는다. 30종목 단면으로 IC 를
# 내면 성적 자체가 잡음에 묻힌다.
ANALYST_LOG_UNIVERSE = 'S&P 500 전체 (500종목)'


def market_regime_slug(tickers):
    """기록에 남길 국면 — 'bull' / 'bear' / 'neutral'.

    팩터 타이밍이 내주는 regime 은 '저변동성 — 모멘텀 강조 + 금리하락(모멘텀↑)'
    같은 표시용 문장이다. 그걸 기록에 넣으면 ic_weights.json 의 국면 키와
    맞지 않아 나중에 국면별 성적을 갈라 볼 수 없고, 문구를 한 번만 손봐도
    과거 기록과 이어지지 않는다.

    국면을 못 재도 점수 기록 자체는 진행한다 — 국면은 부가 정보고, 그날의
    판단을 못 남기는 쪽이 훨씬 비싸다.
    """
    try:
        regime, _ = core.get_market_regime(core._scope.regime_benchmark(tickers))
        return regime
    except Exception as e:
        print(f"[경고] 국면 판정 실패 — neutral 로 기록: {e}")
        return 'neutral'


def record_only_main():
    """애널리스트 점수만 기록한다 — 텔레그램 발송도 시그널 로그도 없다.

    signal-alerts 의 크론이 꺼진 뒤(2026-07-20, 팩터에 측정 가능한 알파가
    없어 매수 알림 중단) 기록을 부르는 곳이 같이 사라졌다. 매수를 추천할지와
    "누가 잘 맞히나" 를 잴지는 별개의 결정이다 — 알림을 멈춘 동안에도 성적표의
    재료는 쌓여야 한다. 그래서 발송 없이 기록만 도는 경로를 따로 둔다.

    0종목 기록은 성공이 아니다. 조용히 넘어가면 몇 달 뒤 빈 성적표를 보고서야
    끊긴 걸 안다 — 이 기능이 실제로 겪은 고장이 그 형태였다.
    """
    universe_raw = os.environ.get('UNIVERSE', ANALYST_LOG_UNIVERSE)
    tickers = _resolve_universe(universe_raw)
    if not tickers:
        print(f"유니버스가 비었다: {universe_raw}", file=sys.stderr)
        return 1

    regime = market_regime_slug(tickers)
    print(f"유니버스: {universe_raw} ({len(tickers)}종목) / 국면: {regime}")

    if not record_analyst_scores(tickers, regime):
        print("기록된 종목이 없다 — 성적표 재료가 오늘 하루 끊겼다.", file=sys.stderr)
        return 1

    return 0


def record_analyst_scores(tickers, regime):
    """전 유니버스의 chart·ict 점수를 기록한다. 실패해도 스캔은 계속된다.

    성적표의 재료를 만드는 유일한 지점이다 — 여기서 안 남기면 "누가 잘
    맞히나" 를 영원히 알 수 없다. 그래서 조용히 죽지 않게 감싸 두고,
    실패하면 경고를 남긴다.

    quant(퀀트+재무)는 종목별 yfinance .info 가 필요해 Phase 1 에서 제외했다.
    스캔의 반환 계약을 정리한 뒤 별도로 붙인다 — 기록 포맷은 이미 수용한다.
    """
    from datetime import datetime, timedelta

    if not tickers:
        return 0

    try:
        from modules.ict_analysis import ict_factor_score, calc_ict_adjustment
    except Exception as e:
        print(f"[경고] ICT 모듈 없음 — 애널리스트 기록 생략: {e}")
        return 0

    try:
        end = datetime.now()
        _, ohlcv = price_panel.load_panel(
            tickers, end - timedelta(days=ANALYST_PANEL_DAYS), end)
    except Exception as e:
        print(f"[경고] 가격 패널 로드 실패 — 애널리스트 기록 생략: {e}")
        return 0

    scores = {}
    for ticker, df in (ohlcv or {}).items():
        if df is None or len(df) < ANALYST_MIN_BARS:
            continue

        row = {}
        # 계산 실패는 키를 뺀다 — 중립값 50 으로 채우면 '계산 불가'가
        # '중립 판단'으로 성적에 섞인다.
        try:
            t_score, _ = core.technical_score(df)
            mom = core.calc_momentum(df) or {}
            row["chart"] = analyst_team.chart_score(
                t_score, mom.get("score", 50.0))
        except Exception:
            pass

        try:
            row["ict"] = analyst_team.ict_score(
                ict_factor_score(df), calc_ict_adjustment(df)["adjustment"])
        except Exception:
            pass

        if row:
            scores[ticker] = row

    if not scores:
        print("[경고] 애널리스트 점수를 낸 종목이 없다 — 기록 생략.")
        return 0

    try:
        analyst_log.append_day(
            datetime.now().strftime("%Y-%m-%d"), regime, scores)
    except Exception as e:
        print(f"[경고] 애널리스트 기록 실패 (스캔은 계속): {e}")
        return 0

    print(f"애널리스트 기록: {len(scores)}종목 ({regime})")
    return len(scores)


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
