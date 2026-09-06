"""첼시 프리뷰/리뷰 대시보드.

실행:  streamlit run football/app.py

여기는 배치와 "어느 범위의 데이터를 쓸지"만 정한다 —
계산은 epl.py, HTML 은 view.py(이스케이프 포함).
라인업·부상은 API-Football 유료 플랜이라야 이번 시즌을 준다. 지금은 자리만.
"""
import streamlit as st

import epl
import fotmob
import view

PAST = ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]
ALL_SEASONS = [epl.CURRENT] + PAST

# 폼 배지에 쓸 경기 수. 시즌 초에는 이번 시즌만으로 폼이 2~3경기밖에 안 되므로
# 시즌 경계를 넘어서 센다.
FORM_N = 10
# 홈/원정 성적의 범위. 6시즌을 다 쓰면 표본은 크지만 지금 팀과 무관한 과거가 섞인다.
VENUE_SEASONS = 2
# 리뷰에 펼칠 최근 결과 수. 폼 배지와 같은 이유로 시즌 경계를 넘는다.
RECENT_N = 5


@st.cache_data(ttl=600, show_spinner=False)
def live():
    """이번 시즌 결과와 순위표 — FotMob.

    openfootball 은 결과를 주 1회(수요일)만 올린다. 2026-09-06 에 3라운드
    10경기가 통째로 비어 있었다 — 캐시가 아니라 소스가 안 채운 것이다.
    그래서 **결과·순위표만** FotMob 으로 받는다. 일정과 과거 시즌은
    openfootball 이 계속 준다(FotMob 은 이번 시즌·이 팀만 준다).
    """
    try:
        return fotmob.as_matches(), fotmob.table()
    except Exception:
        return None, None


@st.cache_data(ttl=600, show_spinner=False)
def injured():
    try:
        return fotmob.injuries()
    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def ratings():
    """선수 평점 — 이번 시즌 EPL 경기 전부. FotMob 은 비공식이라 죽을 수 있고,
    죽으면 이 구역만 비어야 한다(순위·일정은 openfootball 이라 멀쩡하다)."""
    try:
        return fotmob.season_rows()
    except Exception:
        return []


def html(fragment):
    st.markdown(fragment, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Chelsea · EPL", page_icon="🔵", layout="wide")
    html(view.CSS)

    season = epl.load_season(epl.CURRENT)          # 일정(전체 380경기)
    results, standings_rows = live()
    if results is None:                            # FotMob 이 막히면 낡아도 openfootball
        results, standings_rows = epl.played(season), epl.table(season)
    current = [{**m, "season": epl.CURRENT} for m in results]
    history = sorted(epl.load_seasons(PAST) + current,
                     key=lambda m: (m["date"], m.get("time", "")))
    recent = [m for m in history if m["season"] in ALL_SEASONS[:VENUE_SEASONS]]
    standings = {r["team"]: r for r in standings_rows}
    me = epl.CHELSEA

    def card_for(name):
        return view.team_card(
            name,
            standings.get(name),
            epl.form(history, name, FORM_N),
            epl.venue_record(recent, name, home=True),
            epl.venue_record(recent, name, home=False),
        )

    html("<div class='blk'>")
    html(view.header(epl.CURRENT))

    # ── 다음 경기 ──
    nxt = epl.next_match(season, me)
    html(view.label("다음 경기"))
    if not nxt:
        html(view.plain_card("남은 경기가 없습니다."))
    else:
        html(view.next_match_card(nxt, me))
        opp = nxt["team2"] if nxt["team1"] == me else nxt["team1"]

        html(view.label(
            f"맞대결 상대 · 폼은 최근 {FORM_N}경기, 홈원정은 {VENUE_SEASONS}시즌"
        ))
        left, right = st.columns(2)
        left.markdown(card_for(me), unsafe_allow_html=True)
        right.markdown(card_for(opp), unsafe_allow_html=True)

        html(view.label(f"상대 전적 · 최근 {len(ALL_SEASONS)}시즌"))
        records = epl.h2h(history, me, opp)
        html(view.h2h_card(records, epl.h2h_summary(records), opp, len(ALL_SEASONS)))

        html(view.label("다음 5경기"))
        html(view.fixtures_table(epl.upcoming(season, me, 5), standings, me))

        html(view.label("결장 · 부상"))
        html(view.injury_card(injured()))
        html(view.pending_card(
            "선발 라인업은 아직 없습니다 — 부상은 FotMob 이 주지만 예상 라인업은 안 줍니다."
        ))

    # ── 지난 경기 ──
    html(view.label("지난 경기"))
    prev = epl.last_match(current, me)
    html(view.last_match_card(prev, me) if prev
         else view.plain_card("이번 시즌 치른 경기가 없습니다."))

    html(view.label(f"최근 {RECENT_N}경기 · 시즌 경계를 넘습니다"))
    results = epl.form(history, me, RECENT_N)
    html(view.results_table(results, epl.h2h_summary(results)))

    # ── 선수 평점 (FotMob) ──
    rows = ratings()
    if prev and rows:
        last_id = max((r["match"] for r in rows), default=None)
        last_rows = [r for r in rows if r["match"] == last_id]
        opp_name = last_rows[0]["opp"] if last_rows else ""
        html(view.label(f"지난 경기 선수 평점 · vs {opp_name}"))
        html(view.match_ratings_table(sorted(last_rows, key=lambda r: -r["rating"])))

    html(view.label("시즌 평균 평점 · 경기 평점의 단순 평균"))
    html(view.player_ratings_table(fotmob.average(rows)))

    # ── 순위표 ──
    html(view.label("순위표"))
    html(view.standings_table(standings_rows, me))
    html(view.footer() + "</div>")


if __name__ == "__main__":
    main()
