# 워크플로: 옵시디언 동기화

## 목표
에이전트와 옵시디언 볼트를 **양방향**으로 연결한다.
- 우리 → 옵시디언: 에이전트 메모리 + stock-analyzer 결과(측정 리포트·매수 신호)를 노트로 기록
- 옵시디언 → 우리: 내가 볼트에 적은 관심종목(Watchlist)을 읽어 작업 입력으로 사용

## 도구
`tools/obsidian_bridge.py` (표준 라이브러리만, 서버 불필요)

## 볼트 위치
기본: `C:\Users\1aass\OneDrive\Desktop\ObsidianVault`
옵시디언에서 **Open folder as vault** 로 이 폴더를 열면 된다.
다른 곳에 두려면 환경변수 `OBSIDIAN_VAULT` 로 경로를 바꾼다(.env 참고).

## 볼트 구조
```
ObsidianVault/
├── Home.md                     # 시작 노트(링크 모음)
├── Watchlist.md                # ← 내가 관심종목을 적는 곳 (pull 입력)
├── Agent Memory/               # ← 에이전트 메모리 사본 (push 출력)
└── Stock Analyzer/
    ├── Signals.md              # ← 최근 매수 신호 표 (push 출력)
    ├── Scorecard.md            # ← 성적표 표본 현황 (push 출력)
    ├── Alpaca.md               # ← 실제 체결·잔고 (push 출력, 네트워크)
    └── Measurements/           # ← 측정 리포트 (push 출력)
```

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
폴더·시작 노트·Watchlist 템플릿을 만든다. 이미 있으면 건드리지 않는다.

### 2) 우리 → 옵시디언 (결과 밀어넣기)
```
python tools/obsidian_bridge.py push
```
메모리 `*.md`, `stock-analyzer/docs/measurements/*.md`, `signal_log.json`(→ Signals.md 표)를
볼트로 복사/렌더한다. **덮어쓰기 방식** — `Agent Memory/`, `Stock Analyzer/` 안 노트는
직접 고쳐도 다음 push 때 덮어써진다(각 노트 상단에 표식 있음).

### 3) 옵시디언 → 우리 (관심종목 읽기)
`Watchlist.md` 에 `- TICKER` 형식으로 적은 뒤:
```
python tools/obsidian_bridge.py pull
```
→ `{"tickers": ["AAPL","NVDA","005930.KS"], "count": 3}` 처럼 JSON 출력.
이 목록을 stock-analyzer 스캔의 `UNIVERSE` 로 넘겨 분석하면 된다.

### 4) 읽은 결과를 옵시디언에서 보기 (analyze)
```
python tools/obsidian_bridge.py analyze
```
Watchlist 종목을 읽어 **각 종목의 트레이드 플랜(방향·진입·손절·목표·R:R·확신도)**
을 계산하고, 볼트에 **`관심종목 분석.md`** 노트로 쓴다. 이게 "읽은 결과를 보는 곳"이다.
stock-analyzer 모듈과 가격 조회(네트워크)를 쓰므로 **같은 파이썬 환경**에서 실행한다.

## 경계 / 주의
- **Watchlist 는 사람이 쓰는 입력**이라 push 가 덮어쓰지 않는다. 나머지 관리 노트는 출력이라 덮어쓴다.
- 메모리 동기화는 **단방향(우리→볼트)** 이다. 볼트에서 메모리 노트를 고쳐도 `.claude` 원본에 되반영되지 않는다(충돌·오작동 방지). 메모리 수정은 에이전트에게 말로 지시.
- OneDrive 안 폴더라 자동 백업된다. 옵시디언 동기화 플러그인과 겹치지 않게 한 쪽만 쓴다.

## 고장나면
- `측정 폴더 없음` / `신호 로그 없음` → `STOCK_DIR` 경로 확인(.env).
- 볼트가 엉뚱한 곳에 생김 → `OBSIDIAN_VAULT` 환경변수 확인.
- pull 이 티커를 못 읽음 → Watchlist 에서 `- ` (하이픈+공백)으로 시작하는지 확인.
