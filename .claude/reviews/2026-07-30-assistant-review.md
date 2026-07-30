# 코드 리뷰: AI 업무 비서 (Task 4~9)

**리뷰 일자**: 2026-07-30
**범위**: 지출 한도로 독립 리뷰를 받지 못한 채 머지된 코드
`assistant/`, `channels/`, `server.py`, `tools/virtual_trade.py`
**커밋**: `d7a6153` (main)
**판정**: **BLOCK** — CRITICAL 2건

## 요약

읽기 경로(주식 조회·메모·감사 기록)는 견고하다. 문제는 전부 **비서와
stock-analyzer가 맞닿는 한 지점** — `VirtualBrokerExecutor` — 에 몰려 있다.
이 이음매를 지나가는 테스트가 하나도 없어서, 106개가 전부 통과하는 상태로
심각한 결함 두 개가 머지됐다.

**예약된 자동매매(내일 아침 실행)는 영향받지 않는다.** 러너는 달러로 넘기고
`PYTHONUTF8=1`을 설정하므로 아래 두 결함을 모두 비껴간다. 망가진 것은
사장님이 비서에게 직접 시키는 수동 매매뿐이다.

---

## CRITICAL

### C1 — 매수 금액이 1,400배로 기록된다

**위치**: `tools/virtual_trade.py:67`

```python
def buy(self, symbol: str, amount_krw: float) -> dict:
    return self._module().place_notional_buy(symbol, amount_krw)
```

`place_notional_buy`는 `market` 기본값이 `"US"`이고, 오늘 올린 수정
(stock-analyzer `0d6a6f1`) 이후 KRX가 아닌 금액을 **달러로 보고 환율을 곱한다**.
비서는 원화를 넘긴다.

**실측**

| | |
|---|---|
| 사장님이 말한 금액 | 500,000 원 |
| 장부에 적힌 금액 | 700,000,000 원 |
| 배수 | 1,400 배 |
| 계좌 현금 | 10,000,000 원 |
| 체결 여부 | 불가 — `_fill_buy`가 현금 부족으로 조용히 폐기 |

**결과**: 사장님은 "예약했습니다. 다음 거래일 시가에 체결됩니다"라는 답을 받지만
주문은 체결되지 않고 사라진다. 실패를 아무도 알려주지 않는다.

**원인**: 오늘의 단위 수정이 러너 경로(달러 전달)를 고치면서 비서 경로(원화 전달)를
깨뜨렸다. 회귀다.

**수정 방향**: 원화 금액을 환율로 나눠 달러로 넘기거나, `virtual_broker`에
단위가 이름에 드러나는 원화 전용 진입점을 만든다. `market="KRX"`를 넘기는 것은
미국 종목을 한국 종목으로 속이는 것이므로 안 된다.

---

### C2 — 승인 한 번에 주문이 나가고도 제안이 남는다 (이중 주문)

**위치**: `tools/virtual_trade.py:204-216` + stock-analyzer `virtual_broker.py:274-275`

```python
save_state(state)                              # 주문이 장부에 들어감
print(f"  [가상] 매수 예약 {symbol} ... — 다음 거래일 시가 체결")   # 여기서 터짐
```

`—`(U+2014)는 cp949로 인코딩되지 않는다. 확인된 실행 환경:
Python 3.14.5, `sys.stdout.encoding == 'cp949'`, 비서 어디에도 `PYTHONUTF8` 없음
(러너에는 있다).

**저장이 끝난 뒤** print가 `UnicodeEncodeError`를 던지므로,
`approve_request`의 `except Exception`은 이것을 "브로커 실패"로 오인해
제안을 대기 목록에 되돌려 놓고 사장님께 이렇게 답한다:

> 제안은 그대로 두었으니 '/승인 xxxx'로 다시 시도할 수 있습니다.

**실측**: 승인 1회 후 → 브로커 장부의 주문 1건, 대기 목록의 제안 1건. 둘 다 존재.
안내대로 재승인하면 **주문이 두 건**이 된다.

`_PENDING_LOCK`으로 막아둔 바로 그 "한 번 승인 → 두 건 주문"이 다른 문으로
되돌아왔다. 잠금은 동시 승인만 막을 뿐, 실행 후 실패를 실행 전 실패와
구분하지 못한다.

**수정 방향**: 실행 실패를 되돌리기 전에 실제로 주문이 나갔는지 확인해야 한다.
근본적으로는 비서가 브로커의 stdout에 의존하지 않도록 `PYTHONUTF8=1`을
서버 진입점에서 강제하는 것이 함께 필요하다.

---

## HIGH

### H1 — 대화 기록이 assistant 메시지로 시작하면 모든 질문이 실패한다

**위치**: `assistant/memory.py:55-66` + `assistant/brain.py:279-280`

Anthropic Messages API는 첫 메시지가 반드시 `user`여야 한다. 두 경로로 깨진다.

**(a) 홀수 `ASSISTANT_HISTORY_LIMIT`** — 검증이 없다. 실측:

```
limit=4 → 첫 메시지 user      (정상)
limit=5 → 첫 메시지 assistant (400)
limit=3 → 첫 메시지 assistant (400)
```

**(b) 저장 도중 중단** — `ask()`는 질문과 답을 두 번에 나눠 저장한다.
그 사이에 프로세스가 죽으면 짝이 맞지 않는 `user` 한 줄이 영구히 남는다. 실측:
그 상태에서 `limit=4` → 첫 메시지 `assistant`.

**결과**: 그 뒤로 무엇을 물어도 API 오류. 사장님은 이유를 알 수 없고,
SQLite 파일을 직접 손대야 풀린다.

**수정 방향**: `load_history`가 반환 직전 맨 앞의 assistant 메시지를 버린다.
저장은 한 트랜잭션으로 묶는다.

---

## MEDIUM

### M1 — 비서와 stock-analyzer의 경계에 테스트가 없다

`tests/test_virtual_trade.py`는 전부 가짜 executor를 주입한다.
`VirtualBrokerExecutor`도, 진짜 `place_notional_buy`의 시그니처와 단위도
한 번도 실행되지 않는다. C1과 C2가 106 그린 상태로 머지된 이유가 이것이다.

두 저장소가 **함수 시그니처로만 이어져 있고 그 계약을 확인하는 테스트가 없다**는
것이 이 시스템의 가장 약한 고리다. 한쪽을 고치면 다른 쪽이 조용히 깨진다 —
오늘 실제로 그렇게 됐다.

### M2 — 브로커 성공 판정이 반환값을 보지 않는다

`approve_request`는 예외가 없으면 성공으로 친다. `place_notional_buy`가
`{"ok": False}`를 돌려줘도 "예약했습니다"라고 답한다. 지금은 항상 `ok: True`라
드러나지 않지만, 판정 근거가 없는 것은 사실이다.

---

## LOW

- **L1** `channels/telegram_bot.py:84` — 텔레그램이 붙이는 `/승인@봇이름` 형태를
  인식하지 못한다. 개인 대화에서는 잘 발생하지 않는다.
- **L2** `channels/web.py:100` — `/approve`에 인증이 없다. 브라우저를 통한 공격은
  `TrustedHostMiddleware`(DNS 재바인딩)와 CORS 프리플라이트(JSON POST)로 막힌다.
  확인함: HTML 폼이 보낼 수 있는 content-type은 FastAPI가 JSON으로 파싱하지 않는다.
  남는 위험은 같은 PC의 다른 프로세스뿐이며, 1인 사용·가상 계좌 전제에서 수용 가능.
- **L3** `tools/assistant_notes.py:144` — 감사 기록 쓰기 실패(디스크 가득 등)가
  처리되지 않는다.

---

## 검증 결과

| 검사 | 결과 |
|---|---|
| 테스트 (pytest) | 106 passed |
| 타입 검사 | 미구성 |
| 린트 | 미구성 |

테스트는 전부 통과한다. 그것이 이 리뷰의 요점이다 — **통과가 안전을 뜻하지 않는다.**

## 검토한 파일

| 파일 | 줄 | 상태 |
|---|---|---|
| `assistant/brain.py` | 324 | H1 |
| `assistant/config.py` | 138 | H1(a) |
| `assistant/memory.py` | 74 | H1 |
| `channels/telegram_bot.py` | 131 | L1 |
| `channels/web.py` | 113 | L2 |
| `server.py` | 96 | C2(관련) |
| `tools/virtual_trade.py` | 234 | **C1, C2**, M2 |
| `tools/assistant_notes.py` | 233 | L3 |
