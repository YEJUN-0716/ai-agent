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
from datetime import date, datetime

import app as core
from modules import (analyst_log, analyst_scorecard, analyst_team,
                     price_panel, scalp_log)
from modules.trade_plan import MEASURED_EDGE_NOTE


def _resolve_universe(raw):
    """UNIVERSE 환경변수 → 티커 목록. 고정 프리셋 → 쉼표구분 티커."""
    if raw in core.UNIVERSE_PRESETS:
        return core.UNIVERSE_PRESETS[raw]
    return [t.strip().upper() for t in raw.split(',') if t.strip()]


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ('0', 'false', 'no')


def _plan_fmt_price(value):
    """플랜 라인용 가격 포맷 — $ 소수 2자리."""
    return f"${value:.2f}"


def _plan_line(ticker, plan):
    """유효 플랜 → '진입 X~Y · 손절 Z · 목표 W (R:R)' 한 줄. 없으면 None."""
    if not plan or not plan.get('valid') or not plan.get('targets'):
        return None
    e = plan['entry']
    rr = plan['rr'][0]
    rr_s = f" (R:R {rr:.1f})" if rr else ""
    return (f"진입 {_plan_fmt_price(e['low'])}~{_plan_fmt_price(e['high'])}"
            f" · 손절 {_plan_fmt_price(plan['stop'])}"
            f" · 목표 {_plan_fmt_price(plan['targets'][0])}{rr_s}")


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

    plan_lines_sent = False
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
            # actionable 을 본다 — valid 는 "기하가 성립하나", 이쪽은 "걸
            # 만한가". 비용에 먹히는 등급(C·D)은 매수 알림에 라인을 안 붙인다.
            if '매수' in a['action'] and p and p.get('actionable'):
                pl = _plan_line(a['ticker'], p)
                if pl:
                    block += f"\n  📐 {pl}"
                    plan_lines_sent = True
            lines.append(block)

    # 라인을 하나라도 내보냈으면 그 라인이 무엇을 약속하는지 같이 적는다.
    # 2026-08-12 측정 전에는 이 자리가 "+0.66R 규칙" 이라는 뜻이었는데,
    # 실제로 걸 수 있는 진입으로 재면 +0.02R 이다. 알림이 그걸 말해야 한다.
    if plan_lines_sent:
        lines.append("")
        lines.append(f"_📐 라인 기준: {MEASURED_EDGE_NOTE}_")

    # 숏 관찰 — 신호 엔진은 롱 전용이라 숏은 여기서만 '분석용'으로 표시한다.
    shorts = [(tk, p) for tk, p in plans.items()
              if p.get('valid') and p.get('direction') == 'short']
    if shorts:
        lines.append("")
        lines.append("🔻 *숏 관찰* (분석용 · 자동주문 아님)")
        for tk, p in sorted(shorts, key=lambda x: -abs(x[1].get('bias_score', 0)))[:5]:
            lines.append(f"*{tk}* 숏 (실행등급 {p['cost_grade']} · 손절 {p['risk_pct']:.1f}%)\n  {_plan_line(tk, p)}")

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

    msg = build_message(tickers, actions, rebal, failed, plans=plans)
    ok, err = core.send_telegram(token, chat_id, msg)
    print(f"텔레그램 발송: {'성공' if ok else f'실패 ({err})'}")
    print(msg)

    # ── 애널리스트 점수 기록 (성적표 재료) ──────────────────────────
    # 위 regime 은 텔레그램에 찍는 표시용 문장이라 기록에 쓰지 않는다.
    record_analyst_scores(tickers, market_regime_slug(tickers))

    # ── 매수 시그널 → signal_log.json 저장 ──────────────────────────
    save_signal_log(actions)

    # ── 오늘의 액션 전체 → latest_signals.json (대시보드 [오늘] 화면) ──
    # 실패해도 워커 본 작업(통보·기록)을 막지 않는다.
    try:
        save_latest_signals(actions, rebal, universe_raw, capital)
        print(f"latest_signals.json: 액션 {len(actions)}건 기록.")
    except OSError as e:
        print(f"[경고] latest_signals.json 기록 실패: {e}", file=sys.stderr)


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
        regime, _ = core.get_market_regime()
        return regime
    except Exception as e:
        print(f"[경고] 국면 판정 실패 — neutral 로 기록: {e}")
        return 'neutral'


# 종목별 yfinance 재무 조회 사이의 간격. calc_factor_scores 가 쓰는 값과 같다 —
# 여기만 빠르게 돌면 야후가 유니버스 후반을 통째로 막는다.
QUANT_FETCH_SLEEP_SEC = 0.3

# 이 비율 밑으로 퀀트를 받으면 성적표가 사실상 안 쌓인다. 잡은 실패시키지
# 않지만(차트·ICT 기록은 살아 있다) 로그에서 눈에 띄어야 한다.
QUANT_COVERAGE_WARN_AT = 0.5


def _quant_score(ticker, df):
    """퀀트+재무 점수 — 못 받았으면 None.

    fundamental_score 는 실패해도 50.0 을 돌려준다. 화면은 사유를 함께 띄우므로
    그걸로 충분하지만 기록은 다르다 — 50 을 적으면 '재무를 못 받았다'가
    '중립 판단'으로 성적에 섞인다. analyst_log 가 값 없는 슬러그의 키를 아예
    빼는 것과 같은 규칙이라, 여기서도 키를 뺄 수 있게 None 으로 바꾼다.
    """
    try:
        score, det = core.fundamental_score(ticker, df)
    except Exception:
        return None
    if det.get('데이터없음') or det.get('오류'):
        return None
    return score


def _directional_weights():
    """화면이 쓰는 것과 같은 방향성 3인 가중치. 못 읽으면 빈 dict."""
    try:
        return core.analyst_weights_by_slug()
    except Exception as e:
        print(f"[경고] 애널리스트 가중치 조회 실패 — 동일가중으로 기록: {e}")
        return {}


def session_date(frames):
    """기록에 찍을 날짜 — 러너의 달력이 아니라 **받은 마지막 봉의 날짜**.

    datetime.now() 를 쓰면 러너의 UTC 벽시계가 찍힌다. 크론은 23:00 UTC 지만
    GitHub 은 이걸 최대 한 시간 넘게 밀어 실제 실행은 23:57~01:41 UTC 사이에
    흩어진다 — 자정을 넘긴 날은 그 장의 기록이 **다음 날짜**로 저장된다.
    실제로 2026-07-25·08-01 (둘 다 토요일) 이 기록에 남았고, 두 장이 같은
    날짜로 찍혀 append_day 가 앞의 것을 대체하는 바람에 07-27·08-05 장은
    통째로 사라졌다.

    봉의 날짜는 러너가 언제 도는지와 무관하다 — 점수를 계산한 바로 그 봉이
    자기 날짜를 들고 있으므로, 채점도 같은 봉에서 시작한다.
    """
    stamps = [df.index[-1] for df in frames
              if df is not None and len(df) > 0]
    return max(stamps).strftime("%Y-%m-%d") if stamps else None


def todays_quant(date_str, root=None):
    """오늘 일봉 기록에 남은 퀀트 점수 — {티커: 점수}.

    재무제표는 봉과 무관하므로 15분봉 판정도 같은 퀀트 점수를 쓴다 — 화면도
    그렇다(app.build_scalp_verdict 가 일봉 퀀트 보고서를 그대로 받는다).
    앞 스텝이 방금 쓴 파일을 읽어서 500종목 .info 조회를 두 번 하지 않는다.
    """
    root = root or analyst_log.LOG_DIRNAME
    for day in analyst_log.load_days(root=root, since=date_str):
        if day.get("date") == date_str:
            return {t: row["quant"] for t, row in day.get("scores", {}).items()
                    if row.get("quant") is not None}
    return {}


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

    quant(퀀트+재무)는 종목별 yfinance .info 가 필요해 유니버스 전체를 도는
    유일한 무거운 부분이다. 이 조회는 하루 한 번만 한다 — 15분봉 기록은 여기서
    남긴 값을 그대로 읽어 쓴다(todays_quant).

    verdict 는 3인의 IC가중 블렌드, 곧 **그 시점 화면 총괄 점수 그대로**다.
    재료를 나중에 다시 섞지 않는 이유는 가중치가 변하기 때문이다 —
    ic_weights.json 은 매주 갱신되고 국면별로도 다르다.
    """
    import time
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

    weights = _directional_weights()
    scores = {}
    quant_n = 0
    scored = [(t, df) for t, df in (ohlcv or {}).items()
              if df is not None and len(df) >= ANALYST_MIN_BARS]

    for i, (ticker, df) in enumerate(scored):
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

        quant = _quant_score(ticker, df)
        if quant is not None:
            row["quant"] = quant
            quant_n += 1
        if i < len(scored) - 1:
            time.sleep(QUANT_FETCH_SLEEP_SEC)

        verdict = analyst_team.verdict_score(row, weights)
        if verdict is not None:
            row[analyst_team.VERDICT_SLUG] = verdict

        if row:
            scores[ticker] = row

    if not scores:
        print("[경고] 애널리스트 점수를 낸 종목이 없다 — 기록 생략.")
        return 0

    # 퀀트가 통째로 비어도 잡을 실패시키지 않는다 — 차트·ICT 기록은 살아 있고,
    # 여기서 죽이면 그것까지 같이 버려진다. 대신 조용히 넘어가지도 않는다.
    if quant_n < len(scores) * QUANT_COVERAGE_WARN_AT:
        print(f"[경고] 퀀트+재무를 받은 종목이 {quant_n}/{len(scores)}뿐이다 — "
              "야후 재무 조회가 막힌 것으로 보인다. 오늘 총괄 판정 성적은 "
              "표본이 거의 안 늘어난다.")

    stamp = session_date(df for _, df in scored)
    if stamp is None:
        print("[경고] 봉에서 날짜를 못 읽었다 — 기록 생략.")
        return 0

    try:
        if not analyst_log.append_day(stamp, regime, scores):
            print(f"[경고] {stamp} 에 이미 더 많은 종목의 기록이 있다 — "
                  f"이번 {len(scores)}종목은 버린다(기존 기록을 지키는 쪽).")
            return 0
    except Exception as e:
        print(f"[경고] 애널리스트 기록 실패 (스캔은 계속): {e}")
        return 0

    verdict_n = sum(1 for row in scores.values()
                    if analyst_team.VERDICT_SLUG in row)
    print(f"애널리스트 기록: {stamp} · {len(scores)}종목 ({regime}) · "
          f"퀀트 {quant_n}종목 · 총괄 판정 {verdict_n}종목")
    return len(scores)


# ── 스캘핑(15분봉) 기록 ─────────────────────────────────────────────
#
# 일봉과 같은 유니버스를 쓴다 — 다른 종목으로 재면 두 성적표를 나란히 놓고
# 비교할 수 없다.
SCALP_LOG_UNIVERSE = ANALYST_LOG_UNIVERSE

# 1주(5거래일) 모멘텀 창을 채우는 최소 봉 수. 이보다 짧으면
# calc_momentum_intraday 가 창 대부분을 None 으로 흘려 점수가 50 에 붙는다.
SCALP_MIN_BARS = core.BARS_PER_DAY_15M * 5


def record_scalp_main():
    """15분봉 점수를 기록하고, 지난 기록의 선행수익률을 채점한다.

    한 번 받은 분봉 패널로 두 가지를 한다 — 오늘치 점수 기록과, 아직 채점
    안 된 지난 기록의 선행수익률. 나눠 받으면 500종목 다운로드가 두 배다.

    채점을 기록과 같은 잡에 두는 이유는 시한 때문이다. 15분봉은 60일이
    지나면 사라지므로, 그때까지 계산해 두지 않은 기록은 영영 채점할 수
    없다 — 일봉처럼 "나중에 가격에서 다시 계산" 이 안 된다.

    0종목 기록은 성공이 아니다 (record_only_main 과 같은 규칙).
    """
    universe_raw = os.environ.get('UNIVERSE', SCALP_LOG_UNIVERSE)
    tickers = _resolve_universe(universe_raw)
    if not tickers:
        print(f"유니버스가 비었다: {universe_raw}", file=sys.stderr)
        return 1

    try:
        prices, ohlcv = price_panel.load_intraday(
            tickers, min_bars=SCALP_MIN_BARS)
    except Exception as e:
        print(f"15분봉 패널 로드 실패 — 기록·채점 모두 불가: {e}", file=sys.stderr)
        return 1

    if not ohlcv:
        print("15분봉을 받은 종목이 없다 — 기록·채점 불가.", file=sys.stderr)
        return 1

    regime = market_regime_slug(tickers)
    print(f"유니버스: {universe_raw} (요청 {len(tickers)}종목 / 수신 "
          f"{len(ohlcv)}종목) / 국면: {regime}")

    # 앞 스텝(일봉 기록)이 방금 남긴 퀀트를 그대로 쓴다. 재무제표는 봉과
    # 무관하고, 여기서 다시 조회하면 500종목 .info 가 하루 두 번이 된다.
    # 앞 스텝이 찍은 날짜와 같은 키로 읽어야 한다 — 둘 다 봉의 날짜를 쓴다.
    quant = todays_quant(session_date(ohlcv.values()))
    if not quant:
        print("[경고] 오늘 일봉 기록에 퀀트가 없다 — 15분봉 총괄 판정은 "
              "오늘 기록되지 않는다(차트·ICT 는 그대로 남는다).")

    recorded = record_scalp_scores(ohlcv, regime, quant_by_ticker=quant)

    # 채점은 오늘 기록이 0종목이어도 돌린다 — 어제까지의 기록은 오늘 받은
    # 가격으로만 채점할 수 있다.
    print(f"선행수익률 채점: {resolve_scalp_returns(prices)}건 (기록일×지평)")

    if not recorded:
        print("15분봉 점수를 낸 종목이 없다 — 스캘핑 성적표 재료가 하루 끊겼다.",
              file=sys.stderr)
        return 1

    return 0


def record_scalp_scores(ohlcv, regime, root=None, quant_by_ticker=None):
    """15분봉 점수를 data/scalp_log 에 남긴다 — 하루 한 줄.

    quant_by_ticker — 오늘 일봉 기록에서 읽은 퀀트 점수. 화면 SCALP 판정도
    일봉 퀀트 보고서를 그대로 받으므로(app.build_scalp_verdict) 같은 값이어야
    한다. 여기서 15분봉으로 재무를 다시 계산하는 것은 애초에 말이 안 된다.

    일봉 기록(record_analyst_scores)과 갈리는 곳은 모멘텀이다.
    calc_momentum 은 창을 봉 개수로 잡으므로(63봉=3개월) 15분봉에 그대로
    먹이면 **예외 없이** '3개월 모멘텀'이 이틀 반이 되고, 점수표가
    "3개월에 +30%면 90점" 이라 그 값은 언제나 50 근처에 붙는다 — 죽은
    숫자가 30% 가중치로 섞인다. calc_momentum_intraday 로 갈아끼운다.

    기준 봉 시각(asof)을 함께 남긴다. 하루에 봉이 26개라 날짜만으로는
    "몇 봉 뒤" 를 셀 기준이 없다.
    """
    try:
        from modules.ict_analysis import ict_factor_score, calc_ict_adjustment
    except Exception as e:
        print(f"[경고] ICT 모듈 없음 — 15분봉 기록 생략: {e}")
        return 0

    weights = _directional_weights()
    quant_by_ticker = quant_by_ticker or {}
    scores, asof = {}, None
    for ticker, df in (ohlcv or {}).items():
        if df is None or len(df) < SCALP_MIN_BARS:
            continue

        row = {}
        # 계산 실패는 키를 뺀다 — 중립값 50 으로 채우면 '계산 불가'가
        # '중립 판단'으로 성적에 섞인다.
        try:
            t_score, _ = core.technical_score(df)
            mom = core.calc_momentum_intraday(df) or {}
            row["chart"] = analyst_team.chart_score(
                t_score, mom.get("score", 50.0))
        except Exception:
            pass

        try:
            row["ict"] = analyst_team.ict_score(
                ict_factor_score(df), calc_ict_adjustment(df)["adjustment"])
        except Exception:
            pass

        if quant_by_ticker.get(ticker) is not None:
            row["quant"] = quant_by_ticker[ticker]

        verdict = analyst_team.verdict_score(row, weights)
        if verdict is not None:
            row[analyst_team.VERDICT_SLUG] = verdict

        if row:
            scores[ticker] = row
            last_bar = df.index[-1]
            if asof is None or last_bar > asof:
                asof = last_bar

    if not scores:
        print("[경고] 15분봉 점수를 낸 종목이 없다 — 기록 생략.")
        return 0

    try:
        # asof 는 이미 기준봉이다 — 날짜도 거기서 나와야 둘이 어긋나지 않는다.
        # 어긋난 실물이 있었다: date=2026-08-07 / asof=2026-08-06T19:45.
        if not analyst_log.append_day(
                asof.strftime("%Y-%m-%d"), regime, scores,
                root=root or scalp_log.SCORE_DIRNAME, asof=asof.isoformat()):
            print(f"[경고] {asof:%Y-%m-%d} 에 이미 더 많은 종목의 15분봉 "
                  f"기록이 있다 — 이번 {len(scores)}종목은 버린다.")
            return 0
    except Exception as e:
        print(f"[경고] 15분봉 기록 실패: {e}")
        return 0

    verdict_n = sum(1 for row in scores.values()
                    if analyst_team.VERDICT_SLUG in row)
    print(f"15분봉 기록: {len(scores)}종목 ({regime}) · 총괄 판정 {verdict_n}종목 "
          f"· 기준봉 {asof}")
    return len(scores)


def resolve_scalp_returns(prices, score_root=None, returns_root=None):
    """아직 채점 안 된 (기록일 × 지평)의 선행수익률을 남긴다. 남긴 건수 반환.

    이미 채점된 것은 다시 계산하지 않는다. 값이 바뀔 일이 없을뿐더러, 60일이
    지나 봉이 사라진 뒤 다시 계산하면 있던 기록을 빈 값으로 덮는다.

    선행 구간이 아직 안 지난 기록은 그냥 넘긴다 — 다음 실행에서 다시 본다.
    """
    score_root = score_root or scalp_log.SCORE_DIRNAME
    returns_root = returns_root or scalp_log.RETURNS_DIRNAME

    days = analyst_log.load_days(root=score_root)
    done = scalp_log.load_returns(returns_root)
    written = 0

    for day in days:
        asof = day.get("asof")
        date_str = day.get("date", "")
        recorded_tickers = day.get("scores", {})
        if not asof or not date_str or not recorded_tickers:
            continue      # 기준 봉을 모르면 몇 봉 뒤를 셀 수 없다

        for horizon in analyst_scorecard.SCALP_HORIZONS_BARS:
            # 완료 판정은 **종목 단위**다. 날짜 단위로 찍으면, 다운로드가 일부만
            # 성공한 날이 통째로 "채점 끝" 이 돼 못 받은 종목은 다음 실행에서
            # 데이터가 와도 영영 안 들어간다 — 한 번의 네트워크 사고가 성적표
            # 표본에 영구히 남는다.
            already = done.get(horizon, {}).get(date_str, {})
            fwd = analyst_scorecard.build_forward_returns(
                prices, [asof], horizon).get(asof) or {}
            fresh = {t: pct for t, pct in fwd.items()
                     if t in recorded_tickers and t not in already}
            if not fresh:
                continue  # 아직 미래가 안 왔거나, 받을 종목이 다 채워졌다
            # 이미 저장된 값이 이긴다. 같은 봉으로 계산한 값이라 바뀔 이유가
            # 없고, 나중에 덮어쓰면 채점 시점이 종목마다 달라진다.
            scalp_log.append_returns(date_str, horizon, {**already, **fresh},
                                     root=returns_root)
            written += 1

    return written


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


LATEST_SIGNALS_FILE = "latest_signals.json"


def save_latest_signals(actions, rebal, universe, capital, path=None):
    """배치가 만든 액션 목록을 통째로 남긴다 — 대시보드 [오늘] 화면이 읽는 유일한 원본.

    signal_log.json 은 *매수* 시그널만 성적 추적용으로 쌓는다. 홈 화면은 매도·축소까지
    포함한 오늘의 지시가 필요한데, 화면에서 generate_system_signals 를 다시 부르면
    유니버스 전체 팩터 스캔이 걸린다. 그래서 워커가 결과를 그대로 적고 화면은 읽기만 한다.

    actions 원소는 signal_engine._make_action 이 반환한 dict 그대로다 — 변환하지 않는다.
    """
    path = path or os.path.join(os.path.dirname(__file__), LATEST_SIGNALS_FILE)
    payload = {
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'universe': universe,
        'capital': capital,
        'rebal': rebal,
        'actions': actions,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


if __name__ == "__main__":
    main()
