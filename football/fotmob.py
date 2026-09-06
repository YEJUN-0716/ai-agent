"""선수 평점 — FotMob 비공식 JSON(`/api/data/`) 한 소스.

왜 여기뿐인가 (2026-09-06 실측):
  API-Football  평점을 주지만 무료 플랜은 2022~2024 시즌만 (이번 시즌은 Pro $19/월)
  SofaScore     403
  FotMob        200 + 이번 시즌 경기별 평점이 실제로 온다  ← 유일

**공식 API 가 아니다.** 키는 필요 없지만 언제든 막힐 수 있다. 그래서 이 모듈은
실패를 위로 던지지 않고 빈 값을 돌려준다 — 평점이 죽어도 순위·일정
(openfootball)은 살아 있어야 한다.
"""
from __future__ import annotations

from epl import CACHE_DIR, cached_json

TEAM_ID = 8455   # Chelsea
EPL_ID = 47      # FotMob 의 프리미어리그 id (openfootball 과 다른 체계다)

TEAM_URL = "https://www.fotmob.com/api/data/teams?id={team_id}"
MATCH_URL = "https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

TEAM_TTL = 6 * 3600
# 끝난 경기의 평점은 더 안 변한다. 한 경기가 260KB 라 다시 받을 이유가 없다.
MATCH_TTL = 10 ** 9

RATING = "FotMob rating"
MINUTES = "Minutes played"


def _stat(player: dict, key: str):
    """playerStats 한 명에서 숫자 하나. 없으면 None(벤치는 stats 가 빈 리스트다)."""
    for block in player.get("stats") or []:
        cell = (block.get("stats") or {}).get(key)
        if isinstance(cell, dict):
            return (cell.get("stat") or {}).get("value")
    return None


def team(team_id: int = TEAM_ID) -> dict:
    return cached_json(
        TEAM_URL.format(team_id=team_id), CACHE_DIR / f"fotmob-team-{team_id}.json", TEAM_TTL
    )


def fixtures(team_id: int = TEAM_ID, league_id: int | None = EPL_ID) -> list[dict]:
    """끝난 경기(오래된 것부터). league_id 를 주면 그 대회만 — 컵 경기가 섞이지 않게."""
    out = []
    for f in (team().get("fixtures", {}).get("allFixtures", {}) or {}).get("fixtures", []):
        status = f.get("status") or {}
        if not status.get("finished"):
            continue
        if league_id and (f.get("tournament") or {}).get("leagueId") != league_id:
            continue
        home = (f.get("home") or {}).get("id") == team_id
        out.append(dict(
            id=f["id"],
            date=(status.get("utcTime") or "")[:10],
            opp=(f.get("opponent") or {}).get("name", ""),
            opp_id=(f.get("opponent") or {}).get("id"),
            home=home,
            # 스코어는 홈-원정 순서로 온다. 'scoreStr' 을 우리 팀 기준으로 읽으면
            # 원정 경기가 통째로 뒤집힌다 — home/away 의 score 를 그대로 쓴다.
            home_goals=(f.get("home") or {}).get("score"),
            away_goals=(f.get("away") or {}).get("score"),
            score=status.get("scoreStr", ""),
        ))
    return sorted(out, key=lambda m: m["date"])


def table(team_id: int = TEAM_ID) -> list[dict]:
    """리그 순위표 — `epl.table()` 과 **같은 행 모양**으로 돌려준다.

    소스를 갈아탄 이유: openfootball 은 주 1회(수요일)만 결과를 올린다.
    9/2 다음 커밋이 없어서 3라운드가 통째로 비어 있었다 — 캐시가 아니라 소스다.
    모양을 맞춰 두면 화면(view)과 나머지 계산은 하나도 안 건드린다.
    """
    blocks = team(team_id).get("table") or []
    rows = ((blocks[0].get("data") if blocks else {}) or {}).get("table", {}).get("all", [])
    out = []
    for r in rows:
        gf, ga = (int(x) for x in r["scoresStr"].split("-"))
        out.append(dict(team=r["name"], rank=r["idx"], p=r["played"], w=r["wins"],
                        d=r["draws"], l=r["losses"], gf=gf, ga=ga,
                        gd=r["goalConDiff"], pts=r["pts"]))
    return out


def as_matches(team_id: int = TEAM_ID, league_id: int | None = EPL_ID) -> list[dict]:
    """끝난 경기 → **openfootball 경기 모양**. epl.py 의 계산을 그대로 쓰려고.

    팀 이름은 순위표 쪽 이름을 쓴다 — 일정에 있는 'Brighton' 이 아니라
    'Brighton and Hove Albion'. openfootball 이름을 epl.short() 로 줄인 것과
    20팀 전부 일치한다(그래서 매핑 표가 필요 없다).
    """
    names = {r["team"]: r["team"] for r in table(team_id)}
    ids = {}
    blocks = team(team_id).get("table") or []
    for r in ((blocks[0].get("data") if blocks else {}) or {}).get("table", {}).get("all", []):
        ids[r["id"]] = r["name"]
    me = ids.get(team_id, "")
    out = []
    for f in fixtures(team_id, league_id):
        opp = ids.get(f["opp_id"], f["opp"])
        out.append(dict(
            date=f["date"], time="", round="",
            team1=me if f["home"] else opp,
            team2=opp if f["home"] else me,
            score={"ft": [int(f["home_goals"]), int(f["away_goals"])]},
        ))
    assert not names or me, "순위표에서 우리 팀을 못 찾았다 — id 체계가 바뀌었다"
    return out


def match_ratings(match_id: int, team_id: int = TEAM_ID) -> list[dict]:
    """한 경기에서 그 팀 선수들의 평점(높은 순). 평점이 없는 선수(미출전)는 뺀다."""
    data = cached_json(
        MATCH_URL.format(match_id=match_id), CACHE_DIR / f"fotmob-match-{match_id}.json", MATCH_TTL
    )
    out = []
    for p in (data.get("content", {}).get("playerStats") or {}).values():
        if p.get("teamId") != team_id:
            continue
        rating = _stat(p, RATING)
        if rating is None:
            continue
        out.append(dict(
            id=p.get("id"), name=p.get("name", ""), rating=float(rating),
            minutes=_stat(p, MINUTES) or 0,
        ))
    return sorted(out, key=lambda r: -r["rating"])


def season_rows(team_id: int = TEAM_ID, league_id: int | None = EPL_ID) -> list[dict]:
    """(경기 × 선수) 한 줄씩. 경기 하나가 실패해도 나머지는 살린다."""
    rows = []
    for m in fixtures(team_id, league_id):
        try:
            players = match_ratings(m["id"], team_id)
        except Exception:
            continue
        for p in players:
            rows.append({**p, "match": m["id"], "date": m["date"], "opp": m["opp"]})
    return rows


def average(rows: list[dict], min_matches: int = 1) -> list[dict]:
    """선수별 평균 평점(높은 순). 순수 함수 — 네트워크를 안 탄다.

    경기 평점의 **단순 평균**이다. 출전 시간으로 가중하지 않는다:
    5분 뛴 경기와 90분 뛴 경기가 같은 무게라는 뜻이고, `min_matches` 로
    표본이 얇은 선수를 잘라내는 게 그 대가다. FotMob 자신의 시즌 평점과
    얼마나 갈리는지는 _selfcheck() 가 잰다.
    """
    by: dict[int, dict] = {}
    for r in rows:
        p = by.setdefault(r["id"], dict(id=r["id"], name=r["name"], n=0, minutes=0, total=0.0))
        p["n"] += 1
        p["minutes"] += r["minutes"]
        p["total"] += r["rating"]
        p["name"] = r["name"]
    out = [dict(p, avg=p["total"] / p["n"]) for p in by.values() if p["n"] >= min_matches]
    return sorted(out, key=lambda p: -p["avg"])


def injuries(team_id: int = TEAM_ID) -> list[dict]:
    """지금 못 뛰는 선수 — 이름과 복귀 예상.

    같은 응답 안에 명단이 두 군데 있고 **서로 다르다**(2026-09-07 실측):
      squad[].members[].injury                    ← 여기를 쓴다. 지금 상태다.
      overview.lastLineupStats.unavailable        지난 경기 시점의 결장자다
    이름이 붙은 그릇('lastLineupStats')이 답을 갖고 있다 — 지난 경기에 못 뛴
    선수(Enzo)와 지금 의심스러운 선수(Caicedo)는 다른 명단이다. 프리뷰는 다음
    경기를 묻는 화면이므로 앞쪽이다.

    부상 '종류'는 못 준다 — `injuryId` 는 숫자 코드고 라벨이 응답에 없다.
    """
    out = []
    for group in (team(team_id).get("squad", {}) or {}).get("squad", []):
        for m in group.get("members", []):
            hurt = m.get("injury")
            if hurt:
                out.append(dict(id=m.get("id"), name=m.get("name", ""),
                                expected=hurt.get("expectedReturn", "")))
    return out


def squad_ratings(team_id: int = TEAM_ID) -> dict[int, float]:
    """FotMob 이 스스로 매긴 시즌 평점 — 우리 평균을 대조할 다른 경로."""
    out = {}
    for group in (team(team_id).get("squad", {}) or {}).get("squad", []):
        for m in group.get("members", []):
            if m.get("rating") is not None:
                out[m["id"]] = float(m["rating"])
    return out


def _selfcheck():
    """네트워크를 탄다. `python football/fotmob.py`"""
    games = fixtures()
    assert games, "끝난 EPL 경기가 하나도 없다 — 엔드포인트가 막혔거나 형식이 바뀌었다"
    assert all(len(g["date"]) == 10 for g in games), "날짜 형식이 깨졌다"

    rows = season_rows()
    assert rows, "평점 행이 비었다"
    assert all(0 < r["rating"] <= 10 for r in rows), "평점 범위 밖의 값이 있다"

    players = average(rows)
    # 합계를 다른 경로로 다시 센다 — 선수별 경기 수의 합 = 전체 행 수
    assert sum(p["n"] for p in players) == len(rows)

    standings = table()
    assert len(standings) == 20, f"순위표가 20팀이 아니다: {len(standings)}"
    us = next(r for r in standings if r["team"].startswith("Chelsea"))
    # 변환한 경기로 순위표를 **다시 세서** FotMob 것과 맞춰 본다.
    # 경기 수만 맞춰 보면 원정 스코어가 뒤집혀 있어도 통과한다(실제로 그랬다).
    import epl
    mine = next(r for r in epl.table(as_matches()) if r["team"] == us["team"])
    for k in ("p", "w", "d", "l", "gf", "ga", "pts"):
        assert mine[k] == us[k], f"{k}: 우리가 센 값 {mine[k]} vs FotMob {us[k]}"

    theirs = squad_ratings()
    pairs = [(p, theirs[p["id"]]) for p in players if p["id"] in theirs and p["n"] >= 2]
    worst = max((abs(p["avg"] - t), p["name"]) for p, t in pairs) if pairs else (0, "")
    print(f"OK  {len(games)}경기 / 평점 {len(rows)}행 / 선수 {len(players)}명")
    print(f"    FotMob 시즌 평점과 대조: {len(pairs)}명, 최대 차이 {worst[0]:.2f} ({worst[1]})")
    hurt = injuries()
    print(f"    결장/의심 {len(hurt)}명: "
          + ", ".join(f"{h['name']}({h['expected']})" for h in hurt))
    print("    상위: " + ", ".join(f"{p['name']} {p['avg']:.2f}({p['n']}경기)" for p in players[:3]))


if __name__ == "__main__":
    _selfcheck()
