"""화면 조각 — HTML 문자열만 만든다. streamlit 을 import 하지 않는다.

배치(st.markdown 호출)는 app.py 가 한다. 여기를 갈라둔 이유:
  ① 팀명·라운드명은 openfootball 에서 온 **외부 문자열**이다. 신뢰하는
     소스여도 이스케이프 없이 unsafe_allow_html 에 넣지 않는다.
     ("Brighton & Hove Albion" 의 & 는 이미 실제로 새고 있었다)
  ② 그게 지켜지는지 검사하려면 순수 함수여야 한다 —
     CI(tests/) 는 streamlit 없이 돈다.

규칙 두 개:
  문자열은 esc() 를 지난다. 팀명은 team() 이 유일한 통로다.
  숫자는 이스케이프하지 않고 {x:d} 로 포맷한다 — 문자열이 섞여 오면
  조용히 새는 대신 그 자리에서 터진다.
"""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import epl

LONDON, SEOUL = ZoneInfo("Europe/London"), ZoneInfo("Asia/Seoul")

GREEN, RED, GREY = "var(--green)", "var(--red)", "var(--text-4)"
RES_COLOR = {"W": GREEN, "D": GREY, "L": RED}

CSS = """<style>
 :root{
   --bg:#0B0F17; --surface:#131A28; --border:#1E2A3E;
   --text-1:#E8EEF9; --text-2:#94A3B8; --text-3:#64748B; --text-4:#475569;
   --blue:#2E6FE8; --green:#22C55E; --red:#EF4444; --amber:#F59E0B;
   --mono:'JetBrains Mono',ui-monospace,Consolas,monospace;
 }
 .stApp{background:var(--bg);color:var(--text-1)}
 #MainMenu,footer,header{visibility:hidden}
 .blk{max-width:1180px;margin:0 auto}
 .lbl{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:1.6px;
      text-transform:uppercase;color:var(--text-2);margin:30px 0 10px}
 .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
 .b{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;
    border-radius:4px;font-family:var(--mono);font-size:11px;font-weight:700;
    margin-right:4px;color:#fff}
 table.t{width:100%;border-collapse:collapse;font-size:13px}
 table.t th{font-family:var(--mono);font-size:10px;letter-spacing:1px;text-transform:uppercase;
            color:var(--text-3);text-align:right;padding:6px 8px;border-bottom:1px solid var(--border)}
 table.t th:first-child,table.t td:first-child{text-align:left}
 table.t td{padding:6px 8px;border-bottom:1px solid rgba(30,42,62,.5);text-align:right;
            color:var(--text-1)}
 .num{font-family:var(--mono)}
 .dim{color:var(--text-3)}
</style>"""


# ─── 안전장치 ──────────────────────────────────────────────────────────

def esc(value) -> str:
    """외부 문자열 → HTML 안전. 따옴표까지 바꾼다(속성 안에 들어가므로)."""
    return escape(str(value), quote=True)


def team(name: str) -> str:
    """팀명 짧게 + 이스케이프. 팀명이 화면으로 나가는 유일한 통로."""
    return esc(epl.short(name))


# ─── 시각 ──────────────────────────────────────────────────────────────

def kickoff(match: dict) -> tuple[datetime, datetime]:
    """(현지 킥오프, 한국 킥오프). 날짜가 망가져 있으면 여기서 터진다."""
    lon = datetime.fromisoformat(f"{match['date']}T{match.get('time', '15:00')}")
    lon = lon.replace(tzinfo=LONDON)
    return lon, lon.astimezone(SEOUL)


def dday(seoul_kickoff: datetime, now: datetime | None = None) -> str:
    days = (seoul_kickoff.date() - (now or datetime.now(SEOUL)).date()).days
    if days == 0:
        return "오늘"
    if days == 1:
        return "내일"
    return f"D-{days}" if days > 0 else "진행/종료"


# ─── 조각 ──────────────────────────────────────────────────────────────

def label(text: str) -> str:
    return f"<div class='lbl'>{esc(text)}</div>"


def header(season_name: str) -> str:
    return (
        "<div style='display:flex;align-items:baseline;gap:12px;padding-top:8px'>"
        "<span style='font-size:26px;font-weight:700;letter-spacing:-.5px'>CHELSEA</span>"
        "<span class='dim' style='font-family:var(--mono);font-size:12px;letter-spacing:1.5px'>"
        f"PREMIER LEAGUE {esc(season_name)}</span></div>"
    )


def badges(rows: list[dict]) -> str:
    """최근 폼 — 왼쪽이 오래된 경기."""
    out = "".join(
        f"<span class='b' style='background:{RES_COLOR[r['res']]}' "
        f"title=\"{esc(r['date'])} {'vs' if r['home'] else '@'} {team(r['opp'])} "
        f"{r['gf']:d}-{r['ga']:d}\">{esc(r['res'])}</span>"
        for r in reversed(rows)
    )
    return out or "<span class='dim'>경기 없음</span>"


def wdl(row: dict | None) -> str:
    """승무패를 짧게. 0 인 항목은 뺀다 — '3승 0무 0패' 보다 '3승' 이 읽기 쉽다."""
    if not row or not row["p"]:
        return "기록 없음"
    parts = [f"{row[k]:d}{n}" for k, n in (("w", "승"), ("d", "무"), ("l", "패")) if row[k]]
    return " ".join(parts)


def _venue(label: str, row: dict | None) -> str:
    """'홈 3승 1무 (10점)' 한 토막. 경기가 없으면 '홈 —'."""
    if not row or not row["p"]:
        return f"{label} —"
    return f"{label} {wdl(row)}<span class='dim'> ({row['pts']:d}점)</span>"


def team_card(name: str, row: dict | None, form_rows: list[dict],
              home: dict | None = None, away: dict | None = None) -> str:
    """한 팀의 순위·승점·홈원정·폼.

    계산은 받기만 한다(matches 를 안 받는다) — 그려야 할 숫자가
    어디서 왔는지는 부르는 쪽 책임이다.
    """
    if row:
        stat = (f"{row['rank']:d}위 · {row['pts']:d}점 · "
                f"{row['w']:d}승 {row['d']:d}무 {row['l']:d}패 · 득실 {row['gd']:+d}")
    else:
        stat = "기록 없음"

    venue = ""
    if home or away:
        venue = ("<div class='num' style='font-size:12px;color:var(--text-3);margin-bottom:12px'>"
                 f"{_venue('홈', home)} · {_venue('원정', away)}</div>")

    return (
        "<div class='card'>"
        f"<div style='font-size:19px;font-weight:600;margin-bottom:10px'>{team(name)}</div>"
        "<div class='num' style='font-size:13px;color:var(--text-2);margin-bottom:6px'>"
        f"{stat}</div>"
        f"{venue}"
        f"<div>{badges(form_rows)}</div>"
        "</div>"
    )


def fixtures_table(fixtures: list[dict], standings: dict, me: str) -> str:
    """다음 몇 경기 — 상대와 그 상대의 현재 순위."""
    if not fixtures:
        return plain_card("남은 경기가 없습니다.")
    rows = ""
    for m in fixtures:
        home = m["team1"] == me
        opp = m["team2"] if home else m["team1"]
        row = standings.get(opp)
        rank = f"{row['rank']:d}위" if row else "—"
        _, seoul = kickoff(m)
        rows += (
            f"<tr><td class='num dim'>{seoul:%m/%d(%a)}</td>"
            f"<td style='text-align:left'>{'홈' if home else '원정'}</td>"
            f"<td style='text-align:left'>{team(opp)}</td>"
            f"<td class='num'>{rank}</td>"
            f"<td class='num dim'>{esc(m['round'])}</td></tr>"
        )
    return (
        "<div class='card'><table class='t'>"
        "<tr><th>날짜</th><th style='text-align:left'>장소</th>"
        "<th style='text-align:left'>상대</th><th>상대 순위</th><th>라운드</th></tr>"
        f"{rows}</table></div>"
    )


def next_match_card(match: dict, me: str, now: datetime | None = None) -> str:
    lon, sel = kickoff(match)
    home = match["team1"] == me
    return (
        "<div class='card' style='border-left:3px solid var(--blue)'>"
        "<div style='display:flex;justify-content:space-between;align-items:center;"
        "flex-wrap:wrap;gap:14px'><div>"
        "<div style='font-size:23px;font-weight:600'>"
        f"{team(match['team1'])} <span class='dim' style='font-size:15px'>vs</span> "
        f"{team(match['team2'])}</div>"
        "<div class='num' style='font-size:13px;color:var(--text-2);margin-top:6px'>"
        f"{esc(match['round'])} · {'홈' if home else '원정'} · "
        f"{sel:%m/%d(%a) %H:%M} 한국 <span class='dim'>({lon:%H:%M} 현지)</span></div>"
        "</div>"
        "<div class='num' style='font-size:30px;font-weight:700;color:var(--blue)'>"
        f"{esc(dday(sel, now))}</div>"
        "</div></div>"
    )


def h2h_card(records: list[dict], summary: dict, opp: str, seasons: int) -> str:
    if not records:
        return (f"<div class='card dim'>{team(opp)} 와(과) 최근 {seasons:d}시즌 "
                "프리미어리그 맞대결이 없습니다 (승격팀이거나 강등 기간).</div>")
    rows = "".join(
        f"<tr><td class='num dim'>{esc(r['season'])}</td>"
        f"<td style='text-align:left'>{'홈' if r['home'] else '원정'}</td>"
        f"<td class='num'>{r['gf']:d}-{r['ga']:d}</td>"
        f"<td style='color:{RES_COLOR[r['res']]}'>{esc(r['res'])}</td>"
        f"<td class='num dim'>{esc(r['date'])}</td></tr>"
        for r in records
    )
    return (
        f"<div class='card'>{wdl_line(summary)}"
        "<table class='t'><tr><th>시즌</th><th style='text-align:left'>장소</th>"
        f"<th>스코어</th><th>결과</th><th>날짜</th></tr>{rows}</table></div>"
    )


def wdl_line(summary: dict) -> str:
    """'12경기 5승 3무 4패 · 득실 18-15' 한 줄. h2h 와 최근 결과가 같이 쓴다."""
    return (
        "<div class='num' style='font-size:17px;margin-bottom:12px'>"
        f"{summary['n']:d}경기 <span style='color:{GREEN}'>{summary['w']:d}승</span> "
        f"<span class='dim'>{summary['d']:d}무</span> "
        f"<span style='color:{RED}'>{summary['l']:d}패</span>"
        "<span class='dim' style='font-size:13px'> · 득실 "
        f"{summary['gf']:d}-{summary['ga']:d}</span></div>"
    )


def results_table(records: list[dict], summary: dict) -> str:
    """최근 결과 — epl.form() 의 행을 그대로 받는다(h2h 와 같은 모양)."""
    if not records:
        return plain_card("아직 치른 경기가 없습니다.")
    rows = "".join(
        f"<tr><td class='num dim'>{esc(r['date'])}</td>"
        f"<td style='text-align:left'>{'홈' if r['home'] else '원정'}</td>"
        f"<td style='text-align:left'>{team(r['opp'])}</td>"
        f"<td class='num'>{r['gf']:d}-{r['ga']:d}</td>"
        f"<td style='color:{RES_COLOR[r['res']]}'>{esc(r['res'])}</td></tr>"
        for r in records
    )
    return (
        f"<div class='card'>{wdl_line(summary)}"
        "<table class='t'><tr><th>날짜</th><th style='text-align:left'>장소</th>"
        "<th style='text-align:left'>상대</th><th>스코어</th><th>결과</th></tr>"
        f"{rows}</table></div>"
    )


def last_match_card(match: dict, me: str) -> str:
    _, _, res, _, home = epl.result_for(match, me)
    gh, ga = epl.ft(match)
    score = match.get("score")
    ht = score.get("ht") if isinstance(score, dict) else None
    tint = RES_COLOR[res]
    half = f" · 전반 {ht[0]:d}-{ht[1]:d}" if ht else ""
    return (
        f"<div class='card' style='border-left:3px solid {tint}'>"
        "<div style='font-size:19px;font-weight:600'>"
        f"{team(match['team1'])} "
        f"<span class='num' style='color:{tint}'>{gh:d} - {ga:d}</span> "
        f"{team(match['team2'])}</div>"
        "<div class='num' style='font-size:13px;color:var(--text-2);margin-top:6px'>"
        f"{esc(match['round'])} · {esc(match['date'])} · {'홈' if home else '원정'}"
        f"{half}</div></div>"
    )


def standings_table(rows: list[dict], me: str) -> str:
    body = "".join(
        f"<tr style=\"{'background:rgba(46,111,232,.12)' if r['team'] == me else ''}\">"
        f"<td class='num dim'>{r['rank']:d}</td>"
        f"<td style='text-align:left;{'font-weight:600' if r['team'] == me else ''}'>"
        f"{team(r['team'])}</td>"
        f"<td class='num'>{r['p']:d}</td><td class='num'>{r['w']:d}</td>"
        f"<td class='num'>{r['d']:d}</td><td class='num'>{r['l']:d}</td>"
        f"<td class='num dim'>{r['gf']:d}:{r['ga']:d}</td>"
        f"<td class='num'>{r['gd']:+d}</td>"
        f"<td class='num' style='font-weight:700'>{r['pts']:d}</td></tr>"
        for r in rows
    )
    return (
        "<div class='card'><table class='t'>"
        "<tr><th>#</th><th style='text-align:left'>팀</th><th>경기</th><th>승</th>"
        f"<th>무</th><th>패</th><th>득실</th><th>±</th><th>승점</th></tr>{body}</table></div>"
    )


def pending_card(text: str) -> str:
    return (f"<div class='card dim' style='border-left:3px solid var(--amber)'>"
            f"{esc(text)}</div>")


def plain_card(text: str) -> str:
    return f"<div class='card dim'>{esc(text)}</div>"


def footer() -> str:
    return ("<div class='dim' style='font-size:11px;font-family:var(--mono);margin:26px 0 40px'>"
            "SOURCE openfootball/football.json · 캐시 6시간</div>")


def rating_color(value: float) -> str:
    """FotMob 평점 색. 7.0 이 평범한 경기다 — 기준선을 화면에서도 그렇게 잡는다."""
    return GREEN if value >= 7.5 else (RED if value < 6.5 else "var(--text-1)")


def player_ratings_table(rows: list[dict]) -> str:
    """시즌 평균 평점 — 경기 평점의 단순 평균(fotmob.average 가 계산)."""
    if not rows:
        return pending_card("평점을 못 받아왔습니다. FotMob 이 막혔거나 아직 경기가 없습니다.")
    body = "".join(
        f"<tr><td class='num dim'>{i:d}</td>"
        f"<td style='text-align:left'>{team(r['name'])}</td>"
        f"<td class='num'>{r['n']:d}</td>"
        f"<td class='num dim'>{r['minutes']:d}</td>"
        f"<td class='num' style='color:{rating_color(r['avg'])};font-weight:600'>"
        f"{r['avg']:.2f}</td></tr>"
        for i, r in enumerate(rows, 1)
    )
    return (
        "<div class='card'><table class='t'>"
        "<tr><th>#</th><th style='text-align:left'>선수</th><th>경기</th>"
        f"<th>분</th><th>평균</th></tr>{body}</table></div>"
    )


def injury_card(rows: list[dict]) -> str:
    """결장·의심 선수. 부상 종류는 안 나온다 — FotMob 이 숫자 코드만 준다."""
    if not rows:
        return plain_card("결장 중인 선수가 없습니다.")
    items = "".join(
        f"<tr><td style='text-align:left'>{team(r['name'])}</td>"
        f"<td class='num dim'>{esc(r['expected'])}</td></tr>"
        for r in rows
    )
    return (
        "<div class='card' style='border-left:3px solid var(--amber)'>"
        "<table class='t'><tr><th style='text-align:left'>선수</th>"
        f"<th style='text-align:right'>복귀 예상</th></tr>{items}</table></div>"
    )


def live_card(m: dict, me: str) -> str:
    """진행중인 경기 — 잠정 스코어와 경과."""
    left, right = (me, m["opp"]) if m["home"] else (m["opp"], me)
    return (
        "<div class='card' style='border-left:3px solid var(--red)'>"
        "<div style='display:flex;justify-content:space-between;align-items:center;"
        "flex-wrap:wrap;gap:14px'><div>"
        "<div style='font-size:23px;font-weight:600'>"
        f"{team(left)} <span class='num' style='color:var(--red)'>{esc(m['score'])}</span> "
        f"{team(right)}</div>"
        "<div class='num' style='font-size:13px;color:var(--text-2);margin-top:6px'>"
        f"{'홈' if m['home'] else '원정'}</div></div>"
        "<div class='num' style='font-size:20px;font-weight:700;color:var(--red)'>"
        f"{esc(m['clock'])}</div></div></div>"
    )


def lineup_table(lu: dict, now: bool = False) -> str:
    """선발·교체와 각자의 현재 평점. 시즌 평점을 옆에 둬서 오늘이 어떤지 보이게."""
    if not lu or not lu.get("starters"):
        return pending_card("라인업이 아직 안 나왔습니다.")

    def rows(players, tag):
        tag = f" <span class='dim'>{esc(tag)}</span>" if tag else ""
        return "".join(
            f"<tr><td class='num dim'>{esc(p['shirt'])}</td>"
            f"<td style='text-align:left'>{team(p['name'])}{tag}</td>"
            f"<td class='num dim'>{'—' if p['season'] is None else format(p['season'], '.2f')}</td>"
            f"<td class='num' style='color:{rating_color(p['rating'])};font-weight:600'>"
            f"{p['rating']:.2f}</td></tr>"
            for p in players
        )

    head = f"{esc(lu['formation'])}"
    if lu.get("rating") is not None:
        head += f" · 팀 평점 <span style='color:{rating_color(lu['rating'])}'>{lu['rating']:.2f}</span>"
    return (
        "<div class='card'>"
        f"<div class='num' style='font-size:15px;margin-bottom:12px'>{head}</div>"
        "<table class='t'><tr><th>번호</th><th style='text-align:left'>선수</th>"
        f"<th>시즌</th><th>{'지금' if now else '평점'}</th></tr>"
        + rows(lu["starters"], "") + rows(lu.get("subs") or [], "교체")
        + "</table></div>"
    )
