"""EPL 경기 데이터 — openfootball(GitHub JSON) 한 소스.

일정·결과·순위·상대전적·폼이 여기서 나온다.
라인업·부상·xG 는 여기 없다 — API-Football 키가 오면 별도 모듈로 붙인다.

소스를 openfootball 로 고른 이유(2026-09-06 실측):
  football-data.co.uk  사이트 전체 503
  Understat            봇에게는 xG 를 안 준다(광고 설정 JSON 만 옴)
  FBref                403 (Cloudflare)
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

SRC = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/en.1.json"
CACHE_DIR = Path(__file__).parent / "data"
TTL_SEC = 6 * 3600
# FotMob 은 기본 UA 에 404 를 준다. 한 군데서만 정해 둔다.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CURRENT = "2026-27"
CHELSEA = "Chelsea FC"


# ─── 로딩 ──────────────────────────────────────────────────────────────

def cached_json(url: str, cache: Path, ttl: int):
    """받아서 캐시하고 파싱까지. 평점 모듈(fotmob.py)도 같이 쓴다.

    캐시가 신선하면 그걸 쓰고, 받아오기가 실패하면 낡은 캐시라도 쓴다 —
    네트워크가 죽었다고 화면까지 죽을 이유는 없다.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    stale = not cache.exists() or time.time() - cache.stat().st_mtime > ttl
    if stale:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read()
            json.loads(body)  # 깨진 응답으로 멀쩡한 캐시를 덮지 않는다
            cache.write_bytes(body)
        except Exception:
            if not cache.exists():
                raise
    return json.loads(cache.read_text(encoding="utf-8"))


def load_season(season: str = CURRENT) -> list[dict]:
    """한 시즌 전 경기(날짜순)."""
    matches = cached_json(
        SRC.format(season=season), CACHE_DIR / f"en1-{season}.json", TTL_SEC
    )["matches"]
    return sorted(matches, key=lambda m: (m["date"], m.get("time", "")))


def load_seasons(seasons: list[str]) -> list[dict]:
    """여러 시즌을 한 리스트로(날짜순). 상대 전적처럼 시즌을 가로지르는 계산용.

    날짜순 정렬이 핵심이다 — 호출부가 최신 시즌부터 넘기든 과거부터 넘기든
    아래 함수들은 전부 "리스트가 시간순"을 전제로 뒤집는다.
    """
    out = []
    for s in seasons:
        for m in load_season(s):
            out.append({**m, "season": s})
    return sorted(out, key=lambda m: (m["date"], m.get("time", "")))


def short(team: str) -> str:
    """'Brighton & Hove Albion FC' → 'Brighton & Hove Albion'"""
    for suffix in (" FC", " AFC"):
        if team.endswith(suffix):
            return team[: -len(suffix)]
    return team


# ─── 조회 ──────────────────────────────────────────────────────────────

def ft(match: dict) -> tuple[int, int] | None:
    """최종 스코어, 없으면 None.

    openfootball 은 같은 경기를 두 형태로 준다 —
      {"ht": [1, 0], "ft": [2, 1]}   하프타임이 있을 때
      [0, 0]                          하프타임 기록이 없는 0-0 (2025-26 에 27경기)
    두 번째를 놓치면 그 경기가 순위표에서 통째로 빠진다.
    """
    s = match.get("score")
    if isinstance(s, dict):
        s = s.get("ft")
    return (s[0], s[1]) if isinstance(s, list) and len(s) == 2 else None


def played(matches: list[dict]) -> list[dict]:
    """결과가 나온 경기만."""
    return [m for m in matches if ft(m) is not None]


def team_matches(matches: list[dict], team: str) -> list[dict]:
    return [m for m in matches if team in (m["team1"], m["team2"])]


def upcoming(matches: list[dict], team: str, n: int | None = None) -> list[dict]:
    """아직 결과가 없는 경기들(날짜순). n 을 주면 앞에서 n 개."""
    out = [m for m in team_matches(matches, team) if ft(m) is None]
    return out[:n] if n else out


def next_match(matches: list[dict], team: str) -> dict | None:
    """다음 경기 하나 — 예정 목록의 첫 칸. 시즌이 끝났으면 None."""
    out = upcoming(matches, team, 1)
    return out[0] if out else None


def last_match(matches: list[dict], team: str) -> dict | None:
    done = played(team_matches(matches, team))
    return done[-1] if done else None


def result_for(match: dict, team: str) -> tuple[int, int, str, str, bool]:
    """한 경기를 team 시점으로 본다 → (득, 실, 'W'/'D'/'L', 상대, 홈여부)."""
    gh, ga = ft(match)
    home = match["team1"] == team
    gf, gaa = (gh, ga) if home else (ga, gh)
    opp = match["team2"] if home else match["team1"]
    return gf, gaa, "W" if gf > gaa else ("D" if gf == gaa else "L"), opp, home


# ─── 계산 ──────────────────────────────────────────────────────────────

def table(matches: list[dict]) -> list[dict]:
    """순위표. 승점 → 골득실 → 득점 순."""
    rows: dict[str, dict] = {}
    for m in played(matches):
        gh, ga = ft(m)
        for team, gf, gaa in ((m["team1"], gh, ga), (m["team2"], ga, gh)):
            r = rows.setdefault(
                team, dict(team=team, p=0, w=0, d=0, l=0, gf=0, ga=0, pts=0)
            )
            r["p"] += 1
            r["gf"] += gf
            r["ga"] += gaa
            if gf > gaa:
                r["w"] += 1
                r["pts"] += 3
            elif gf == gaa:
                r["d"] += 1
                r["pts"] += 1
            else:
                r["l"] += 1
    out = sorted(rows.values(), key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
        r["gd"] = r["gf"] - r["ga"]
    return out


def standing(matches: list[dict], team: str) -> dict | None:
    """순위표에서 한 팀 줄만."""
    return next((r for r in table(matches) if r["team"] == team), None)


def venue_record(matches: list[dict], team: str, home: bool) -> dict | None:
    """홈(또는 원정) 경기만의 성적.

    승점 계산을 새로 쓰지 않는다 — 부분집합에 standing() 을 다시 돌린다.
    그래서 홈+원정의 합은 정의상 전체와 같다(자체 점검이 그걸 확인한다).
    """
    side = "team1" if home else "team2"
    return standing([m for m in matches if m[side] == team], team)


def form(matches: list[dict], team: str, n: int = 5) -> list[dict]:
    """최근 n경기 — 최신이 앞."""
    out = []
    for m in reversed(played(team_matches(matches, team))[-n:]):
        gf, ga, res, opp, home = result_for(m, team)
        out.append(dict(date=m["date"], opp=opp, home=home, gf=gf, ga=ga, res=res))
    return out


def h2h(matches: list[dict], team_a: str, team_b: str) -> list[dict]:
    """두 팀 맞대결 — 최신이 앞. load_seasons() 로 여러 시즌을 넘겨 쓴다."""
    pair = [
        m
        for m in played(matches)
        if {m["team1"], m["team2"]} == {team_a, team_b}
    ]
    out = []
    for m in reversed(pair):
        gf, ga, res, opp, home = result_for(m, team_a)
        out.append(
            dict(date=m["date"], season=m.get("season", ""), home=home,
                 gf=gf, ga=ga, res=res, opp=opp)
        )
    return out


def h2h_summary(records: list[dict]) -> dict:
    """맞대결 승/무/패 집계 — team_a 시점."""
    return dict(
        n=len(records),
        w=sum(r["res"] == "W" for r in records),
        d=sum(r["res"] == "D" for r in records),
        l=sum(r["res"] == "L" for r in records),
        gf=sum(r["gf"] for r in records),
        ga=sum(r["ga"] for r in records),
    )


# ─── 자체 점검 ─────────────────────────────────────────────────────────

def _selfcheck():
    ms = load_season(CURRENT)
    assert len(ms) == 380, f"EPL 한 시즌은 380경기여야 한다: {len(ms)}"
    assert ms == sorted(ms, key=lambda m: (m["date"], m.get("time", ""))), "날짜순이 아니다"

    done = played(ms)
    t = table(ms)
    # 승점 총합 = 3*경기수 - 무승부수 (무승부는 3점이 아니라 2점을 나눠 갖는다)
    draws = sum(1 for m in done if ft(m)[0] == ft(m)[1])
    assert sum(r["pts"] for r in t) == 3 * len(done) - draws, "승점 총합이 안 맞는다"
    assert sum(r["p"] for r in t) == 2 * len(done), "출전 경기 수 총합이 안 맞는다"
    assert sum(r["gf"] for r in t) == sum(r["ga"] for r in t), "득점 총합 != 실점 총합"
    assert [r["rank"] for r in t] == list(range(1, len(t) + 1)), "순위가 1..n 이 아니다"
    for r in t:
        assert r["w"] + r["d"] + r["l"] == r["p"], f"{r['team']} 승무패 합 != 경기수"

    # 첼시 폼이 실제 경기 기록과 맞는가
    f = form(ms, CHELSEA, n=3)
    assert len(f) == min(3, len(played(team_matches(ms, CHELSEA)))), "폼 길이가 안 맞는다"
    for row in f:
        assert row["res"] in "WDL" and row["opp"] != CHELSEA

    nm = next_match(ms, CHELSEA)
    assert nm is None or ft(nm) is None, "다음 경기인데 결과가 있다"

    up = upcoming(ms, CHELSEA, 5)
    assert len(up) <= 5, "예정 경기를 n 개로 안 자른다"
    assert all(ft(m) is None for m in up), "예정 경기에 결과가 들어 있다"
    assert [m["date"] for m in up] == sorted(m["date"] for m in up), "예정 경기가 날짜순이 아니다"
    assert nm == (up[0] if up else None), "next_match 가 예정 목록의 첫 칸이 아니다"

    # 홈/원정을 쪼개도 합은 전체와 같아야 한다 — 쪼개는 축이 틀리면 여기서 걸린다
    full = standing(ms, CHELSEA)
    h = venue_record(ms, CHELSEA, True)
    a = venue_record(ms, CHELSEA, False)
    for field in ("p", "w", "d", "l", "gf", "ga", "pts"):
        got = (h[field] if h else 0) + (a[field] if a else 0)
        assert got == full[field], f"홈+원정 {field} 합이 전체와 다르다: {got} != {full[field]}"

    # 끝난 시즌은 380경기가 전부 소화돼 있어야 한다.
    # 이 검사가 [0,0] 형식을 놓치던 결함을 잡았다 — 2025-26 에서 27경기가 새고 있었다.
    for s_ in ("2025-26", "2024-25"):
        old = load_season(s_)
        assert len(played(old)) == 380, f"{s_}: 끝난 시즌인데 소화 {len(played(old))}경기"

    # 상대 전적: 맞대결은 시즌당 최대 2번
    hist = load_seasons(["2025-26", "2024-25"])
    rec = h2h(hist, CHELSEA, "Arsenal FC")
    assert len(rec) <= 4, f"두 시즌 맞대결이 4경기를 넘는다: {len(rec)}"
    s = h2h_summary(rec)
    assert s["w"] + s["d"] + s["l"] == s["n"], "맞대결 승무패 합이 안 맞는다"
    assert hist == sorted(hist, key=lambda m: (m["date"], m.get("time", ""))),         "여러 시즌 합본이 날짜순이 아니다"
    assert all(rec[i]["date"] >= rec[i + 1]["date"] for i in range(len(rec) - 1)),         "상대 전적이 최신순이 아니다"

    if nm:
        print(f"OK  {len(ms)}경기 / 소화 {len(done)} / 순위표 {len(t)}팀 / "
              f"첼시 다음: {short(nm['team1'])} vs {short(nm['team2'])} ({nm['date']})")
    else:
        print("OK  (시즌 종료)")


if __name__ == "__main__":
    _selfcheck()
