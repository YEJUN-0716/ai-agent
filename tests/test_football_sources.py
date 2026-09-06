"""두 소스를 섞는 자리 — 결과·순위표는 FotMob, 일정·과거 시즌은 openfootball.

네트워크 없음(FotMob 응답 모양을 직접 만든다).
여기서 잡으려는 건 두 가지다.
  ① 원정 경기의 스코어 방향. FotMob 은 홈-원정 순서로 주는데 우리 팀 기준으로
     읽으면 원정 경기가 통째로 뒤집힌다 — 실제로 한 번 그렇게 짰고,
     경기 수만 세는 검사는 그걸 통과시켰다.
  ② '다음 경기'를 결과 유무로 가르면 안 된다는 것. openfootball 은 결과를
     주 1회만 올려서 끝난 경기가 며칠씩 '예정'으로 남는다.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "football"))
import epl  # noqa: E402
import fotmob  # noqa: E402

CHELSEA, FULHAM = 8455, 9879


def _row(name, tid, **kw):
    base = dict(name=name, id=tid, idx=1, played=1, wins=1, draws=0, losses=0,
                scoresStr="3-2", goalConDiff=1, pts=3)
    base.update(kw)
    return base


PAYLOAD = {
    "table": [{"data": {"table": {"all": [
        _row("Chelsea", CHELSEA),
        _row("Fulham", FULHAM, idx=2, wins=0, losses=1, scoresStr="2-3",
             goalConDiff=-1, pts=0),
    ]}}}],
    "fixtures": {"allFixtures": {"fixtures": [{
        # 첼시가 **원정**. 스코어는 홈-원정 순서라 풀럼 2 - 첼시 3 이다.
        "id": 1,
        "opponent": {"id": FULHAM, "name": "Fulham"},
        "home": {"id": FULHAM, "score": 2},
        "away": {"id": CHELSEA, "score": 3},
        "tournament": {"leagueId": fotmob.EPL_ID},
        "status": {"finished": True, "utcTime": "2026-08-24T19:00:00.000Z",
                   "scoreStr": "2 - 3"},
    }]}},
}


def test_원정_경기의_스코어가_뒤집히지_않는다(monkeypatch):
    monkeypatch.setattr(fotmob, "team", lambda team_id=CHELSEA: PAYLOAD)
    (m,) = fotmob.as_matches(CHELSEA)
    assert (m["team1"], m["team2"]) == ("Fulham", "Chelsea")
    assert epl.ft(m) == (2, 3)
    gf, ga, res, opp, home = epl.result_for(m, "Chelsea")
    assert (gf, ga, res, home) == (3, 2, "W", False)


def test_우리가_다시_센_순위표가_FotMob_것과_같다(monkeypatch):
    """자체 점검이 실제로 하는 대조 — 경기 수만 맞춰 보면 ①을 놓친다."""
    monkeypatch.setattr(fotmob, "team", lambda team_id=CHELSEA: PAYLOAD)
    theirs = next(r for r in fotmob.table(CHELSEA) if r["team"] == "Chelsea")
    mine = next(r for r in epl.table(fotmob.as_matches(CHELSEA)) if r["team"] == "Chelsea")
    for k in ("p", "w", "d", "l", "gf", "ga", "pts"):
        assert mine[k] == theirs[k], k


def _match(days, time="15:00"):
    when = datetime.now(epl.LONDON) + timedelta(days=days)
    return dict(date=when.strftime("%Y-%m-%d"), time=time,
                team1="Chelsea", team2="Arsenal", score=None, round="Matchday 1")


def test_결과가_없어도_지난_경기는_예정이_아니다():
    """소스가 결과를 안 올린 경기 — 시계로 가른다."""
    past, future = _match(-3), _match(+3)
    assert epl.upcoming([past, future], "Chelsea") == [future]


def test_진행중인_경기는_다음_경기로_남는다():
    now = datetime.now(epl.LONDON) - timedelta(minutes=30)
    live = dict(date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M"),
                team1="Chelsea", team2="Arsenal", score=None, round="Matchday 3")
    assert epl.upcoming([live], "Chelsea") == [live]
