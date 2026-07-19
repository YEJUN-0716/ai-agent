# 유니버스 확대와 가격 패널 캐시 — 설계

작성일: 2026-07-20

## 배경

전략 성능을 개선하려 했으나, 현재 시스템은 **팩터에 알파가 있는지 판정할 수 없는 상태**다.

관측된 사실:

- `signal_log.json` — 시그널 6건, 청산 완료 0건. 실측 성과 없음.
- `ic_weights.json` — 유니버스 37종목, `mom_3m` ICIR 0.077, `mom_1m` 0.024, `low_vol` -0.21. 쓸만한 수준(0.5 이상)에 한참 못 미친다.
- `ic_weight_updater.py:20` — 티커 37개 하드코딩.
- `modules/factor_validator.py:175, 392, 519` — 동일한 다운로드 루프 3벌 중복, 티커를 1개씩 순차 다운로드. 37종목에 30~60분(≈1분/종목).
- `modules/factor_engine.py:197` — 펀더멘털도 종목당 `yf.Ticker().quarterly_financials` 1회 호출.
- `yf.Ticker('AAPL').quarterly_financials`는 **5개 분기(약 15개월)만** 반환. 5년 IC 스터디에서 밸류·퀄리티 팩터는 기간의 약 80%가 결측이며, 그 결과 `n: 0`으로 IC가 아예 측정되지 않는다. 그럼에도 하락장 가중치의 31%를 배분받고 있다 (아래 "펀더멘털 팩터 처리" 참조).
- `app.py:3124`의 'S&P 500 전체' 프리셋은 오늘 기준 하드코딩이라 사멸 티커(ATVI, FISV, PXD, KSU 등)를 포함한다.

37종목이라는 표본으로는 팩터의 크로스섹션 분산이 부족해 IC 통계 자체가 의미를 갖기 어렵다. 티커 목록만 늘리면 500종목 × 1분 = 8시간이 되어 GitHub Actions 6시간 제한을 초과한다. 따라서 **데이터 계층부터 손봐야 한다.**

## 목표

`ic_weight_updater.py`가 S&P 500 전체(~500종목)로 완주하고, 모멘텀·저변동성 팩터의 IC가 통계적으로 유의한지 판정 가능해지는 것.

**비목표**: 수익 증가. 이 작업의 산출물은 측정 능력이지 성과가 아니다. 표본을 늘리면 추정이 정밀해질 뿐 없던 알파가 생기지는 않는다.

## 아키텍처

신규 모듈 2개를 추가하고 기존 호출부를 그쪽으로 돌린다. `app.py`는 건드리지 않는다.

### `modules/price_panel.py`

단일 진입점:

```python
load_panel(tickers: list[str], start, end) -> tuple[dict, dict]
```

반환 형태를 기존 루프의 산출물과 동일하게 맞춘다 — `({ticker: Series}, {ticker: DataFrame})`. 그래야 `factor_validator.py`의 3개 블록을 각각 한 줄 호출로 치환할 수 있고 하위 로직은 손대지 않는다.

내부 책임 (전부 모듈 안에 은닉):

| 책임 | 방식 |
|---|---|
| 캐시 읽기 | `data/price_panel_v1.parquet`, wide DataFrame, `index=날짜`, `columns=MultiIndex(ticker, field)`, field는 OHLCV 5개 |
| 결손분 판별 | 요청 범위 ∖ 캐시 보유 범위 → 부족한 티커·날짜 산출 |
| 다운로드 | `yf.download(list, threads=True)` 일괄 호출, 청크 100종목 |
| 캐시 쓰기 | 병합 후 재기록. 원자적 쓰기 (tmp 파일 → replace) |
| 폴백 | 캐시 미스·손상 시 전량 재구축 |

의존성 경계: yfinance와 pandas만 안다. Streamlit도, 팩터 로직도, 워크플로도 모른다. 역방향 의존 없음.

### `modules/universe.py`

현재 티커 목록이 세 곳(`app.py:3124`, `paper_trade_runner*.py:9x`, `ic_weight_updater.py:20`)에 흩어져 있고 서로 다르다. `SP500` 상수 하나로 통합한다.

- 사멸 티커 정리. 확인된 것: `ATVI` 제거(MSFT 피인수), `FISV` → `FI`(사명 변경), `PXD` 제거(XOM 피인수), `KSU` 제거(CP 합병), `FBHS` → `FBIN`(분할). 전체 목록은 구현 시 `app.py:3124` 프리셋 500개를 yfinance로 일괄 조회해 응답 없는 티커를 뽑아 확정한다.
- `ic_weight_updater.py`는 `TICKERS = universe.SP500`으로 교체 (37 → ~500)
- `.gitignore`에 `data/` 추가
- 데이터를 못 받는 티커는 로그로 남기고 제외 — 조용히 삼키지 않는다

## 증분 갱신 로직

`load_panel` 진입 시 4개 경로:

1. 캐시 로드 실패(없음/손상/스키마 불일치) → 전량 다운로드, 새로 기록
2. 요청 티커 중 캐시에 없는 종목 → 그 종목만 전체 기간 다운로드
3. 캐시 최종일 < 요청 end → 부족한 날짜 구간만 전 종목 일괄 다운로드
4. 둘 다 충족 → 네트워크 호출 0회, 캐시 슬라이스만 반환

일상적인 주간 실행은 3번 경로 = 7일치 × 500종목 ≈ 일괄 호출 5회.

## 캐시 영속화

`data/`는 gitignore 대상. 워크플로 간 공유는 `actions/cache`로 한다.

```yaml
- uses: actions/cache@v4
  with:
    path: data/
    key: price-panel-v1-${{ github.run_id }}
    restore-keys: price-panel-v1-
```

캐시 키는 불변이라 덮어쓸 수 없다. `run_id`로 매 실행 새 항목을 저장하고 `restore-keys` 접두사로 직전 것을 복원하는 표준 패턴. 7일 미사용 시 만료되지만 주간 실행이라 유지되며, 만료되어도 1번 경로로 자동 복구된다(첫 실행 ~15분).

**캐시 무효화**: 파일명의 `v1`과 cache key의 `v1`이 스키마 버전. 패널 구조 변경 시 둘 다 올려 전량 재구축을 강제한다.

워크플로 변경은 `ic-update.yml` 한 곳뿐. `signal-alerts.yml`·`paper-trade-us.yml`도 같은 캐시를 쓸 수 있으나 이번 목표와 무관하므로 넣지 않는다.

## 펀더멘털 팩터 처리

### 현황 (실측)

IC 계산 자체는 이미 정직하다. `ic_weights.json`을 보면:

- `per_factor_ic.value` / `.quality` → `n: 0`, `mean_ic: 0.0`
- `ic_unavailable_factors: ["value", "quality"]` 필드 존재
- `caveats.ic_data_note`에 "PIT 재무 데이터 미확보" 명시

`_calc_per_factor_zscores`가 결측 시 0.0을 반환하고, 전 종목이 상수 0.0이면 `spearmanr`가 NaN을 내어 해당 리밸런싱 시점이 스킵된다. 그래서 `n: 0`이 정확히 기록된다.

### 실제 문제

IC가 측정되지 않은 팩터에 **여전히 실질 가중치가 배분된다.**

```
bear:    mom_3m 0.2825 | low_vol 0.2797 | value 0.1748 | quality 0.1399 | mom_1m 0.0392 | ict 0.0839
neutral: mom_3m 0.5305 | low_vol 0.1050 | value 0.1050 | quality 0.0788 | mom_1m 0.1176 | ict 0.0630
bull:    mom_3m 0.6387 | low_vol 0.0678 | value 0.0678 | quality 0.0452 | mom_1m 0.1265 | ict 0.0542
```

하락장에서 측정 예측력이 0인 두 팩터가 가중치의 **31%**를 차지한다.

원인은 `ic_weight_updater.py:41`:

```python
ic_val = max(per_factor_ic.get(factor, {}).get("mean_ic", IC_FLOOR), IC_FLOOR)
scaled[factor] = base * ic_val
```

`IC_FLOOR = 0.005`가 `mom_3m`의 실측 IC `0.0202`의 약 1/4이라, 바닥값으로 눌러도 무시할 수 없는 크기로 살아남는다. `caveats.ic_data_note`는 이 동작을 "기본 REGIME_WEIGHTS 비례 배분"이라고 설명하지만 코드는 그렇게 하지 않는다 — 문서와 구현이 어긋나 있다.

### 결정

`ic_unavailable_factors`에 속한 팩터는 `derive_ic_regime_weights`에서 **가중치 0으로 제외하고 나머지를 재정규화**한다.

이는 실거래 시그널을 바꾸는 변경이다. 하락장 종목 선정이 눈에 띄게 달라진다. 그럼에도 측정되지 않은 신호에 자본의 31%를 배분하는 현 상태보다는 낫다.

`ic_data_note` 문구도 실제 동작에 맞게 고친다.

최근 15개월 구간의 참고용 IC는 만들지 않는다. 신뢰할 수 없는 숫자를 하나 더 두면 결국 쓰이게 된다.

모멘텀·저변동성 등 가격 기반 팩터는 5년 전체가 정상 계산되므로 유니버스 확대의 효과가 온전히 나온다.

## 생존편향

오늘 살아있는 500종목으로 과거 5년을 측정하는 구조라 IC가 실제보다 낙관적으로 나온다. 시점별 구성종목 복원은 이번 범위에 넣지 않으므로, **제거하지 않고 명시**한다.

`ic_weights.json`의 기존 `caveats.survivorship_bias_warning`은 이미 `len(TICKERS)` 기준으로 생성되므로, 유니버스를 500으로 늘리면 자동으로 갱신된다. 별도 작업은 필요 없다.

앱 노출은 하지 않는다 — `app.py`를 건드리지 않는다는 이 스펙의 결정과 충돌한다. 후속 작업으로 남긴다.

## 오류 처리

현재 다운로드 루프는 `except Exception: pass`다. 500종목에서 300개가 실패해도 결과가 정상처럼 나온다. `price_panel`은 다르게 간다:

| 상황 | 동작 |
|---|---|
| 개별 티커 다운로드 실패 | 제외하고 실패 목록에 수집 |
| 실행 종료 시 | 성공 N / 실패 M, 실패 티커 목록을 stdout에 출력 |
| 성공률 < 80% | 예외 발생 — 표본 부족 상태로 가중치를 쓰느니 실패가 낫다 |
| 캐시 손상 | 경고 출력 후 재구축 (여기서만 조용한 폴백 허용) |

`ic_weights.json`에 `coverage: {requested, resolved, failed}`를 기록해 사후 확인을 가능하게 한다.

## 테스트

이 레포 최초의 pytest를 도입하되 **`price_panel`에만 범위를 한정**한다. 순수 데이터 모듈이라 테스트 가치가 가장 높다. yfinance는 목으로 대체하여 네트워크 없이 도는 테스트만 만든다.

- 캐시 미스 → 다운로드 호출됨
- 캐시 히트 → 네트워크 호출 0회
- 부분 히트 → 부족한 구간만 요청
- 티커 추가 → 신규 종목만 요청
- 손상된 parquet → 재구축, 예외 없음
- 성공률 80% 미만 → 예외 발생

## 의존성 변경

`requirements.txt`에 추가:

- `pyarrow>=15.0` (parquet)
- `pytest>=8.0` (테스트)

## 성공 기준

1. `ic_weight_updater.py`가 ~500종목으로 완주하고, 2회차부터 10분 이내
2. 모멘텀·저변동성 팩터의 IC가 500종목 표본에서 산출됨 (현재 37종목, n=58)
3. 그 IC가 통계적으로 유의한지 판정 가능해짐

3번의 답이 "유의하지 않다"로 나올 가능성이 상당하다. 그 경우 다음 단계는 팩터 튜닝이 아니라 이 팩터군을 버리고 다른 신호원을 찾는 것이다. 이 작업은 그 판단의 근거를 만드는 일이다.
