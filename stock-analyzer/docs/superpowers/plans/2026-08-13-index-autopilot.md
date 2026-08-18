# 자동 인덱스 운용 시스템(A) — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**설계서:** `docs/superpowers/specs/2026-08-13-index-autopilot-design.md` (PR #102)

**Goal:** 매달 정해진 금액을 ITOT 70 / AGG 20 / GLDM 10 에 정수주로 넣고 절대 팔지 않는 시스템을, 사람 손 안 대고 12개월 돌게 만든다. 이기는 게 목표가 아니라 **안 새는 것**이 목표다.

**Architecture:** 규칙은 순수 함수 하나(`plan_orders`)에 전부 넣고, 체결·장부·환율·텔레그램은 이미 있는 것을 그대로 쓴다. 러너는 **매월 1~8일 매일 깨어나** 장부를 보고 이번 달 할 일이 남았는지 스스로 판단한다(멱등).

**Tech Stack:** Python 3.14 · pandas · yfinance · requests — **새 의존성 없음**

---

## Global Constraints

- **브랜치 `feat/index-autopilot`. main 직접 커밋 금지.**
- **`requirements.txt` 를 건드리지 않는다.**
- **기존 441 그린이 유지돼야 한다** (`pytest -q`). 공유 코드(`virtual_broker`)를 건드리는 Task 2 는 기존 호출자 동작이 **글자 그대로 같아야** 한다.
- **공개 성적표를 오염시키지 않는다.** 인덱스 장부는 `VIRTUAL_PORTFOLIO_FILE=index_portfolio.json`. 기존 `virtual_portfolio.json` 에 인덱스 매수가 한 건이라도 섞이면 두 시스템의 성적을 영영 분리할 수 없다.
- **매도 코드를 한 줄도 쓰지 않는다.** 리밸런싱 매도·손절·익절 전부 없다.
- **비중 70/20/10 을 백테스트로 조정하지 않는다.** 측정 결과가 나빠도 그대로 둔다(설계서 3.2).

## 실측으로 확인한 전제 (읽고 시작할 것)

코드를 읽고 확인한 것들이다. 틀린 가정 위에 짜면 조용히 어긋난다.

1. **`STATE_FILE` 은 import 시점에 결정된다** — `virtual_broker.py:28` 이 모듈 최상단에서 `os.environ.get(...)` 을 읽는다. 환경변수를 `import` **뒤에** 세팅하면 스윙 장부에 쓴다. 러너는 반드시 워크플로 env 로 넘긴다.
2. **`load_state()` 는 모르는 키를 조용히 버린다** — `virtual_broker.py:114` 가 `_empty_state()` 에 있는 키만 남긴다. 새 상태를 저장하려면 `_empty_state()` 에 키를 **먼저** 추가해야 한다. 안 그러면 저장은 되는데 다음 실행에서 사라진다.
3. **시장가 매수 경로가 이미 있다** — `place_notional_buy(symbol, notional_usd, market="US")` 가 `kind` 없는 주문을 넣고, `settle_pending` 이 다음 거래일 **시가**로 체결한다. 새 브로커 코드를 짜지 않는다.
4. **`place_notional_buy` 는 내부에서 `load_state()` 를 다시 한다** — 러너가 메모리에서 고친 state 를 저장하지 않고 이 함수를 부르면 그 수정이 통째로 날아간다. **입금·배당 반영 → `save_state` → 주문** 순서를 지킨다.
5. **이월 현금에 새 장치가 필요 없다** — `cash_krw` 가 하나의 풀이고 `_fill_buy` 는 실제 체결액만 뺀다. 안 쓴 돈은 그냥 남는다. 그게 이월이다.
6. **수수료는 브로커에 없다** — `_fill_buy` 는 수수료를 안 뗀다. 여기에 수수료를 넣으면 **스윙 공개 성적표의 과거 숫자가 바뀐다.** 비용은 전부 러너에서 처리한다.
7. **`etf_panel.parquet` 에 ITOT·GLDM·GLD 가 없다** (현재 AGG·DBC·EFA·IEF·SPY·VNQ, 2003~2026). `load_panel()` 이 없는 티커를 받아 붙인다.

---

### Task 1: 규칙 — `modules/index_autopilot.py`

설계서 4장의 규칙 전부. 순수 함수다. 파일도 네트워크도 안 건드린다.

**Files:**
- Create: `modules/index_autopilot.py`
- Create: `tests/test_index_autopilot.py`

**Interfaces:**
- Produces:
  - `TARGETS: dict` — `{"ITOT": 0.70, "AGG": 0.20, "GLDM": 0.10}`
  - `plan_orders(holdings: dict, prices: dict, cash_usd: float, targets: dict = TARGETS) -> list[dict]`
    반환 `[{"ticker": str, "qty": int, "est_price": float}, ...]` — `qty` 는 항상 **양의 정수**. 부족분이 큰 순서.
  - `demo() -> None` — assert 기반 자체검사. 프레임워크 없음.

**규칙(설계서 4장 그대로):**

```
목표총액 = sum(holdings[t] * prices[t]) + cash_usd
부족분[t] = 목표총액 * targets[t] - holdings[t] * prices[t]

부족분 큰 순서로:
    살 수 있는 최대 정수주 = min(부족분[t], 남은현금) // prices[t]
    0 이면 건너뛴다 (매도 없음, 이월)
    남은현금 -= 수량 * prices[t]
```

- [ ] **Step 1: 실패하는 테스트를 쓴다** — `tests/test_index_autopilot.py`

`demo()` 를 부르는 테스트 한 줄 + 설계서 9장의 검증 항목을 그대로 옮긴 테스트:

```python
def test_demo_self_check():
    index_autopilot.demo()          # assert 가 안 터지면 통과

def test_never_sells():             # 한 자산이 목표를 크게 넘겨도 qty 는 전부 양수
def test_integer_shares_only():     # 모든 qty 가 int, 소수 없음
def test_never_exceeds_cash():      # sum(qty*price) <= cash_usd
def test_largest_gap_first():       # 부족분 1위가 첫 주문
def test_skips_when_share_costs_more_than_cash():
    # GLDM 목표 $71 < 1주 $87 → GLDM 주문 없음, 현금 이월
def test_crash_makes_asset_first_priority():
    # ITOT 반토막 → ITOT 이 우선순위 1위
def test_targets_must_sum_to_one():  # 합이 1.0 이 아니면 ValueError
```

- [ ] **Step 2: 통과시킨다** — `modules/index_autopilot.py`
- [ ] **Step 3: 검증** — `pytest -q tests/test_index_autopilot.py` 그린, `python -m modules.index_autopilot` 로 `demo()` 통과
- [ ] **Step 4: 커밋** — `feat: 인덱스 자동운용 규칙 — 부족한 것부터 정수주로 산다`

---

### Task 2: 가상 브로커 최소 확장 (2곳)

공유 코드를 건드리는 유일한 Task 다. **기존 동작이 바뀌면 안 된다.**

**Files:**
- Modify: `modules/virtual_broker.py`
- Modify: `tests/test_virtual_broker.py` (있으면; 없으면 생성)

**바꾸는 것 — 딱 둘:**

1. **`_empty_state()` 에 `"index_meta": {}` 추가** (전제 2 때문). 인덱스 러너의 멱등성 기록이 여기 산다:
   ```
   {"last_deposit_month": "2026-09", "last_report_month": "2026-09",
    "dividends": {"AGG": "2026-08-01"}, "fees_krw": 12345.0,
    "deposited_krw": 1000000.0}
   ```
   스윙 장부에는 빈 dict 로 남는다(무해).

   > 왜 별도 파일이 아닌가: 입금 기록과 장부가 두 파일로 갈리면 한쪽만 써진 순간 **입금이 두 번 들어가거나 한 달이 빠진다.** 중복 입금은 성적을 조용히 위조하고 되돌릴 수 없다. 진실의 출처를 하나로 둔다.

2. **`_fill_buy` 가 주문에 `qty` 가 있으면 그 수량을 쓴다:**
   ```python
   qty = int(order["qty"]) if order.get("qty") else int(order["notional_krw"] // price_krw)
   ```
   그리고 `place_notional_buy` 에 `qty: int | None = None` 인자를 추가해 주문 dict 에 실어 보낸다.

   > 왜 필요한가: 금액만 넘기면 체결 시가가 예상보다 낮을 때 계획보다 **더** 산다. 그 초과분은 다른 자산 몫의 현금이라 비중이 계획과 어긋나고, "계획한 주문이 그대로 체결됐는가"를 못 잰다.
   >
   > **기존 호출자는 전부 `qty` 를 안 넘긴다** — 러너(`paper_trade_runner_toss.py`)·비서(`AI AGENT/tools/virtual_trade.py:153` 의 `buy()`) 확인함. 기본값 `None` 이면 기존 경로와 글자 그대로 같다.

- [ ] **Step 1: 기존 동작 불변 테스트** — `qty` 없는 주문이 예전과 같은 수량으로 체결되는지, `index_meta` 없는 예전 `virtual_portfolio.json` 이 그대로 열리는지
- [ ] **Step 2: 새 동작 테스트** — `qty=3` 을 넘기면 시가가 낮아도 정확히 3주, 현금 부족이면 0주
- [ ] **Step 3: 구현**
- [ ] **Step 4: 검증 — `pytest -q` 전체 441 그린** (여기가 이 Task 의 진짜 관문)
- [ ] **Step 5: 커밋** — `feat: 가상 브로커 — 수량 지정 매수와 index_meta 칸`

---

### Task 3: 러너 — `index_runner.py`

월 1회 적립을 실행한다. **매일 깨어나도 결과가 같아야 한다.**

`paper_trade_runner_toss.py` 에 넣지 않는다 — 규칙이 다른 두 시스템을 한 파일에 넣으면 성적이 섞인다(설계서 5.1).

**Files:**
- Create: `index_runner.py`
- Create: `tests/test_index_runner.py`

**실행 순서 (전제 4 때문에 순서가 규칙이다):**

```
1. fx = fx.fetch_krw_per_usd();  virtual_broker.set_fx(fx)
2. state = load_state()
   state = settle_pending(state, fx)            # 지난번 주문부터 체결
3. 배당 반영: 보유 종목마다 yfinance Ticker.dividends 를
   index_meta["dividends"][ticker] 이후 것만 → 세후 85% → cash_krw 에 더함
   → index_meta["dividends"][ticker] 갱신
4. save_state(state)                            # ★ 반드시 여기서 저장
5. 이번 달 적립 안 했으면 (index_meta["last_deposit_month"] != YYYY-MM):
     입금: cash_krw += INDEX_MONTHLY_KRW
     환전: cash_usd = 적립금/fx * (1 - FX_SPREAD_BP/10000)  ← 환전비용
     주문용 현금 = cash_usd * (1 - FEE_BP/10000)
     prices = last_close_price(t) for t in TARGETS
     orders = plan_orders(holdings, prices, 주문용현금)
     save_state(state)                          # ★ 주문 전에 저장
     for o in orders: place_notional_buy(o.ticker, o.qty*o.est_price,
                                         qty=o.qty, market="US")
     수수료 = sum(주문금액) * FEE_BP/10000 을 cash_krw 에서 차감,
       index_meta["fees_krw"] 에 누적
     index_meta["last_deposit_month"] = YYYY-MM
6. 보고: pending 이 비었고(=체결 끝) 이번 달 적립을 했고
   index_meta["last_report_month"] != YYYY-MM 이면
     → 메시지 만들어 공개 채널 발송 → last_report_month 갱신
```

**환경변수 (전부 기본값 있음):**

| 변수 | 기본 | 설명 |
|---|---|---|
| `VIRTUAL_PORTFOLIO_FILE` | — | **`index_portfolio.json` 필수.** 안 주면 스윙 장부를 오염시킨다 |
| `VIRTUAL_CAPITAL_KRW` | `0` | 시작 자본 0원 (설계서 11장) |
| `INDEX_MONTHLY_KRW` | `1000000` | 월 적립금 |
| `INDEX_FX_SPREAD_BP` | `10` | 환전 스프레드 편도 |
| `INDEX_FEE_BP` | `25` | 매매 수수료 편도 |
| `INDEX_DIV_WITHHOLDING` | `0.15` | 미국 배당 원천징수 |
| `TELEGRAM_TOKEN` / `TELEGRAM_PUBLIC_CHANNEL_ID` | — | 없으면 발송 생략(러너는 성공) |
| `DRY_RUN` | `true` | `false` 여야 장부에 쓴다 |

**보고 형식:** 설계서 8장 그대로. **벤치마크 줄(ITOT 100% 동일 적립 대비)과 미실현 양도세를 반드시 포함한다** — 성공 판정 기준이 이 두 줄이다.

**Interfaces:**
- Produces:
  - `run(now: date | None = None) -> dict` — 그날 한 일을 dict 로 반환(테스트가 읽는다). 부수효과는 장부·텔레그램.
  - `build_report(state, fx, prices) -> str` — 순수 함수. 테스트 가능.

- [ ] **Step 1: 멱등성 테스트를 먼저 쓴다** (이 Task 의 핵심)
  - 같은 달에 `run()` 을 **5번** 불러도 입금 1회·주문 1세트·보고 1회
  - 다음 달에 부르면 다시 1회
  - 같은 배당이 두 번 반영되지 않는다
  - 텔레그램 환경변수가 없어도 러너가 성공으로 끝난다
  - 매도 주문이 장부에 0건
  - `VIRTUAL_PORTFOLIO_FILE` 이 안 잡혔으면 **아무것도 안 하고 죽는다** (스윙 장부 보호)
- [ ] **Step 2: 구현** — 네트워크(yfinance·환율·텔레그램)는 테스트에서 monkeypatch
- [ ] **Step 3: 검증** — `pytest -q` 그린 + `DRY_RUN=true` 로 로컬 1회 실행해 출력 눈으로 확인
- [ ] **Step 4: 커밋** — `feat: 인덱스 자동운용 러너 — 매달 한 번, 몇 번을 깨워도 한 번`

---

### Task 4: 무인 가동 — `.github/workflows/index-autopilot.yml`

- [ ] **Step 1: 워크플로 작성**
  - `cron: "30 21 1-8 * *"` — **매월 1~8일** 매일. cron 은 휴장일을 모르므로 날짜로 창을 잡고 멱등성으로 중복을 막는다.
    > 설계서는 1~5일이었다. **1~8일로 넓힌다** — 주문은 다음 거래일 시가에 체결되므로 5일에 주문하면 6일 실행분이 있어야 체결·보고가 끝난다. 연휴가 겹치면 5일 창으로는 그 달 보고가 통째로 빠지고, 그건 성공 판정 ①(무인 가동 12/12)의 실패로 기록된다.
  - `runs-on: ubuntu-latest` — 브로커 API 를 안 부르므로 클라우드로 충분하다(토스 IP 화이트리스트 이슈와 무관).
  - `concurrency: index-autopilot`, `cancel-in-progress: false`
  - env: `VIRTUAL_PORTFOLIO_FILE: index_portfolio.json`, `VIRTUAL_CAPITAL_KRW: "0"`, `DRY_RUN: "false"`, 시크릿은 `TELEGRAM_TOKEN` / `TELEGRAM_PUBLIC_CHANNEL_ID`
  - `workflow_dispatch` 의 `dry_run` 기본값은 **`true`** (수동 실행이 장부를 건드리지 않게). `paper-trade-us.yml` 의 2026-07-30 사고 주석과 같은 이유다.
  - 커밋 단계는 `index_portfolio.json` **하나만** add 한다. `virtual_portfolio.json` 을 절대 이 워크플로에서 커밋하지 않는다.
- [ ] **Step 2: 검증** — `workflow_dispatch` 로 `dry_run=true` 수동 실행 1회. 로그에 계획된 주문이 찍히고 장부 파일이 안 바뀌는지 확인
- [ ] **Step 3: 커밋** — `feat: 인덱스 자동운용 워크플로 — 매월 1~8일`

---

### Task 5: 마찰을 잰다 — `scripts/measure_index_autopilot.py`

**성공을 찾는 최적화가 아니다.** 규칙이 적힌 대로 도는지와 마찰(정수주 지연·비용·환전)의 크기를 재는 용도다.

**Files:**
- Create: `scripts/measure_index_autopilot.py`
- Modify: 없음 (패널은 `load_panel` 이 알아서 붙인다)

- [ ] **Step 1: 패널 확보** — `load_panel(["ITOT","AGG","GLDM","GLD"], ...)` 로 `data/etf_panel.parquet` 에 없는 티커를 받아 붙인다. **GLDM 은 2018년 상장이라 그 이전은 GLD 로 대체하고 그 사실을 결과 표에 적는다.** 구간은 ITOT 상장(2004) 이후로 자동으로 잘린다.
- [ ] **Step 2: 시뮬** — 매월 첫 거래일에 `plan_orders()` 를 그대로 불러 다음 거래일 시가로 체결. **비용·환전·배당 세후를 전부 켜고 잰다.**
- [ ] **Step 3: 벤치마크 줄을 같이 낸다** — 같은 날 같은 금액 **ITOT 100%** 적립. 연환산 차이(%p)가 성공 판정 ②의 기준선이다.
- [ ] **Step 4: 결과를 `docs/measurements/` 에 기록** — 비용 가정을 표에 같이 적는다. 이 저장소를 죽인 게 비용 가정 하나였다(PR #94·#95).
- [ ] **Step 5: 커밋** — `measure: 인덱스 자동운용 마찰 측정 — 벤치마크 동반`

---

## 완료 조건

- [ ] `pytest -q` 그린 (기존 441 + 신규)
- [ ] `index_portfolio.json` 이 생기고 `virtual_portfolio.json` 은 **한 바이트도 안 변했다** (`git diff` 로 확인)
- [ ] 워크플로 수동 실행 1회 성공
- [ ] 측정 결과가 `docs/measurements/` 에 벤치마크 줄과 함께 있다
- [ ] PR 하나로 머지

## 이 계획이 명시적으로 안 하는 것

- 실제 돈 · 매도 · 밴드 감시 · 비중 튜닝 · 레짐 필터 · 국내 상장 ETF (설계서 10장)
- 월 보고 외의 대시보드·앱 화면
- 성공 판정은 **12개월 뒤** 한다. 3개월 성적으로 규칙을 바꾸면 그게 여섯 번째 실패다.
