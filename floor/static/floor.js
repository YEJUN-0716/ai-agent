/* 화면 담당. 서버가 보낸 사건을 큐에 넣고 천천히 재생한다.
   데모에서는 사건이 한꺼번에 도착하므로, 연출 속도는 전적으로 여기서 만든다. */

const $ = (id) => document.getElementById(id);
const PARAMS = new URLSearchParams(location.search);
const DEMO = PARAMS.get("demo") === "1";

/* ── 픽셀 캐릭터 ────────────────────────────────── */
// o 외곽선 · h 머리 · s 피부 · e 눈 · m 입 · c 옷
const SPRITE = [
  "................",
  ".....oooooo.....",
  "....ohhhhhhho...",
  "...ohhhhhhhhho..",
  "...ohsssssssho..",
  "...ohsesssesho..",
  "...ohsssssssho..",
  "...ohssmmmssho..",
  "....ohssssssho..",
  "....oosssssoo...",
  "..occccccccco...",
  ".occccccccccco..",
  ".ocsccccccccsco.",
  ".occccccccccco..",
  "..occccccccco...",
  "..oo......oo....",
];

const SKIN = "#f0c9a4";
const LOOKS = {
  TARO:    { h: "#5aa9ff", c: "#2b3f66" },
  DIANA:   { h: "#f4c04a", c: "#5a3f7a" },
  NOVA:    { h: "#4fe3d0", c: "#1f5a52" },
  VIBE:    { h: "#ff8ad0", c: "#6d2b57" },
  BULL:    { h: "#41d67f", c: "#14512f" },
  BEAR:    { h: "#ff5d6c", c: "#5c1d24" },
  BLITZ:   { h: "#ffb347", c: "#7a3c0f" },
  GUARD:   { h: "#9aa6bd", c: "#2f3a4f" },
  RISKY:   { h: "#ff6b3d", c: "#6b2412" },
  SAFE:    { h: "#7fd4ff", c: "#1d3f56" },
  NEUTRAL: { h: "#d6dcea", c: "#3a4257" },
  ACE:     { h: "#a98bff", c: "#3b2a6b" },
  PM:      { h: "#f4c04a", c: "#6b5410" },
};

function drawSprite(canvas, look) {
  const g = canvas.getContext("2d");
  const colors = { o: "#0a0c12", h: look.h, s: SKIN, e: "#0a0c12", m: "#b5654a", c: look.c };
  g.clearRect(0, 0, 16, 16);
  SPRITE.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      const color = colors[row[x]];
      if (!color) continue;
      g.fillStyle = color;
      g.fillRect(x, y, 1, 1);
    }
  });
}

/* ── 8비트 차트 ─────────────────────────────────── */
function drawChart(q) {
  const canvas = $("chart");
  const g = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, PAD = 6;
  g.clearRect(0, 0, W, H);
  const closes = q.closes || [];
  if (closes.length < 2) return;

  const values = closes.concat(q.ma50.filter((v) => v !== null));
  const lo = Math.min(...values, q.low20), hi = Math.max(...values, q.high20);
  const span = hi - lo || 1;
  const y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
  const x = (i) => PAD + (i / (closes.length - 1)) * (W - PAD * 2);

  // 20봉 고·저 점선
  g.fillStyle = "#3d4761";
  [q.high20, q.low20].forEach((level) => {
    for (let px = PAD; px < W - PAD; px += 8) g.fillRect(px, Math.round(y(level)), 4, 1);
  });

  // 봉 하나가 x 간격만큼 넓은 블록이다. 점으로 찍으면 차트가 아니라
  // 흩뿌린 자국처럼 보여서, 계단 모양으로 이어 붙인다.
  const step = (W - PAD * 2) / (closes.length - 1);
  const stair = (series, color, thick) => {
    g.fillStyle = color;
    series.forEach((v, i) => {
      if (v === null) return;
      g.fillRect(Math.round(x(i)), Math.round(y(v)) - thick / 2, Math.ceil(step) + 1, thick);
    });
  };
  stair(closes, q.change_pct >= 0 ? "#41d67f" : "#ff5d6c", 4);
  stair(q.ma20, "#f4c04a", 2);
  stair(q.ma50, "#5aa9ff", 2);

  // 날짜 대신 봉 번호 라벨 — 데모 시세에는 실제 날짜가 없다
  g.fillStyle = "#5b6480";
  g.font = "9px monospace";
  g.fillText(`-${closes.length}봉`, PAD, H - 1);
  g.fillText("현재", W - PAD - 22, H - 1);
}

/* ── 값 표시 ────────────────────────────────────── */
const money = (v, cur) =>
  cur === "KRW"
    ? `${Math.round(v).toLocaleString("ko-KR")}원`
    : `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");

/* ── 상태 ───────────────────────────────────────── */
let MODES = [], AGENTS = [], mode = "algo", running = false;
let currency = "KRW", track = "all", lastBriefs = {};

/* ── 좌석 그리기 ────────────────────────────────── */
function buildSeats() {
  document.querySelectorAll(".room").forEach((room) => {
    const key = room.dataset.room;
    const seats = room.querySelector(".seats");
    seats.innerHTML = "";
    AGENTS.filter((a) => a.room === key).forEach((a) => {
      const box = document.createElement("div");
      box.className = "agent idle";
      box.id = `seat-${a.key}`;
      box.title = `${a.name} · ${a.role} — ${a.watches}`;
      const canvas = document.createElement("canvas");
      canvas.width = 16; canvas.height = 16;
      box.appendChild(canvas);
      const who = document.createElement("div");
      who.className = "who";
      who.textContent = a.key;
      box.appendChild(who);
      seats.appendChild(box);
      drawSprite(canvas, LOOKS[a.key] || { h: "#8892a8", c: "#3a4257" });
    });
  });
}

function applyRoster(roster) {
  AGENTS.forEach((a) => {
    const seat = $(`seat-${a.key}`);
    if (!seat) return;
    seat.classList.toggle("idle", !roster.includes(a.key));
    seat.querySelectorAll(".bubble,.thinking").forEach((n) => n.remove());
    seat.classList.remove("talking", "busy");
  });
  $("riskTitle").textContent = roster.includes("BLITZ") ? "스캘핑 데스크" : "리스크 위원회";
}

/* ── 콘솔 ───────────────────────────────────────── */
function scrollLog() {
  const log = $("log");
  log.scrollTop = log.scrollHeight;
}

function line(text, cls) {
  const el = document.createElement("div");
  el.className = `ln ${cls || ""}`;
  el.dataset.track = "system";
  el.textContent = text;
  $("log").appendChild(el);
  applyTrack();
  scrollLog();
  return el;
}

// 한 글자씩 흘려 넣는다. 브리핑 전문은 길어서 덩어리로 끊는다.
//
// 타이핑 중에는 글자 그대로 넣고, 다 친 뒤에 한 번만 마크다운을 입힌다.
// 덩어리로 끊으면 **강조** 짝이 조각나서 별표가 그대로 보인다.
function type(el, text, chunk, tick) {
  return new Promise((done) => {
    let i = 0;
    const typed = document.createElement("span");
    const cursor = document.createElement("span");
    cursor.className = "cursor";
    cursor.textContent = " ";
    el.append(typed, cursor);
    const step = () => {
      i += chunk;
      typed.append(text.slice(i - chunk, i));
      scrollLog();
      if (i < text.length) { setTimeout(step, tick); return; }
      cursor.remove();
      el.innerHTML = md(text);
      done();
    };
    step();
  });
}

async function briefing(ev) {
  const box = document.createElement("div");
  box.className = `brief ${ev.track === "debate" ? "debate" : ""}`;
  box.dataset.track = ev.track;
  const turn = ev.turn ? ` · ${ev.turn}턴` : "";
  box.innerHTML =
    `<div><span class="name">${esc(ev.agent)}</span> ` +
    `<span class="role">${esc(ev.role)}${turn}</span></div>`;
  const body = document.createElement("div");
  body.className = "txt";
  box.appendChild(body);
  $("log").appendChild(box);
  applyTrack();
  await type(body, ev.body, 6, 8);
}

function applyTrack() {
  $("log").querySelectorAll("[data-track]").forEach((el) => {
    el.hidden = track !== "all" && el.dataset.track !== track;
  });
}

/* ── 말풍선 ─────────────────────────────────────── */
function showThinking(key) {
  const seat = $(`seat-${key}`);
  if (!seat) return;
  seat.classList.add("busy");
  seat.querySelectorAll(".bubble,.thinking").forEach((n) => n.remove());
  const tag = document.createElement("div");
  tag.className = "thinking";
  tag.textContent = "···";
  seat.appendChild(tag);
}

function showBubble(ev) {
  const seat = $(`seat-${ev.agent}`);
  lastBriefs[ev.agent] = ev;
  if (!seat) return;
  seat.classList.remove("busy");
  seat.classList.add("talking");
  seat.querySelectorAll(".bubble,.thinking").forEach((n) => n.remove());
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML =
    (ev.turn ? `<span class="turn">${ev.turn}턴</span>` : "") + esc(ev.bubble);
  bubble.onclick = () => openBrief(ev.agent);
  seat.appendChild(bubble);
}

function openBrief(key) {
  const ev = lastBriefs[key];
  if (!ev) return;
  $("modalTitle").textContent = `${ev.agent} · ${ev.name} — ${ev.role}`;
  $("modalBody").innerHTML = md(ev.body);
  $("modal").hidden = false;
}

/* ── 전광판 ─────────────────────────────────────── */
function paintQuote(q) {
  currency = q.currency;
  $("tkSym").textContent = `${q.label} (${q.symbol})`;
  $("tkPrice").textContent = money(q.price, q.currency);
  $("tkPrice").classList.toggle("down", q.change_pct < 0);
  $("tkChg").textContent =
    `${q.change_pct >= 0 ? "▲" : "▼"} ${Math.abs(q.change_pct).toFixed(2)}%  ·  공포탐욕 ${q.fear_greed}`;
  $("chartSrc").textContent =
    q.source === "demo" ? "시세 출처: 데모 합성값 (실측 아님)" : `시세 출처: ${q.source}`;

  const badges = [
    q.market_open
      ? '<span class="badge open">MARKET OPEN</span>'
      : '<span class="badge shut">MARKET CLOSED</span>',
    `<span class="badge">무기한 변동성 ${q.perp_vol_pct.toFixed(2)}%</span>`,
    `<span class="badge">원화 변동성 ${q.krw_vol_pct.toFixed(2)}%</span>`,
  ];
  if (q.source === "demo") badges.push('<span class="badge demo">DEMO</span>');
  $("tkBadges").innerHTML = badges.join("");

  drawChart(q);

  const board = $("board");
  if (!q.board || !q.board.length) { board.hidden = true; return; }
  board.hidden = false;
  const rows = q.board.map((r) => {
    const funding = r.estimated
      ? '<span class="est">추정 · 직접 조회 불가</span>'
      : `${r.funding_pct.toFixed(4)}%`;
    const gap = `${r.gap_pct >= 0 ? "+" : ""}${r.gap_pct.toFixed(2)}%`;
    return `<tr><td>${esc(r.exchange)}${r.estimated ? " *" : ""}</td>` +
      `<td>${r.perp_usdt}</td><td>${funding}</td><td>${gap}</td></tr>`;
  }).join("");
  board.innerHTML =
    `<tr><th>거래소</th><th>무기한(USDT)</th><th>펀딩</th><th>KRX 괴리</th></tr>${rows}` +
    `<tr><td colspan="4" class="est">* 탭비트는 API 직접 조회가 막혀 4개 거래소 평균으로 ` +
    `추정합니다. 주문 전에는 거래소 화면을 직접 확인하세요. 환율 ${q.fx_krw ?? "—"}원</td></tr>`;
}

/* ── 판정 패널 ──────────────────────────────────── */
function paintVerdict(v) {
  const act = $("vAction");
  act.textContent = v.action;
  act.className = `v-action ${v.action.toLowerCase()}`;
  $("vGauge").style.width = `${v.confidence}%`;
  $("vConf").textContent = `확신도 ${v.confidence}%  ·  기준 ${v.basis}`;
  $("vEntry").textContent = money(v.entry, currency);
  $("vStop").textContent = money(v.stop, currency);
  $("vTarget").textContent = money(v.target, currency);
  $("vSize").textContent = `${v.size_pct}%`;
  const pm = $("vPm");
  pm.hidden = !v.pm_status;
  if (v.pm_status) pm.textContent = `PM ${v.pm_status} — ${v.pm_comment}`;
  $("vWhy").textContent = v.rationale;
}

/* ── 사건 재생 ──────────────────────────────────── */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let queue = [], draining = false, seen = 0, total = 0;

async function drain() {
  if (draining) return;
  draining = true;
  while (queue.length) await play(queue.shift());
  draining = false;
}

async function play(ev) {
  switch (ev.type) {
    case "meta":
      total = ev.calls;
      applyRoster(ev.roster);
      $("tkBasis").textContent = `판정 기준 시장 ${ev.basis}`;
      line(`▶ ${ev.label} · ${ev.mode_label} · 클로드 호출 ${ev.calls}회 예정`, "head");
      break;
    case "quote":
      paintQuote(ev);
      break;
    case "log":
      line(ev.text, ev.text.startsWith("■") ? "head" : (ev.text.includes("⚠") ? "warn" : ""));
      await sleep(45);
      break;
    case "phase": {
      document.querySelectorAll(".room").forEach((r) => r.classList.remove("active"));
      const room = document.querySelector(`.room[data-room="${ev.room}"]`);
      if (room) room.classList.add("active");
      line(`■ ${ev.label}`, "head");
      await sleep(320);
      break;
    }
    case "thinking":
      showThinking(ev.agent);
      await sleep(420);
      break;
    case "agent":
      showBubble(ev);
      seen += 1;
      $("progress").textContent = `${seen}/${total}`;
      await briefing(ev);
      await sleep(160);
      break;
    case "verdict":
      paintVerdict(ev);
      line(`■ 최종 판정 ${ev.action} · 확신도 ${ev.confidence}%`, "head");
      await sleep(300);
      break;
    case "saved": {
      const toast = $("toast");
      toast.hidden = false;
      toast.textContent = `리포트 저장됨 — ${ev.name}`;
      toast.onclick = () => window.open(ev.url, "_blank");
      break;
    }
    case "error":
      line(`⚠ ${ev.text}`, "warn");
      break;
    case "done":
      document.querySelectorAll(".room").forEach((r) => r.classList.remove("active"));
      break;
  }
}

/* ── 실행 ───────────────────────────────────────── */
async function finish() {
  while (draining || queue.length) await sleep(120);
  running = false;
  $("run").disabled = false;
}

function analyze(symbol) {
  if (running) return;
  if (!symbol) { line("⚠ 종목을 입력해 주세요. 예: BTC · TSLA · 하이닉스", "warn"); return; }
  running = true;
  $("run").disabled = true;
  $("log").innerHTML = "";
  $("toast").hidden = true;
  queue = []; seen = 0; total = 0; lastBriefs = {};
  $("progress").textContent = "";

  const url = `/api/analyze?symbol=${encodeURIComponent(symbol)}&mode=${mode}&demo=${DEMO ? 1 : 0}`;
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    queue.push(ev);
    drain();
    if (ev.type === "done" || ev.type === "error") {
      source.close();
      finish();
    }
  };
  source.onerror = () => {
    source.close();
    if (!seen) line("⚠ 서버와 연결이 끊겼습니다. 서버 창이 켜져 있는지 확인하세요.", "warn");
    finish();
  };
}

/* ── 초기화 ─────────────────────────────────────── */
function paintModeNote() {
  const m = MODES.find((x) => x.key === mode);
  if (!m) return;
  const warn = m.key === "attack"
    ? ' <span class="warn">— 확신도가 50%대면 동전 던지기와 같습니다. 방향보다 손절 레벨을 보세요.</span>'
    : "";
  const live = DEMO
    ? ""
    : ` <span class="warn">— 실전 모드입니다. 실제 시세를 조회하고 클로드를 ${m.calls}번 부릅니다. 화면만 볼 거면 주소 끝에 ?demo=1 을 붙이세요.</span>`;
  $("modeNote").innerHTML =
    `판정 기준 시장 <b>${esc(m.basis)}</b> · 가능한 판정 ${esc(m.actions.join(" / "))} · ` +
    `${esc(m.note)}${warn}${live}`;
}

function buildModeButtons() {
  const wrap = $("modes");
  wrap.innerHTML = "";
  MODES.forEach((m) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${m.label} (${m.calls}회)`;
    b.className = m.key === mode ? "on" : "";
    b.onclick = () => { mode = m.key; buildModeButtons(); paintModeNote(); };
    wrap.appendChild(b);
  });
}

$("controls").addEventListener("submit", (e) => {
  e.preventDefault();
  analyze($("symbol").value.trim());
});
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("on"));
    tab.classList.add("on");
    track = tab.dataset.track;
    applyTrack();
  };
});
$("modalClose").onclick = () => ($("modal").hidden = true);
$("modal").onclick = (e) => { if (e.target === $("modal")) $("modal").hidden = true; };

fetch("/api/meta")
  .then((r) => r.json())
  .then((data) => {
    MODES = data.modes;
    AGENTS = data.agents;
    buildSeats();
    buildModeButtons();
    paintModeNote();
    line(DEMO
      ? "데모 모드입니다. 클로드를 부르지 않고 준비된 응답으로 전 과정을 재생합니다. 시세는 합성값입니다."
      : "실전 모드입니다. ▶ ANALYZE 를 누르면 실제 시세를 조회하고 클로드가 한 명씩 답합니다. 한 판에 몇 분 걸립니다.");
  })
  .catch(() => line("⚠ 서버 정보를 읽지 못했습니다.", "warn"));
