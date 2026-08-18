# EDGAR PIT 재무 데이터 — 설계

작성일: 2026-07-20

## 배경

유니버스 확대(37 → 276종목) 작업에서 가격 기반 팩터에 측정 가능한 알파가 없다는 결론이 나왔다. 다음 단계인 신호원 교체의 가장 값싼 후보가 밸류·퀄리티 팩터인데, 이 둘은 **지금 측정 자체가 불가능**하다.

원인:

- `factor_engine.py:184` `fetch_quarterly_fundamentals_history`가 `yf.Ticker(tk).quarterly_financials`를 쓴다.
- yfinance는 분기 재무를 **5분기(약 15개월)만** 반환한다.
- 5년 IC 스터디에서 밸류·퀄리티는 기간의 약 80%가 결측 → `n: 0`으로 IC가 아예 측정되지 않는다.
- 공시일을 "분기말 + 45일"로 **추정**한다 (`reporting_lag_days=45`). 추정이 틀리면 조용히 look-ahead bias가 생긴다.

## 목표

SEC EDGAR에서 20년치 PIT(point-in-time) 재무 데이터를 확보해, `ic_weights.json`에서 밸류·퀄리티의 IC를 **처음으로 측정 가능**하게 만든다.

**비목표**: 수익 증가. 두 팩터가 유의하게 나올지는 미지수다. 이 작업은 그 질문을 던질 수 있게 만드는 일이다. 답이 부정적이어도 실패가 아니라 결과다 — 신호원 교체의 범위가 커진다는 정보를 얻는다.

## EDGAR 확인 결과 (실측)

- API 무료, 키 불필요. `User-Agent` 헤더만 요구.
- `companyfacts` 엔드포인트가 AAPL 기준 2007~2026년, 20년치 제공.
- 공시일이 **추정이 아니라 실제 `filed` 날짜**로 들어있다.
- 티커→CIK 매핑: `company_tickers.json`이 SP500 276종목 전부 매칭.

### 수집 방식 — companyfacts (종목별 전체) 채택

두 엔드포인트를 벤치마크했다 (5종목 표본, 276종목 추정):

| 방식 | 276종목 소요 | 전송량 | 요청 수 |
|---|---:|---:|---:|
| **companyfacts** (채택) | **4.4분** | 1.40 GB | 276 |
| companyconcept (태그별) | 14.3분 | 35 MB | 1,380 |

companyconcept가 전송량이 40배 적지만, SEC의 초당 10요청 제한이 지배적이라 요청 수가 5배인 쪽이 3배 느리다.

**미래 확장성에서도 companyfacts가 낫다.** 원본에 438개 태그가 다 들어있으므로:
- 새 팩터 추가 = 추출 코드만. 네트워크 작업 0 (companyconcept는 팩터마다 14분 재수집).
- 태그 파편화(기업마다 다른 태그명)를 로컬에서 해결. 실패해도 디스크에서 즉시 재시도.
- 알파 탐색의 반복 주기가 네트워크가 아니라 CPU에 묶인다.

대가는 저장 공간 1.4GB뿐이며 Actions 캐시 한도 10GB 안에 든다.

참고: 벌크 zip(`companyfacts.zip`)은 1.39GB로 276종목 개별 수집과 사실상 동일 크기다(1만 기업 전체 포함). 유니버스를 크게 늘릴 때만 의미가 있고 지금은 압축 해제·파싱 비용만 추가되어 이득이 없다.

### CIK 매핑 함정 (반드시 방어)

`company_tickers.json`은 `XOM`에 CIK 2115436을 주는데, 이 CIK를 조회하면 **HTTP 200과 함께 재무제표가 아닌 수수료 신고(`ffd`) 데이터**만 온다. 실제 Exxon Mobil은 CIK 34088 (`us-gaap` 태그 438개). 잘못된 CIK는 `us-gaap` 태그 0개.

- 요청이 성공하므로 예외로 잡히지 않는다.
- 이름이 둘 다 "Exxon Mobil"이라 이름 검증으로도 못 거른다.
- **`us-gaap` 네임스페이스 존재 여부로 검증**해야 한다. 없으면 그 종목은 실패 처리.

## 아키텍처

신규 `modules/edgar_fundamentals.py` 하나. `factor_engine.py`의 두 함수를 대체한다 (완전 교체, yfinance 경로 제거).

```python
fetch_quarterly_fundamentals_history(tickers, ...) -> {ticker: DataFrame}
fetch_shares_history(tickers, ...)                 -> {ticker: Series}
```

**반환 형태를 그대로 유지한다.** `factor_validator.py`가 `_fetch_fin_hist` / `_fetch_shares_hist`로 임포트하고 `point_in_time_fundamentals`가 소비한다. 시그니처·반환 구조를 맞추면 하위 로직을 손대지 않는다 (price_panel과 같은 전략).

**단, 인덱스의 의미가 바뀐다.** 지금은 "분기말 + 45일" 추정치가 인덱스인데, EDGAR의 실제 `filed` 날짜로 바뀐다. 소비 측(`<= as_of_date` 필터)은 그대로 동작하지만 값의 의미가 달라지므로 docstring에 명시한다.

### 추출 지표 5개

| 지표 | 용도 | 주 태그 | 종류 |
|---|---|---|---|
| revenue | margin 분모 | `RevenueFromContractWithCustomerExcludingAssessedTax` | 기간(duration) |
| operating_income | margin 분자 | `OperatingIncomeLoss` | 기간 |
| net_income | pe 분자 | `NetIncomeLoss` | 기간 |
| shares | pe 분모 | `WeightedAverageNumberOfDilutedSharesOutstanding` | 기간(duration) |
| stockholders_equity | **PIT p/b 신규 활성화** | `StockholdersEquity` | 시점(instant) |

주의: shares는 "가중평균"이라 `start`/`end`가 있는 **기간 데이터**다 (units 키는 `USD`가 아니라 `shares`). 손익 항목과 같은 80~100일 기간 필터 대상이다. 순수 시점 데이터는 stockholders_equity 하나뿐. 실측: `NetIncomeLoss` 첫 팩트가 `2007-09-30 ~ 2008-06-28`(272일, 9개월 누적)이었다 — 기간 필터의 필요성을 실증한다.

앞 4개는 기존 기능 유지에 필요한 최소 집합. `stockholders_equity`는 신규 추가지만 `factor_engine.py:402-410`에 p/b 사용 코드가 이미 있고 PIT 경로에서만 데이터가 없어 놀고 있다. 전체를 받는 이상 추출 비용이 0이라 함께 넣는다.

각 지표는 **태그 대체 목록(fallback chain)**을 갖는다. 주 태그가 없으면 대체 태그를 로컬에서 즉시 찾는다. 이는 `factor_engine.py:206`이 yfinance 컬럼명에 쓰는 패턴과 동일.

## 저장과 캐시

추출본과 원본을 분리 저장한다. 성격이 다르다.

| 산출물 | 크기 | 성격 | 없으면 |
|---|---|---|---|
| `data/edgar_facts_v1.parquet` | 수 MB (추정, 구현 시 확정) | IC 계산에 실제 쓰이는 5지표 | IC 분석 불가 |
| `data/edgar_raw/*.json` | 1.4 GB | 원본 438개 태그 | 새 팩터 추가 시에만 필요 |

`data/`는 이미 gitignore.

**캐시 키 분리** (`ic-update.yml`):

```yaml
- uses: actions/cache@v4          # 추출본 - 작고 매 실행 필요
  with:
    path: data/edgar_facts_v1.parquet
    key: edgar-facts-v1-${{ github.run_id }}
    restore-keys: edgar-facts-v1-

- uses: actions/cache@v4          # 원본 - 크고 가끔 필요
  with:
    path: data/edgar_raw/
    key: edgar-raw-v1-${{ github.run_id }}
    restore-keys: edgar-raw-v1-
```

분리 이유: 원본이 만료돼도 IC 분석은 계속 돌아야 한다. 하나로 묶으면 1.4GB 캐시 만료 시 추출본까지 날아간다.

**주간 실행과 캐시 만료.** Actions 캐시는 7일 미접근 시 만료되는데 `ic-update.yml`은 주간(정확히 7일 간격) 실행이라 원본 캐시가 경계선에 걸린다. 만료되면 원본 1.4GB를 4.4분에 재수집한다. 치명적이지 않다 — 실패가 아니라 느려질 뿐이고 `timeout-minutes: 120` 안에 든다. **원본 캐시는 자주 만료된다고 가정하고, 파이프라인이 원본 없이도 완결되게 설계한다.**

**증분 갱신은 하지 않는다.** 과거 공시는 불변이지만 새 분기·정정 공시가 있다. price_panel처럼 정교한 증분 로직을 만들 만큼 자주 돌지 않는다 (주 1회, 4.4분). **원본 있으면 재사용, 없으면 전량 재수집**으로 단순하게 간다.

**로컬 개발.** `data/edgar_raw/`가 로컬 디스크에 남으므로 새 팩터 시험 시 네트워크 없이 추출만 재실행. companyfacts를 택한 이유가 여기서 나온다.

## 분기 데이터 정합성 (핵심 위험)

`point_in_time_fundamentals`(`factor_engine.py:262`)는 TTM을 이렇게 계산한다:

```python
recent_q = past.tail(4)
net_ttm  = float(recent_q["net_income"].sum())
```

**"마지막 4행 = 서로 다른 4개 분기"라고 무조건 가정한다.** yfinance는 우연히 이를 만족했지만 EDGAR는 두 방식으로 깬다:

1. **같은 분기가 여러 번 등장.** 2025 Q1 실적은 2025년 10-Q에 처음 실리고 이듬해 10-Q에 비교 대상으로 또 실린다. `filed`가 다르므로 별개 행이 된다. `tail(4)`가 겹치는 기간을 합산해 **TTM이 부풀려진다.**
2. **10-K는 연간 수치를 담는다.** 손익 항목은 기간 데이터고 10-K 매출은 12개월치다. 분기 행 사이에 섞이면 `tail(4)`가 12+3+3+3 = **21개월치를 합산한다.**

둘 다 **예외 없이 조용히 틀린 값**을 낸다. P/E가 실제의 1/3이면 저평가로 보여 매수 신호가 뜬다.

### 대응: 모듈이 "분기당 정확히 1행"을 보장한다

`edgar_fundamentals.py`가 책임진다:

- **기간 필터링.** 손익 항목은 `end - start`가 80~100일인 것만 채택. 연간(약 365일)은 분기로 쓰지 않는다.
- **Q4 유도.** 10-K에 Q4 단독 수치가 없다. `Q4 = FY − (Q1+Q2+Q3)`로 계산. 세 분기가 다 있을 때만 유도하고 하나라도 없으면 Q4는 결측 — 추측하지 않는다.
- **중복 제거.** `(start, end)`로 묶어 **가장 이른 `filed`**를 남긴다.
- **시점 항목**(stockholders_equity)은 `start`가 없다. `end` 기준으로 중복 제거. shares는 기간 데이터이므로 손익 항목과 동일하게 `(start, end)` 기준 처리한다.

### 중복 제거를 최초 공시 기준으로 하는 판단

엄밀한 PIT는 "as_of 시점에 알 수 있던 최신 정보"이므로 정정 공시가 있었다면 정정본을 써야 한다. 하지만 현재 인터페이스(`filed`로 인덱싱 → `tail(4)`)로는 버전 선택을 표현할 수 없다.

**최초 공시 값을 쓴다.** 팩터 연구의 통상 관행이고, 정정 정보를 미리 아는 look-ahead를 확실히 차단한다. 대가: 정정된 종목의 값이 실제 알려진 것보다 낡을 수 있다.

### 암묵적 계약을 명시화

`tail(4)`가 옳으려면 모듈 출력이 다음을 만족해야 한다. 이를 명시적 계약으로 삼고 테스트한다:

- 같은 `(start, end)` 조합이 두 번 나오지 않는다
- 손익 행의 기간 길이가 모두 80~100일이다
- 인덱스(`filed`)가 단조 증가한다

`point_in_time_fundamentals`는 **고치지 않는다.** 위 계약 하에서 지금 코드는 정확하다. 계약을 지키는 건 데이터를 만드는 쪽 책임이다. 다만 지금은 계약이 암묵적이므로 `factor_engine.py` docstring에 명시한다.

## 오류 처리

price_panel과 같은 원칙 — 개별 실패는 수집하고 실행 끝에 성공 N / 실패 M과 실패 목록을 출력. `except: pass` 금지.

| 상황 | 동작 |
|---|---|
| `company_tickers.json`에 티커 없음 | 실패 기록, 제외 |
| companyfacts 200이나 `us-gaap` 없음 | 실패 기록, 제외 (XOM 사례) |
| HTTP 429 (SEC 제한) | 지수 백오프 재시도, 3회 실패 시 그 종목만 실패 |
| 특정 지표 태그를 전부 못 찾음 | 그 지표만 결측, 종목은 유지 |
| Q1~Q3 중 결손 | Q4 결측 |

### 편향된 커버리지가 결측보다 위험하다

지표별 종목 커버리지가 **70% 미만이면 해당 팩터를 `unavailable`로 표시**하고 IC를 계산하지 않는다.

태그 표준화는 대형주일수록 잘 되어 있어, 커버리지가 낮으면 남은 표본이 대형주로 치우친다. 그 IC는 `n > 0`이라 유효해 보이지만 편향된 부분집합의 통계다. `n = 0`은 "모른다"고 정직하게 말하지만 편향된 IC는 틀린 답을 자신 있게 말한다.

**70%는 근거가 아니라 판단이다.** 데이터를 보고 조정할 수 있다.

## 테스트

네트워크를 타지 않고 SEC 응답을 목으로 대체한다.

- 연간 행(365일)이 분기로 채택되지 않는다
- Q4가 `FY − (Q1+Q2+Q3)`로 유도된다
- Q1~Q3 중 결손이 있으면 Q4를 만들지 않는다
- 같은 `(start, end)`가 여러 `filed`로 오면 가장 이른 것만 남는다
- `ffd`만 든 페이로드(XOM 사례)는 실패 처리된다
- 429 응답에 재시도한다
- **계약 검증**: 출력에 기간 중복 없음, 손익 행 기간 80~100일, 인덱스 단조 증가

실제 SEC 응답을 잘라낸 고정 픽스처 1개를 넣어, 목이 실제 스키마와 어긋나는 것을 잡는다 (price_panel에서 목이 `end`를 무시해 테스트가 무의미해졌던 전례).

## 의존성 변경

`requirements.txt`에 추가 (이미 있으면 불필요):

- `requests` — 이미 있음
- `pyarrow` — 이미 있음 (유니버스 확대 작업에서 추가)

신규 의존성 없음.

## 성공 기준

1. 276종목 중 70% 이상에서 5개 지표 수집
2. `ic_weights.json`에서 **value·quality의 `n`이 처음으로 0이 아니게 됨**
3. 두 팩터의 IC가 유의한지 판정 가능

3번의 답이 "유의하지 않다"일 수 있다. 그건 실패가 아니라 결과다. 지금은 데이터가 없어 질문 자체를 못 던지는 상태이고, 이 작업은 질문을 던질 수 있게 만드는 일이다. 답이 부정적이면 신호원 교체의 범위가 훨씬 커진다는 정보를 얻는다.

## 이번 범위 밖 (후속)

- **정정 공시 반영**: 최초 공시 기준을 쓰므로 정정본은 무시된다. 엄밀한 PIT를 원하면 인터페이스(`filed` 인덱싱 → `tail(4)`)부터 재설계해야 한다.
- **신규 펀더멘털 팩터**: 총이익률, 부채비율, FCF 수익률, 발생액, 자산성장률 등. 원본이 로컬에 있으므로 추출 코드만 추가하면 된다. 이건 #1(신호원 교체)의 대상.
- **IC_FLOOR 로직 재설계**(#3): value·quality에 실측 IC가 생긴 뒤라야 "역방향 팩터를 어떻게 다룰까"가 실질 문제가 된다. 이 작업 완료 후 착수.
- **app.py 라이브 경로**: 이번엔 PIT(백테스트) 경로만 EDGAR로 교체한다. app.py의 실시간 재무 조회는 손대지 않는다.
