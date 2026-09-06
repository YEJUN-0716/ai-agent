"""첼시 프리뷰/리뷰 대시보드.

실행:  streamlit run football/app.py

라인업·부상·xG 자리는 비어 있다 — API-Football 키가 오면 채운다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

import epl

PAST = ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]
LONDON, SEOUL = ZoneInfo("Europe/London"), ZoneInfo("Asia/Seoul")

st.set_page_config(page_title="Chelsea · EPL", page_icon="🔵", layout="wide")

st.markdown("""<style>
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
</style>""", unsafe_allow_html=True)


# ─── 조각 ──────────────────────────────────────────────────────────────

def label(text):
    st.markdown(f"<div class='lbl'>{text}</div>", unsafe_allow_html=True)


def badges(rows):
    """최근 폼 — 왼쪽이 오래된 경기."""
    color = {"W": "var(--green)", "D": "var(--text-4)", "L": "var(--red)"}
    out = "".join(
        f"<span class='b' style='background:{color[r['res']]}' "
        f"title=\"{r['date']} {'vs' if r['home'] else '@'} {epl.short(r['opp'])} "
        f"{r['gf']}-{r['ga']}\">{r['res']}</span>"
        for r in reversed(rows)
    )
    return out or "<span class='dim'>경기 없음</span>"


def kickoff(match):
    lon = datetime.fromisoformat(f"{match['date']}T{match.get('time', '15:00')}")
    lon = lon.replace(tzinfo=LONDON)
    return lon, lon.astimezone(SEOUL)


def team_block(matches, team, tbl_row):
    """한 팀의 순위·승점·폼 카드."""
    r = tbl_row
    rank = f"{r['rank']}위" if r else "—"
    pts = f"{r['pts']}점" if r else "—"
    rec = f"{r['w']}승 {r['d']}무 {r['l']}패" if r else "기록 없음"
    gd = f"{r['gd']:+d}" if r else "—"
    return (
        f"<div class='card'>"
        f"<div style='font-size:19px;font-weight:600;margin-bottom:10px'>{epl.short(team)}</div>"
        f"<div class='num' style='font-size:13px;color:var(--text-2);margin-bottom:12px'>"
        f"{rank} · {pts} · {rec} · 득실 {gd}</div>"
        f"<div>{badges(epl.form(matches, team, 5))}</div>"
        f"</div>"
    )


# ─── 데이터 ────────────────────────────────────────────────────────────

season = epl.load_season(epl.CURRENT)
tbl = {r["team"]: r for r in epl.table(season)}
me = epl.CHELSEA

st.markdown("<div class='blk'>", unsafe_allow_html=True)
st.markdown(
    "<div style='display:flex;align-items:baseline;gap:12px;padding-top:8px'>"
    "<span style='font-size:26px;font-weight:700;letter-spacing:-.5px'>CHELSEA</span>"
    "<span class='dim' style='font-family:var(--mono);font-size:12px;letter-spacing:1.5px'>"
    f"PREMIER LEAGUE {epl.CURRENT}</span></div>",
    unsafe_allow_html=True,
)

# ── 다음 경기 ──
nxt = epl.next_match(season, me)
label("다음 경기")
if not nxt:
    st.markdown("<div class='card dim'>남은 경기가 없습니다.</div>", unsafe_allow_html=True)
else:
    lon, sel = kickoff(nxt)
    home = nxt["team1"] == me
    opp = nxt["team2"] if home else nxt["team1"]
    days = (sel.date() - datetime.now(SEOUL).date()).days
    when = "오늘" if days == 0 else ("내일" if days == 1 else f"D-{days}" if days > 0 else "진행/종료")

    st.markdown(
        f"<div class='card' style='border-left:3px solid var(--blue)'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px'>"
        f"<div>"
        f"<div style='font-size:23px;font-weight:600'>"
        f"{epl.short(nxt['team1'])} <span class='dim' style='font-size:15px'>vs</span> "
        f"{epl.short(nxt['team2'])}</div>"
        f"<div class='num' style='font-size:13px;color:var(--text-2);margin-top:6px'>"
        f"{nxt['round']} · {'홈' if home else '원정'} · "
        f"{sel:%m/%d(%a) %H:%M} 한국 <span class='dim'>({lon:%H:%M} 현지)</span></div>"
        f"</div>"
        f"<div class='num' style='font-size:30px;font-weight:700;color:var(--blue)'>{when}</div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    # ── 양팀 비교 ──
    label("맞대결 상대")
    c1, c2 = st.columns(2)
    c1.markdown(team_block(season, me, tbl.get(me)), unsafe_allow_html=True)
    c2.markdown(team_block(season, opp, tbl.get(opp)), unsafe_allow_html=True)

    # ── 상대 전적 ──
    label(f"상대 전적 · 최근 {len(PAST)}시즌")
    hist = epl.load_seasons(PAST)
    rec = epl.h2h(hist, me, opp)
    s = epl.h2h_summary(rec)
    if not rec:
        st.markdown(
            f"<div class='card dim'>{epl.short(opp)} 와(과) 최근 {len(PAST)}시즌 "
            f"프리미어리그 맞대결이 없습니다 (승격팀이거나 강등 기간).</div>",
            unsafe_allow_html=True,
        )
    else:
        rows = "".join(
            f"<tr><td class='num dim'>{r['season']}</td>"
            f"<td style='text-align:left'>{'홈' if r['home'] else '원정'}</td>"
            f"<td class='num'>{r['gf']}-{r['ga']}</td>"
            f"<td style='color:{'var(--green)' if r['res']=='W' else 'var(--red)' if r['res']=='L' else 'var(--text-3)'}'>"
            f"{r['res']}</td><td class='num dim'>{r['date']}</td></tr>"
            for r in rec
        )
        st.markdown(
            f"<div class='card'>"
            f"<div class='num' style='font-size:17px;margin-bottom:12px'>"
            f"{s['n']}경기 <span style='color:var(--green)'>{s['w']}승</span> "
            f"<span class='dim'>{s['d']}무</span> "
            f"<span style='color:var(--red)'>{s['l']}패</span>"
            f"<span class='dim' style='font-size:13px'> · 득실 {s['gf']}-{s['ga']}</span></div>"
            f"<table class='t'><tr><th>시즌</th><th style='text-align:left'>장소</th>"
            f"<th>스코어</th><th>결과</th><th>날짜</th></tr>{rows}</table></div>",
            unsafe_allow_html=True,
        )

    # ── 아직 없는 것 ──
    label("라인업 · 부상")
    st.markdown(
        "<div class='card dim' style='border-left:3px solid var(--amber)'>"
        "API-Football 키를 넣으면 채워집니다. 라인업은 킥오프 40분 전에 확정됩니다."
        "</div>",
        unsafe_allow_html=True,
    )

# ── 지난 경기 ──
label("지난 경기")
prev = epl.last_match(season, me)
if not prev:
    st.markdown("<div class='card dim'>아직 치른 경기가 없습니다.</div>", unsafe_allow_html=True)
else:
    gf, ga, res, opp_p, home_p = epl.result_for(prev, me)
    ht = prev.get("score", {}).get("ht") if isinstance(prev.get("score"), dict) else None
    tint = {"W": "var(--green)", "D": "var(--text-4)", "L": "var(--red)"}[res]
    st.markdown(
        f"<div class='card' style='border-left:3px solid {tint}'>"
        f"<div style='font-size:19px;font-weight:600'>"
        f"{epl.short(prev['team1'])} "
        f"<span class='num' style='color:{tint}'>{epl.ft(prev)[0]} - {epl.ft(prev)[1]}</span> "
        f"{epl.short(prev['team2'])}</div>"
        f"<div class='num' style='font-size:13px;color:var(--text-2);margin-top:6px'>"
        f"{prev['round']} · {prev['date']} · {'홈' if home_p else '원정'}"
        + (f" · 전반 {ht[0]}-{ht[1]}" if ht else "")
        + "</div></div>",
        unsafe_allow_html=True,
    )

# ── 순위표 ──
label("순위표")
rows = "".join(
    f"<tr style=\"{'background:rgba(46,111,232,.12)' if r['team'] == me else ''}\">"
    f"<td class='num dim'>{r['rank']}</td>"
    f"<td style='text-align:left;{'font-weight:600' if r['team'] == me else ''}'>"
    f"{epl.short(r['team'])}</td>"
    f"<td class='num'>{r['p']}</td><td class='num'>{r['w']}</td><td class='num'>{r['d']}</td>"
    f"<td class='num'>{r['l']}</td><td class='num dim'>{r['gf']}:{r['ga']}</td>"
    f"<td class='num'>{r['gd']:+d}</td>"
    f"<td class='num' style='font-weight:700'>{r['pts']}</td></tr>"
    for r in epl.table(season)
)
st.markdown(
    f"<div class='card'><table class='t'>"
    f"<tr><th>#</th><th style='text-align:left'>팀</th><th>경기</th><th>승</th><th>무</th>"
    f"<th>패</th><th>득실</th><th>±</th><th>승점</th></tr>{rows}</table></div>",
    unsafe_allow_html=True,
)

st.markdown(
    "<div class='dim' style='font-size:11px;font-family:var(--mono);margin:26px 0 40px'>"
    "SOURCE openfootball/football.json · 캐시 6시간</div></div>",
    unsafe_allow_html=True,
)
