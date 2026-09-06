"""축구 대시보드 — 외부 JSON 이 HTML 로 새지 않는가.

팀명·라운드명은 openfootball 에서 온 문자열인데 화면은
`unsafe_allow_html=True` 로 그린다. 소스를 신뢰하더라도 그 사이에
이스케이프가 있어야 한다. 네트워크 없음(경기 데이터를 직접 만든다).

음성 검사만 두면 "빈 문자열을 돌려줘도 통과"하므로,
이스케이프된 형태가 실제로 들어 있는지도 같이 본다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "football"))
import epl  # noqa: E402
import view  # noqa: E402

# 태그 탈출 + 속성 탈출을 한 문자열에 담았다.
POISON = '<script>alert(1)</script>" onmouseover="alert(2)'
ESCAPED = "&lt;script&gt;"

RAW_MARKERS = ["<script", "</script>", '" onmouseover=', "onmouseover=\"alert"]


def _match(**kw):
    base = dict(round="Matchday 1", date="2026-01-03", time="15:00",
                team1=POISON, team2="Rival FC", score={"ht": [1, 0], "ft": [2, 1]})
    base.update(kw)
    return base


def _fragments():
    """조각 생성기를 오염된 데이터로 전부 한 번씩 돌린다."""
    done = [_match()]
    upcoming = _match(score=None)
    standing = epl.standing(done, POISON)
    records = epl.h2h([{**_match(), "season": POISON}], POISON, "Rival FC")

    return {
        "team": view.team(POISON),
        "label": view.label(POISON),
        "header": view.header(POISON),
        # 상대 시점으로 부른다 — badges 는 '상대' 이름을 title 속성에 넣기 때문에,
        # 이래야 속성 탈출(따옴표)까지 검사된다.
        "badges": view.badges(epl.form(done, "Rival FC")),
        "team_card": view.team_card(
            POISON, standing, epl.form(done, POISON),
            epl.venue_record(done, POISON, home=True),
            epl.venue_record(done, POISON, home=False),
        ),
        # me 를 상대로 놓아야 오염된 팀명이 '상대' 칸과 round 에 함께 들어간다
        "fixtures_table": view.fixtures_table(
            [_match(score=None)], {POISON: standing}, "Rival FC"
        ),
        "next_match_card": view.next_match_card(upcoming, POISON),
        "h2h_card": view.h2h_card(records, epl.h2h_summary(records), POISON, 5),
        # form 행은 '상대' 이름을 쓴다 — 오염된 팀을 상대 자리에 놓아야 검사가 된다
        "results_table": view.results_table(
            epl.form(done, "Rival FC"), epl.h2h_summary(epl.form(done, "Rival FC"))
        ),
        "h2h_empty": view.h2h_card([], epl.h2h_summary([]), POISON, 5),
        "last_match_card": view.last_match_card(done[0], POISON),
        "standings_table": view.standings_table(epl.table(done), POISON),
        # 선수 이름도 외부 문자열이다(FotMob)
        "player_ratings_table": view.player_ratings_table(
            [dict(id=1, name=POISON, n=2, minutes=173, avg=7.42)]
        ),
        "live_card": view.live_card(
            dict(id=1, opp=POISON, home=False, score=POISON, clock=POISON), "Rival FC"
        ),
        "lineup_table": view.lineup_table(dict(
            formation=POISON, rating=6.7,
            starters=[dict(id=1, name=POISON, shirt=POISON, rating=7.3, season=8.16)],
            subs=[dict(id=2, name=POISON, shirt="", rating=6.1, season=None)],
        )),
        "injury_card": view.injury_card([dict(id=1, name=POISON, expected=POISON)]),
        "pending_card": view.pending_card(POISON),
        "plain_card": view.plain_card(POISON),
    }


@pytest.mark.parametrize("name,html", sorted(_fragments().items()))
def test_오염된_문자열이_raw_로_안_나간다(name, html):
    for marker in RAW_MARKERS:
        assert marker not in html, f"{name} 에서 {marker!r} 가 그대로 나간다"


def test_이스케이프된_형태로는_실제로_들어_있다():
    """빈 문자열을 돌려줘서 위 검사를 통과하는 걸 막는다."""
    frags = _fragments()
    for name in ("team", "badges", "team_card", "next_match_card", "fixtures_table",
                 "last_match_card", "standings_table", "pending_card", "results_table",
                 "player_ratings_table", "injury_card",
                 "live_card", "lineup_table"):
        assert ESCAPED in frags[name], f"{name} 이 팀명을 아예 안 그리고 있다"


def test_엠블럼이_붙어도_팀명은_이스케이프된다():
    """로고는 team() 한 곳에서 붙는다 — 그 자리가 이스케이프 통로이기도 하다."""
    view.LOGOS[POISON] = "https://x/1.png\" onerror=\"alert(1)"
    try:
        html = view.team(POISON)
    finally:
        view.LOGOS.pop(POISON, None)
    assert "<img" in html and ESCAPED in html
    for marker in RAW_MARKERS + ['onerror="alert']:
        assert marker not in html


def test_앰퍼샌드_팀명이_엔티티로_나간다():
    """실제 EPL 데이터에 있는 경우 — 이건 가상의 위협이 아니다."""
    assert view.team("Brighton & Hove Albion FC") == "Brighton &amp; Hove Albion"  # 로고 없을 때


def test_숫자_자리에_문자열이_오면_조용히_새지_않고_터진다():
    poisoned_row = dict(team="X FC", rank=1, p=1, w=1, d=0, l=0,
                        gf=1, ga=0, gd=1, pts="<script>alert(1)</script>")
    with pytest.raises((ValueError, TypeError)):
        view.standings_table([poisoned_row], "X FC")
