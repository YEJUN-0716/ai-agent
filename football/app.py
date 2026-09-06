"""첼시 프리뷰/리뷰 대시보드.

실행:  streamlit run football/app.py

여기는 배치와 "어느 범위의 데이터를 쓸지"만 정한다 —
계산은 epl.py, HTML 은 view.py(이스케이프 포함).
라인업·부상은 API-Football 유료 플랜이라야 이번 시즌을 준다. 지금은 자리만.
"""
import streamlit as st

import epl
import view

PAST = ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]
ALL_SEASONS = [epl.CURRENT] + PAST

# 폼 배지에 쓸 경기 수. 시즌 초에는 이번 시즌만으로 폼이 2~3경기밖에 안 되므로
# 시즌 경계를 넘어서 센다.
FORM_N = 10
# 홈/원정 성적의 범위. 6시즌을 다 쓰면 표본은 크지만 지금 팀과 무관한 과거가 섞인다.
VENUE_SEASONS = 2


def html(fragment):
    st.markdown(fragment, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Chelsea · EPL", page_icon="🔵", layout="wide")
    html(view.CSS)

    season = epl.load_season(epl.CURRENT)
    history = epl.load_seasons(ALL_SEASONS)
    recent = [m for m in history if m["season"] in ALL_SEASONS[:VENUE_SEASONS]]
    standings = {r["team"]: r for r in epl.table(season)}
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

        html(view.label("라인업 · 부상"))
        html(view.pending_card(
            "API-Football 무료 플랜은 2022~2024 시즌만 줍니다. "
            "이번 시즌 라인업·부상을 보려면 Pro($19/월)가 필요합니다."
        ))

    # ── 지난 경기 ──
    html(view.label("지난 경기"))
    prev = epl.last_match(season, me)
    html(view.last_match_card(prev, me) if prev
         else view.plain_card("아직 치른 경기가 없습니다."))

    # ── 순위표 ──
    html(view.label("순위표"))
    html(view.standings_table(epl.table(season), me))
    html(view.footer() + "</div>")


if __name__ == "__main__":
    main()
