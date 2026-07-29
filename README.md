# AI AGENT

Claude가 "Workflow → Agent → Tool" 3단 구조로 작업을 처리하는 프로젝트입니다.
전체 운영 규칙은 [`CLAUDE.md`](./CLAUDE.md)에 있습니다.

## 폴더 구조

| 경로 | 역할 |
|---|---|
| `CLAUDE.md` | 프로젝트 운영 규칙 (Claude가 매 세션 읽음) |
| `workflows/` | 작업별 평문 지침 (목표/입력/도구/출력/에러처리). 새로 만들 땐 `_template.md` 참고 |
| `tools/` | 실제로 동작하는 Python 스크립트. 새로 만들 땐 `_template.py` 참고 |
| `server.py` | AI 업무 비서 실행. 사용법은 [`workflows/ai-assistant.md`](./workflows/ai-assistant.md) |
| `assistant/` | 비서 본체 — 설정, 대화 기억, Claude API 도구 호출 루프 |
| `channels/` | 입출력 창구 — 텔레그램, 웹 채팅 |
| `tests/` | pytest 테스트. `pytest -q`로 전체 실행 |
| `data/assistant/` | 비서가 소유하는 데이터 — 관심종목, 메모, 대화 기록, 감사 로그 |
| `.tmp/` | 버려도 되는 스크래치 공간 (git에는 폴더만 추적, 내용물은 무시) |
| `.env` | 시크릿 전용 (git에서 제외됨). 필요해지면 `.env.example`을 복사해서 만드세요 |
| `credentials.json` / `token.json` | OAuth 관련 파일. 필요한 도구가 생길 때 자동 생성됨 (git에서 제외됨) |

## 시작하기

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

새 작업이 생기면: `workflows/`에 지침을 먼저 쓰고, 필요한 실행 로직은 `tools/`에
Python 스크립트로 만듭니다.
