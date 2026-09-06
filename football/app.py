"""첼시 프리뷰/리뷰 대시보드.

실행:  streamlit run football/app.py

여기는 배치만 한다 — HTML 은 view.py 가 만든다(이스케이프 포함).
라인업·부상·xG 자리는 비어 있다. API-Football 키가 오면 채운다.
"""
import streamlit as st

import epl
import view

PAST = ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]


def html(fragment):
    st.markdown(fragment, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Chelsea · EPL", page_icon="🔵", layout="wide")
    html(view.CSS)

    season = epl.load_season(epl.CURRENT)
    table = {r["team"]: r for r in epl.table(season)}
    me = epl.CHELSEA

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

        html(view.label("맞대결 상대"))
        left, right = st.columns(2)
        left.markdown(view.team_card(season, me, table.get(me)), unsafe_allow_html=True)
        right.markdown(view.team_card(season, opp, table.get(opp)), unsafe_allow_html=True)

        html(view.label(f"상대 전적 · 최근 {len(PAST)}시즌"))
        records = epl.h2h(epl.load_seasons(PAST), me, opp)
        html(view.h2h_card(records, epl.h2h_summary(records), opp, len(PAST)))

        html(view.label("라인업 · 부상"))
        html(view.pending_card(
            "API-Football 키를 넣으면 채워집니다. 라인업은 킥오프 40분 전에 확정됩니다."
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
