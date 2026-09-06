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


@st.cache_data(ttl=30, show_spinner=False)
def now_playing():
    """지금 뛰고 있는 경기와 그 라인업·평점. 없으면 (None, {}).

    캐시가 30초인 건 이것만이다 — 나머지는 경기 중에도 안 바뀐다.
    """
    try:
        m = fotmob.live_match()
        return (m, fotmob.lineup(m["id"])) if m else (None, {})
    except Exception:
        return None, {}


@st.fragment(run_every=30)
def live_block(me):
    """진행중일 때만 그리는 조각. 30초마다 이 부분만 다시 그린다."""
    m, lu = now_playing()
    if not m:
        return
    html(view.label("진행중"))
    html(view.live_card(m, me))
    html(view.label("라인업 · 실시간 평점"))
    html(view.lineup_table(lu, now=True))


@st.cache_data(ttl=600, show_spinner=False)
def results(league_id):
    """이번 시즌 결과 — FotMob. league_id=None 이면 컵·친선까지 전부.

    openfootball 은 결과를 주 1회(수요일)만 올리고, 리그만 준다.
    2026-09-06 에 3라운드 10경기가 통째로 비어 있었다 — 소스가 안 채운 것이다.
    """
    try:
        return fotmob.as_matches(league_id=league_id)
    except Exception:
        return None


@st.cache_data(ttl=600, show_spinner=False)
def fixtures(league_id):
    """예정 경기 — FotMob(컵 포함). 실패하면 None."""
    try:
        return fotmob.schedule(league_id=league_id)
    except Exception:
        return None


@st.cache_data(ttl=600, show_spinner=False)
def standings():
    try:
        return fotmob.table()
    except Exception:
        return None


@st.cache_data(ttl=600, show_spinner=False)
def lineup_of(match_id):
    """끝난 경기의 라인업 — 진행중이 아니므로 영구 캐시 쪽 ttl 을 쓴다."""
    try:
        return fotmob.lineup(match_id, ttl=fotmob.MATCH_TTL)
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def comps():
    """이번 시즌 뛰는 대회들 — 화면 위 선택지."""
    try:
        return fotmob.competitions()
    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def injured():
    try:
        return fotmob.injuries()
    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def ratings(league_id):
    """선수 평점 — 고른 대회의 이번 시즌 경기 전부. FotMob 은 비공식이라
    죽을 수 있고, 죽으면 이 구역만 비어야 한다."""
    try:
        return fotmob.season_rows(league_id=league_id)
    except Exception:
        return []


def html(fragment):
    st.markdown(fragment, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Chelsea · EPL", page_icon="🔵", layout="wide")
    html(view.CSS)

    season = epl.load_season(epl.CURRENT)          # 과거·백업용 리그 일정
    me = epl.CHELSEA

    # 대회 선택. FotMob 이 죽으면 선택지가 없고 리그만 남는다.
    available = comps()
    labels = ["전체"] + [c["name"] for c in available]
    ids = {c["name"]: c["id"] for c in available}
    default = next((c["name"] for c in available if c["id"] == fotmob.EPL_ID), "전체")
    choice = st.segmented_control("대회", labels, default=default) or default
    lid = ids.get(choice)                          # '전체' 는 None

    picked = results(lid)
    league = results(fotmob.EPL_ID) if lid != fotmob.EPL_ID else picked
    standings_rows = standings()
    if picked is None or league is None or standings_rows is None:
        # FotMob 이 막히면 낡아도 openfootball(리그만)
        picked = league = epl.played(season)
        standings_rows = epl.table(season)

    # 폼·홈원정·상대전적은 **리그 기준**이다 — 컵을 섞으면 상대와 비교가 안 된다.
    history = sorted(epl.load_seasons(PAST)
                     + [{**m, "season": epl.CURRENT} for m in league],
                     key=lambda m: (m["date"], m.get("time", "")))
    recent = [m for m in history if m["season"] in ALL_SEASONS[:VENUE_SEASONS]]
    current = [{**m, "season": epl.CURRENT} for m in picked]
    rank_of = {r["team"]: r for r in standings_rows}

    def card_for(name):
        return view.team_card(
            name,
            rank_of.get(name),
            epl.form(history, name, FORM_N),
            epl.venue_record(recent, name, home=True),
            epl.venue_record(recent, name, home=False),
        )

    html("<div class='blk'>")
    html(view.header(epl.CURRENT))

    live_block(me)

    # ── 다음 경기 ──
    later = fixtures(lid)
    if later is None:
        later = epl.upcoming(season, me)
    nxt = later[0] if later else None
    html(view.label(f"다음 경기 · {choice}"))
    if not nxt:
        html(view.plain_card("남은 경기가 없습니다."))
    else:
        html(view.next_match_card(nxt, me))
        opp = nxt["team2"] if nxt["team1"] == me else nxt["team1"]

        html(view.label(
            f"맞대결 상대 · 리그 기준(폼 최근 {FORM_N}경기, 홈원정 {VENUE_SEASONS}시즌)"
        ))
        left, right = st.columns(2)
        left.markdown(card_for(me), unsafe_allow_html=True)
        right.markdown(card_for(opp), unsafe_allow_html=True)

        html(view.label(f"상대 전적 · 리그 최근 {len(ALL_SEASONS)}시즌"))
        records = epl.h2h(history, me, opp)
        html(view.h2h_card(records, epl.h2h_summary(records), opp, len(ALL_SEASONS)))

        html(view.label(f"다음 5경기 · {choice}"))
        html(view.fixtures_table(later[:5], rank_of, me))

        html(view.label("결장 · 부상"))
        html(view.injury_card(injured()))
        html(view.pending_card(
            "경기 전 예상 라인업은 없습니다 — FotMob 이 경기 전에 주는 건 '지난 경기 선발'"
            "이지 예상이 아닙니다. 킥오프가 가까워지면 확정 라인업이 맨 위에 뜹니다."
        ))

    # ── 지난 경기 ──
    html(view.label(f"지난 경기 · {choice}"))
    prev = epl.last_match(current, me)
    html(view.last_match_card(prev, me) if prev
         else view.plain_card("이번 시즌 치른 경기가 없습니다."))

    # 리그일 때만 시즌 경계를 넘는다. 컵은 이번 시즌 것만 있으면 된다.
    pool = history if lid == fotmob.EPL_ID else current
    note = "시즌 경계를 넘습니다" if lid == fotmob.EPL_ID else choice
    html(view.label(f"최근 {RECENT_N}경기 · {note}"))
    rows = epl.form(pool, me, RECENT_N)
    html(view.results_table(rows, epl.h2h_summary(rows)))

    # ── 선수 평점 (FotMob) ──
    rows = ratings(lid)
    if prev and rows:
        last_id = max((r["match"] for r in rows), default=None)
        opp_name = next((r["opp"] for r in rows if r["match"] == last_id), "")
        html(view.label(f"지난 경기 라인업 · vs {opp_name}"))
        html(view.lineup_table(lineup_of(last_id)))

    html(view.label(f"시즌 평균 평점 · {choice} · 경기 평점의 단순 평균"))
    html(view.player_ratings_table(fotmob.average(rows)))

    # ── 순위표 ──
    playing, _ = now_playing()
    html(view.label("순위표 · 리그" + (" · 진행중 경기 잠정 반영" if playing else "")))
    html(view.standings_table(standings_rows, me))
    html(view.footer() + "</div>")


if __name__ == "__main__":
    main()
