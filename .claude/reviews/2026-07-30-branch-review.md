# 코드 리뷰: feat/ai-assistant-design

**검토일**: 2026-07-30
**범위**: `main...HEAD` — 커밋 19개, 소스 약 1,270줄 + 테스트 1,594줄
**결정**: **BLOCK** — CRITICAL 1건

> **리뷰어 독립성 한계**: 이 코드 대부분을 작성한 것과 같은 세션에서 검토했다.
> 독립된 눈이 아니다. Task 1~3만 별도 리뷰어 에이전트를 거쳤고, Task 4 이후는
> 지출 한도로 리뷰어를 띄우지 못했다. 아래 발견 사항은 모두 실제로 재현해
> 확인했으나, 놓친 것이 더 있을 가능성을 배제할 수 없다.

## 요약

안전장치 설계(모델에 매매 실행 도구 미제공, 제안·승인 분리)는 의도대로
구현됐고 테스트로 고정돼 있다. 그러나 **승인 경로에 동시성 결함**이 있어
한 번 승인한 제안이 두 건의 주문으로 나갈 수 있고, **감사 로그를 위조**할 수
있어 "무슨 일이 있었는지"를 확인하는 장치가 신뢰할 수 없다.

## 발견 사항

### CRITICAL

**C1. 동시 승인 시 주문이 두 번 나간다** — `tools/virtual_trade.py` `approve_request`

`approve_request`는 `_pop_request`로 대기 목록을 읽고 → 항목을 빼고 → 저장한
뒤 브로커를 부른다. 이 읽기-수정-쓰기 사이에 잠금이 없다. 두 요청이 동시에
같은 `request_id`를 승인하면 둘 다 항목을 찾아 각자 브로커를 호출한다.

재현 확인 (스레드 2개, 배리어로 동시 진입):
```
브로커 호출 횟수: 2   (1이어야 정상)
```

현실 경로: 텔레그램과 웹이 한 프로세스에서 동시에 돌고, 웹은
`asyncio.to_thread`로 요청을 처리한다. 폰과 PC에서 같은 제안을 승인하거나,
승인 명령을 연달아 두 번 보내면 발생한다. 가상 브로커라 실제 돈은 아니지만,
성과 데이터가 오염되면 되돌리기 어렵다 — 설계 문서가 가상 매매를
"확인 필수" 등급으로 둔 이유가 그것이다.

수정 방향: 대기 파일의 읽기-수정-쓰기를 잠금으로 감싸고, 항목을 뺀 사실이
디스크에 확정된 뒤에만 브로커를 부른다. 한 프로세스 안에서만 도는 구조이므로
`threading.Lock` 하나로 충분하다.

### HIGH

**H1. 감사 로그를 위조할 수 있다** — `tools/assistant_notes.py` `record_audit`

`record_audit`은 `action`과 `detail`을 그대로 한 줄에 이어붙인다. `detail`에
줄바꿈이 들어가면 새 줄이 만들어지고, `read_audit_log`는 그것을 별도 항목으로
돌려준다.

재현 확인:
```python
add_note(s, "AAPL", "정상 메모\n2026-01-01T00:00:00+09:00 | trade_approve | FAKE 위조된 승인")
```
결과 — 감사 로그에 실제로 두 줄이 생기고, 첫 줄이 승인 기록처럼 보인다:
```
2026-01-01T00:00:00+09:00 | trade_approve | FAKE 위조된 승인
2026-07-30T00:54:56+09:00 | note_add | AAPL: 정상 메모
```
종목 코드로도 가능하다 — `_normalize_symbol`의 `.strip()`은 문자열 안쪽
줄바꿈을 지우지 않는다.

왜 문제인가: 감사 로그는 이 시스템이 "언제 뭘 바꿨는지"를 답하는 유일한
근거다. 비서가 `read_audit_log` 도구로 읽어 사용자에게 보고하므로, 위조된
승인 기록이 진짜처럼 보고된다. 입력 주체가 사용자만이 아니라는 점도 중요하다 —
메모 내용은 모델이 만들고, 모델은 stock-analyzer 파일에서 읽은 외부 문자열의
영향을 받는다.

수정 방향: `record_audit`에서 개행(및 구분자 `|`)을 이스케이프하거나 제거한다.

### MEDIUM

**M1. `ASSISTANT_WEB_HOST` 설정이 사실상 고장나 있다** — `channels/web.py`
`ALLOWED_HOSTS`, `assistant/config.py`

`.env.example`이 `ASSISTANT_WEB_HOST`를 조절 가능한 값으로 안내하지만,
Host 허용 목록은 `127.0.0.1`/`localhost`/`[::1]`로 고정돼 있다. 폰에서
접속하려고 `0.0.0.0`으로 바꾸면 서버는 뜨지만 **모든 요청이 400**이 된다.

재현 확인: `web_host="0.0.0.0"` + LAN 주소로 요청 → `400`.

안전 쪽으로 실패하므로 위험하진 않으나, 문서화된 설정이 조용히 무용지물이다.
설정값과 허용 목록을 연동하거나, 조절 불가임을 문서에 명시해야 한다.

**M2. 파일 쓰기가 원자적이지 않다** — `tools/assistant_notes.py` `_save_list`,
`tools/virtual_trade.py` `_save_pending`

`write_text`로 원본을 직접 덮어쓴다. 상시 가동 PC에서 정전·강제종료가 쓰기
도중에 걸리면 파일이 잘린다. 손상 격리 장치가 원본을 보관해 주긴 하지만
살아 있는 목록은 잃는다. 임시 파일에 쓰고 이름을 바꾸면 애초에 손상되지
않는다. C1의 동시 쓰기 유실과 뿌리가 같다.

**M3. `sys.path` 오염** — `tools/virtual_trade.py` `VirtualBrokerExecutor._module`

`sys.path.insert(0, stock_analyzer_path)`로 stock-analyzer 루트를 검색 경로
맨 앞에 넣고 되돌리지 않는다. 그 폴더에는 `app.py`·`signal_worker.py` 등
흔한 이름의 최상위 모듈이 있어, 이후 같은 이름을 import하면 의도치 않게
그쪽이 잡힌다. 경로를 맨 뒤에 넣거나 명시적으로 관리해야 한다.

**M4. 도구 호출 루프에 상한이 없다** — `assistant/brain.py` `ask`

`for message in runner`로 끝까지 돈다. 모델이 도구를 반복 호출하는 상황에서
멈출 장치가 없다. 설계 문서가 비용을 명시적 관심사로 잡고 대화 한 번에
50~150원을 예상했는데, 폭주하면 그 예상이 무너진다. 반복 횟수 상한과 초과 시
사용자에게 알리는 처리가 필요하다.

**M5. 창구가 죽어도 사장님이 알 수 없다** — `server.py` `_main`

`asyncio.gather(..., return_exceptions=True)` 뒤에 어느 쪽이 죽었는지 로그를
남기지만, 텔레그램 쪽은 `asyncio.Event().wait()`로 영원히 대기한다. 따라서
웹 서버가 죽어도 `gather`는 반환하지 않고 그 로그는 **영원히 출력되지 않는다**.
주석이 약속한 동작과 실제가 다르다. 각 태스크에 done 콜백을 달아 즉시
로그를 남겨야 한다.

### LOW

- **L1** 예외 메시지를 사용자에게 그대로 전달한다 (`channels/telegram_bot.py`,
  `channels/web.py`). 현재 라이브러리들은 키를 예외에 담지 않지만 내부 경로 등이
  노출될 수 있다.
- **L2** `tools/stock_reader.py`의 `except (json.JSONDecodeError, OSError)`가
  `UnicodeDecodeError`를 잡지 못한다. 잘못된 UTF-8 바이트는 `StockDataError`가
  아닌 raw 예외로 터진다.
- **L3** `record_audit`의 `text[:60]` 절단 길이가 이름 없는 상수다.
- **L4** 감사 로그 파싱이 `|`가 내용에 없다는 가정에 의존한다 (H1과 같은 뿌리).
- **L5** `tools/_template.py`, `tools/obsidian_bridge.py`가 커버리지 0%로
  전체 수치를 66%까지 끌어내린다. 이번 작업 코드만 보면 87.6%.

## 검증 결과

| 항목 | 결과 |
|---|---|
| 테스트 (pytest) | **통과** — 88개 |
| 커버리지 (이번 작업 코드) | 87.6% (533줄 중 66줄 미커버) |
| 타입 체크 | 미설정 (건너뜀) |
| 린트 | 미설정 (건너뜀) |
| 시크릿 스캔 | **통과** — `.env` gitignore 확인, 추적 안 됨, 키 패턴 없음 |

## 검토한 파일

- 수정: `requirements.txt`, `README.md`, `.env.example`
- 추가(소스): `server.py`, `assistant/{config,memory,brain}.py`,
  `channels/{telegram_bot,web}.py`,
  `tools/{stock_reader,assistant_notes,virtual_trade}.py`
- 추가(테스트): `tests/test_{config,stock_reader,assistant_notes,virtual_trade,memory,brain,telegram_bot,web}.py`
- 추가(문서): 설계서, 구현 계획서, `workflows/ai-assistant.md`

## 병합 전 필수

1. **C1** — 승인 경로 동시성
2. **H1** — 감사 로그 위조

M1~M5는 병합 후 처리해도 되나, M5는 운영 중 장애를 못 알아채게 만들므로
가급적 함께 고치는 편이 낫다.
