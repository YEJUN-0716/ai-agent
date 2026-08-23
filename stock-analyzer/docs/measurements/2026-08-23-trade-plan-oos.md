# 트레이드 플랜 OOS 검증 (2026-08-23)

- 유니버스: 293종목 · 2020-03-31 ~ 2026-08-14 (저장 패널)
- 나눈 기준: **사람이 본 적 있는 구간인가.** 2026-07-24 측정이 쓴 '최근 400봉'(2024-12-20~)이 IS, 그 앞이 OOS
- 숏 레짐필터·저확신 컷은 IS 결과를 보고 넣은 규칙이다 — 그 구간에서 재면 잘 나오는 게 당연하다
- 백테스트 자체에는 미래 참조가 없다 (`build_trade_plan(df[:i+1])`)
- **2026-08-16 정정 — 산 값이 바뀌었다.** 2026-08-09 판(+0.58R)은 구간 상단만 닿아도 구간 **중간값**에 산 것으로 적었다. 시장에 그 가격이 없었다. 지금은 러너가 실제로 거는 지정가(구간 상단, 갭이면 그날 시가)로 채점한다 — 셋업·체결 봉·승패는 그대로고 **R 만** 다시 낸 값이다 (`modules/trade_plan_backtest.placeable_r`, 근거는 `2026-08-12-entry-rule-daily.md`)
- **2026-08-23 정정 — timeout 을 장부와 같이 센다.** 예전에는 보유 상한을 넘긴 트레이드를 **0R**(안 판 것)로 셌는데, 장부는 상한 다음 봉 시가에 실제로 팔고 그 R 을 받는다(`virtual_broker.scan_plan_exit`). 지금은 백테스트도 그 값으로 센다. 청산 봉이 패널 밖이면 0R 이 아니라 **미결로 뺀다**

```
  ── OOS (규칙을 정할 때 안 본 구간, ~2024-12-19) ──
  전체                     setups=14244  filled= 9510  W/L=3409/5948  timeout= 153  winrate=  36%  expectancy=+0.11R  avg=+0.15R
    └ 롱                    setups=11078  filled= 7309  W/L=2743/4438  timeout= 128  winrate=  38%  expectancy=+0.21R  avg=+0.25R
    └ 숏                    setups= 3166  filled= 2201  W/L= 666/1510  timeout=  25  winrate=  31%  expectancy=-0.21R  avg=-0.19R

  ── IS (필터를 고를 때 본 구간, 2024-12-20~) ──
  전체                     setups= 5118  filled= 3405  W/L=1232/2114  timeout=  59  winrate=  37%  expectancy=+0.17R  avg=+0.20R
    └ 롱                    setups= 3777  filled= 2466  W/L= 925/1494  timeout=  47  winrate=  38%  expectancy=+0.25R  avg=+0.29R
    └ 숏                    setups= 1341  filled=  939  W/L= 307/ 620  timeout=  12  winrate=  33%  expectancy=-0.06R  avg=-0.03R

  ── 연도별 (전체) ──
  2020                   setups= 1667  filled= 1022  W/L= 459/ 533  timeout=  30  winrate=  46%  expectancy=+0.57R  avg=+0.59R
    └ 롱                    setups= 1468  filled=  876  W/L= 415/ 433  timeout=  28  winrate=  49%  expectancy=+0.71R  avg=+0.73R
    └ 숏                    setups=  199  filled=  146  W/L=  44/ 100  timeout=   2  winrate=  31%  expectancy=-0.22R  avg=-0.22R
  2021                   setups= 3074  filled= 2041  W/L= 833/1181  timeout=  27  winrate=  41%  expectancy=+0.29R  avg=+0.31R
    └ 롱                    setups= 2592  filled= 1685  W/L= 723/ 936  timeout=  26  winrate=  44%  expectancy=+0.39R  avg=+0.42R
    └ 숏                    setups=  482  filled=  356  W/L= 110/ 245  timeout=   1  winrate=  31%  expectancy=-0.20R  avg=-0.20R
  2022                   setups= 3367  filled= 2492  W/L= 748/1703  timeout=  41  winrate=  31%  expectancy=-0.13R  avg=-0.08R
    └ 롱                    setups= 2300  filled= 1735  W/L= 508/1195  timeout=  32  winrate=  30%  expectancy=-0.15R  avg=-0.09R
    └ 숏                    setups= 1067  filled=  757  W/L= 240/ 508  timeout=   9  winrate=  32%  expectancy=-0.08R  avg=-0.05R
  2023                   setups= 3099  filled= 2027  W/L= 644/1351  timeout=  32  winrate=  32%  expectancy=-0.07R  avg=-0.04R
    └ 롱                    setups= 2232  filled= 1435  W/L= 474/ 937  timeout=  24  winrate=  34%  expectancy=+0.03R  avg=+0.07R
    └ 숏                    setups=  867  filled=  592  W/L= 170/ 414  timeout=   8  winrate=  29%  expectancy=-0.31R  avg=-0.28R
  2024                   setups= 3118  filled= 1989  W/L= 747/1218  timeout=  24  winrate=  38%  expectancy=+0.19R  avg=+0.22R
    └ 롱                    setups= 2519  filled= 1601  W/L= 634/ 948  timeout=  19  winrate=  40%  expectancy=+0.31R  avg=+0.34R
    └ 숏                    setups=  599  filled=  388  W/L= 113/ 270  timeout=   5  winrate=  30%  expectancy=-0.33R  avg=-0.29R
  2025                   setups= 3098  filled= 2117  W/L= 768/1304  timeout=  45  winrate=  37%  expectancy=+0.18R  avg=+0.22R
    └ 롱                    setups= 2209  filled= 1499  W/L= 552/ 912  timeout=  35  winrate=  38%  expectancy=+0.23R  avg=+0.27R
    └ 숏                    setups=  889  filled=  618  W/L= 216/ 392  timeout=  10  winrate=  36%  expectancy=+0.06R  avg=+0.10R
  2026                   setups= 1939  filled= 1227  W/L= 442/ 772  timeout=  13  winrate=  36%  expectancy=+0.15R  avg=+0.18R
    └ 롱                    setups= 1535  filled=  944  W/L= 362/ 571  timeout=  11  winrate=  39%  expectancy=+0.28R  avg=+0.32R
    └ 숏                    setups=  404  filled=  283  W/L=  80/ 201  timeout=   2  winrate=  28%  expectancy=-0.28R  avg=-0.27R

  ── OOS 확신도별 ──
  high                   setups= 2231  filled= 1449  W/L= 445/ 974  timeout=  30  winrate=  31%  expectancy=-0.07R  avg=-0.01R
    └ 롱                    setups= 1452  filled=  925  W/L= 316/ 591  timeout=  18  winrate=  35%  expectancy=+0.11R  avg=+0.17R
    └ 숏                    setups=  779  filled=  524  W/L= 129/ 383  timeout=  12  winrate=  25%  expectancy=-0.39R  avg=-0.33R
  medium                 setups= 8046  filled= 5725  W/L=2166/3487  timeout=  72  winrate=  38%  expectancy=+0.13R  avg=+0.16R
    └ 롱                    setups= 5659  filled= 4048  W/L=1629/2360  timeout=  59  winrate=  41%  expectancy=+0.25R  avg=+0.28R
    └ 숏                    setups= 2387  filled= 1677  W/L= 537/1127  timeout=  13  winrate=  32%  expectancy=-0.15R  avg=-0.14R
  low                    setups= 3967  filled= 2336  W/L= 798/1487  timeout=  51  winrate=  35%  expectancy=+0.18R  avg=+0.23R
    └ 롱                    setups= 3967  filled= 2336  W/L= 798/1487  timeout=  51  winrate=  35%  expectancy=+0.18R  avg=+0.23R
```

**과최적화 폭 +0.05R (IS +0.17R − OOS +0.11R)**

비교 대상 — 팩터 가중치 워크포워드(`data/walkforward_result.json`)는 IS +0.0169 / OOS −0.0046, 과최적화 폭 +0.0215 였다.

창: 체결 20봉 · 보유 40봉 (러너와 같은 값)
생성: `python scripts/measure_trade_plan_oos.py`
