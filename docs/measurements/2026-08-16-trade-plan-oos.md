# 트레이드 플랜 OOS 검증 (2026-08-16)

- 유니버스: 279종목 · 2020-03-31 ~ 2026-08-07 (저장 패널)
- 나눈 기준: **사람이 본 적 있는 구간인가.** 2026-07-24 측정이 쓴 '최근 400봉'(2024-12-20~)이 IS, 그 앞이 OOS
- 숏 레짐필터·저확신 컷은 IS 결과를 보고 넣은 규칙이다 — 그 구간에서 재면 잘 나오는 게 당연하다
- 백테스트 자체에는 미래 참조가 없다 (`build_trade_plan(df[:i+1])`)
- **2026-08-16 정정 — 산 값이 바뀌었다.** 2026-08-09 판(+0.58R)은 구간 상단만 닿아도 구간 **중간값**에 산 것으로 적었다. 시장에 그 가격이 없었다. 지금은 러너가 실제로 거는 지정가(구간 상단, 갭이면 그날 시가)로 채점한다 — 셋업·체결 봉·승패는 그대로고 **R 만** 다시 낸 값이다 (`modules/trade_plan_backtest.placeable_r`, 근거는 `2026-08-12-entry-rule-daily.md`)

```
  ── OOS (규칙을 정할 때 안 본 구간, ~2024-12-19) ──
  전체                     setups=15275  filled= 9807  W/L=3454/6041  timeout= 312  winrate=  36%  expectancy=+0.14R  avg=+0.14R
    └ 롱                    setups=12037  filled= 7675  W/L=2799/4606  timeout= 270  winrate=  38%  expectancy=+0.22R  avg=+0.21R
    └ 숏                    setups= 3238  filled= 2132  W/L= 655/1435  timeout=  42  winrate=  31%  expectancy=-0.12R  avg=-0.12R

  ── IS (필터를 고를 때 본 구간, 2024-12-20~) ──
  전체                     setups= 5479  filled= 3549  W/L=1217/2156  timeout= 176  winrate=  36%  expectancy=+0.16R  avg=+0.16R
    └ 롱                    setups= 4075  filled= 2607  W/L= 935/1525  timeout= 147  winrate=  38%  expectancy=+0.27R  avg=+0.26R
    └ 숏                    setups= 1404  filled=  942  W/L= 282/ 631  timeout=  29  winrate=  31%  expectancy=-0.12R  avg=-0.12R

  ── 연도별 (전체) ──
  2020                   setups= 1860  filled= 1133  W/L= 473/ 598  timeout=  62  winrate=  44%  expectancy=+0.44R  avg=+0.42R
    └ 롱                    setups= 1635  filled=  974  W/L= 428/ 488  timeout=  58  winrate=  47%  expectancy=+0.55R  avg=+0.52R
    └ 숏                    setups=  225  filled=  159  W/L=  45/ 110  timeout=   4  winrate=  29%  expectancy=-0.21R  avg=-0.20R
  2021                   setups= 3267  filled= 2070  W/L= 816/1195  timeout=  59  winrate=  41%  expectancy=+0.27R  avg=+0.26R
    └ 롱                    setups= 2775  filled= 1725  W/L= 714/ 956  timeout=  55  winrate=  43%  expectancy=+0.37R  avg=+0.36R
    └ 숏                    setups=  492  filled=  345  W/L= 102/ 239  timeout=   4  winrate=  30%  expectancy=-0.26R  avg=-0.26R
  2022                   setups= 3617  filled= 2579  W/L= 762/1734  timeout=  83  winrate=  31%  expectancy=-0.09R  avg=-0.09R
    └ 롱                    setups= 2542  filled= 1855  W/L= 525/1262  timeout=  68  winrate=  29%  expectancy=-0.13R  avg=-0.13R
    └ 숏                    setups= 1075  filled=  724  W/L= 237/ 472  timeout=  15  winrate=  33%  expectancy=+0.02R  avg=+0.02R
  2023                   setups= 3295  filled= 2064  W/L= 675/1334  timeout=  55  winrate=  34%  expectancy=+0.03R  avg=+0.03R
    └ 롱                    setups= 2400  filled= 1492  W/L= 506/ 941  timeout=  45  winrate=  35%  expectancy=+0.11R  avg=+0.11R
    └ 숏                    setups=  895  filled=  572  W/L= 169/ 393  timeout=  10  winrate=  30%  expectancy=-0.19R  avg=-0.19R
  2024                   setups= 3328  filled= 2024  W/L= 753/1217  timeout=  54  winrate=  38%  expectancy=+0.26R  avg=+0.26R
    └ 롱                    setups= 2727  filled= 1658  W/L= 642/ 971  timeout=  45  winrate=  40%  expectancy=+0.36R  avg=+0.35R
    └ 숏                    setups=  601  filled=  366  W/L= 111/ 246  timeout=   9  winrate=  31%  expectancy=-0.17R  avg=-0.16R
  2025                   setups= 3377  filled= 2198  W/L= 764/1339  timeout=  95  winrate=  36%  expectancy=+0.19R  avg=+0.18R
    └ 롱                    setups= 2431  filled= 1576  W/L= 568/ 935  timeout=  73  winrate=  38%  expectancy=+0.28R  avg=+0.27R
    └ 숏                    setups=  946  filled=  622  W/L= 196/ 404  timeout=  22  winrate=  33%  expectancy=-0.06R  avg=-0.06R
  2026                   setups= 2010  filled= 1288  W/L= 428/ 780  timeout=  80  winrate=  35%  expectancy=+0.12R  avg=+0.12R
    └ 롱                    setups= 1602  filled= 1002  W/L= 351/ 578  timeout=  73  winrate=  38%  expectancy=+0.22R  avg=+0.20R
    └ 숏                    setups=  408  filled=  286  W/L=  77/ 202  timeout=   7  winrate=  28%  expectancy=-0.20R  avg=-0.19R

  ── OOS 확신도별 ──
  high                   setups= 2400  filled= 1491  W/L= 459/ 983  timeout=  49  winrate=  32%  expectancy=+0.00R  avg=+0.00R
    └ 롱                    setups= 1615  filled= 1002  W/L= 337/ 633  timeout=  32  winrate=  35%  expectancy=+0.16R  avg=+0.16R
    └ 숏                    setups=  785  filled=  489  W/L= 122/ 350  timeout=  17  winrate=  26%  expectancy=-0.33R  avg=-0.32R
  medium                 setups= 8648  filled= 5939  W/L=2197/3578  timeout= 164  winrate=  38%  expectancy=+0.15R  avg=+0.15R
    └ 롱                    setups= 6195  filled= 4296  W/L=1664/2493  timeout= 139  winrate=  40%  expectancy=+0.23R  avg=+0.22R
    └ 숏                    setups= 2453  filled= 1643  W/L= 533/1085  timeout=  25  winrate=  33%  expectancy=-0.06R  avg=-0.06R
  low                    setups= 4227  filled= 2377  W/L= 798/1480  timeout=  99  winrate=  35%  expectancy=+0.21R  avg=+0.20R
    └ 롱                    setups= 4227  filled= 2377  W/L= 798/1480  timeout=  99  winrate=  35%  expectancy=+0.21R  avg=+0.20R
```

**과최적화 폭 +0.02R (IS +0.16R − OOS +0.14R)**

비교 대상 — 팩터 가중치 워크포워드(`data/walkforward_result.json`)는 IS +0.0169 / OOS −0.0046, 과최적화 폭 +0.0215 였다.

생성: `python scripts/measure_trade_plan_oos.py`
