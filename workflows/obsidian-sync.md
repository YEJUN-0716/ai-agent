# 워크플로: 옵시디언 동기화

## 목표
에이전트의 결과를 옵시디언 볼트에 **단방향**으로 기록한다.
에이전트 메모리 + stock-analyzer 결과(측정 리포트·매수 신호·성적표·Alpaca 체결)를 노트로 남긴다.

**볼트는 읽는 곳이다.** 관심종목을 볼트에서 읽어 오던 `pull`/`analyze` 는 뺐다(2026-08-11).
관심종목 입력은 비서(텔레그램·디스코드)가 `data/assistant/` 에서 관리한다 —
같은 목록을 두 곳에서 받으면 어느 쪽이 진짜인지 모르게 된다.

## 도구
`tools/obsidian_bridge.py` (표준 라이브러리만, 서버 불필요)

## 볼트 위치
기본: `C:\Users\1aass\OneDrive\Desktop\ObsidianVault`
옵시디언에서 **Open folder as vault** 로 이 폴더를 열면 된다.
다른 곳에 두려면 환경변수 `OBSIDIAN_VAULT` 로 경로를 바꾼다(.env 참고).

## 볼트 구조
```
ObsidianVault/
├── Home.md                     # ← 현황판 (push 가 매번 다시 그림)
├── Agent Memory/
│   ├── MEMORY.md               # ← 목차, 규칙/측정/진행 세 묶음 (push 출력)
│   └── *.md                    # ← 에이전트 메모리 사본
├── Stock Analyzer/
│   ├── Signals.md              # ← 최근 매수 신호 표 (push 출력)
│   ├── Scorecard.md            # ← 성적표 표본 현황 (push 출력)
│   ├── Alpaca.md               # ← 실제 체결·잔고 (push 출력, 네트워크)
│   ├── Measurements.md         # ← 날짜·주제·판정 목록 (push 출력)
│   └── Measurements/           # ← 측정 리포트 원본
└── YouTube/
    ├── YouTube.md              # ← 편별 목차 (push 출력)
    └── EP0N 대본/녹음용.md      # ← content/ep*/ 사본
```

**목록 노트가 요점이다.** 리포트 33개·메모리 40개를 파일명만 보고는 못 고른다.
`Measurements.md` 는 각 리포트의 `## 판정:` **제목 줄만** 읽어 통과/실패/미측정을
표로 뽑는다. 절 본문까지 훑으면 판정표 머리글 "통과선" 이 '통과'로 읽혀 **실패한
측정이 통과로 뒤집힌다** — 실제로 한 번 그렇게 났다(2026-08-17). 본문에 서술로
적은 초기 리포트는 한 낱말로 못 줄이니 `📄 서술형` 으로만 표시하고 요약하지 않는다.

**Alpaca 체결 기록은 장부를 따로 만들지 않는다.** 진짜 기록은 브로커에 있고,
push 할 때마다 계좌·포지션·최근 체결 100건을 받아 다시 그린다. 옆에서 받아 적으면
주문이 거절되거나 부분체결된 날 두 장부가 갈라지고, 그때 어느 쪽이 맞는지 알 수 없다.

키는 `stock-analyzer/.env` 에서 읽는다(환경변수가 있으면 그쪽이 우선). 사본을
한 곳에 두려는 것 — 두 곳에 두면 로테이션한 날 한쪽만 바뀐다.

## 사용법

### 1) 최초 1회 — 볼트 만들기
```
python tools/obsidian_bridge.py init
```
폴더와 시작 노트를 만든다. 이미 있으면 건드리지 않는다.

### 2) 우리 → 옵시디언 (결과 밀어넣기)
```
python tools/obsidian_bridge.py push
```
메모리 `*.md`, `stock-analyzer/docs/measurements/*.md`, `signal_log.json`(→ Signals.md 표),
`content/ep*/script.md`·`narration.md`(→ YouTube/)를 볼트로 복사/렌더하고 목록 노트와
Home 을 다시 그린다. **덮어쓰기 방식** — `Agent Memory/`, `Stock Analyzer/`, `YouTube/`
안 노트와 `Home.md` 는 직접 고쳐도 다음 push 때 덮어써진다(각 노트 상단에 표식 있음).

### 3) 무인 갱신 (매시간)
작업 스케줄러 `ObsidianSync` 가 `tools/obsidian_sync.cmd` → `sync` 를 매시간 부른다.
`sync` 는 stock-analyzer 를 원격에 맞춘 뒤 push 한다. 로그는 `.tmp/obsidian_sync.log`.

## 경계 / 주의
- 볼트의 관리 노트는 전부 **출력**이라 push 가 덮어쓴다. 볼트에서 고쳐도 남지 않는다.
- 메모리 동기화는 **단방향(우리→볼트)** 이다. 볼트에서 메모리 노트를 고쳐도 `.claude` 원본에 되반영되지 않는다(충돌·오작동 방지). 메모리 수정은 에이전트에게 말로 지시.
- OneDrive 안 폴더라 자동 백업된다. 옵시디언 동기화 플러그인과 겹치지 않게 한 쪽만 쓴다.

## 고장나면
- `측정 폴더 없음` / `신호 로그 없음` → `STOCK_DIR` 경로 확인(.env).
- 볼트가 엉뚱한 곳에 생김 → `OBSIDIAN_VAULT` 환경변수 확인.
- `Alpaca 키 없음` → `stock-analyzer/.env` 에 키가 있는지 확인. 그 노트만 건너뛰고 나머지는 갱신된다.
