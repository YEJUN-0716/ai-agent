# 트레이드 플랜 OOS 검증 (2026-08-09) — **폐기됨**

> **이 문서의 R 은 걸 수 없는 가격에서 나온 값이다. 인용하지 말 것.**
> 구간 상단만 닿아도 구간 **중간값**에 산 것으로 적고 있었다. 같은 셋업을
> 실제로 걸 수 있는 지정가로 다시 채점하면 OOS 기대값이 **+0.58R → +0.14R**
> 이다. 살아 있는 값은 `2026-08-16-trade-plan-oos.md`, 근거는
> `2026-08-12-entry-rule-daily.md`.

- 유니버스: 276종목 · 2020-03-31 ~ 2026-08-07 (저장 패널)
- 나눈 기준: **사람이 본 적 있는 구간인가.** 2026-07-24 측정이 쓴 '최근 400봉'(2024-12-20~)이 IS, 그 앞이 OOS
- 숏 레짐필터·저확신 컷은 IS 결과를 보고 넣은 규칙이다 — 그 구간에서 재면 잘 나오는 게 당연하다
- 백테스트 자체에는 미래 참조가 없다 (`build_trade_plan(df[:i+1])`)

```
  ── OOS (규칙을 정할 때 안 본 구간, ~2024-12-19) ──
  전체                     setups=15275  filled= 9807  W/L=3454/6041  timeout= 312  winrate=  36%  expectancy=+0.58R  avg=+0.57R
    └ 롱                    setups=12037  filled= 7675  W/L=2799/4606  timeout= 270  winrate=  38%  expectancy=+0.66R  avg=+0.64R
    └ 숏                    setups= 3238  filled= 2132  W/L= 655/1435  timeout=  42  winrate=  31%  expectancy=+0.30R  avg=+0.30R

  ── IS (필터를 고를 때 본 구간, 2024-12-20~) ──
  전체                     setups= 5449  filled= 3529  W/L=1209/2145  timeout= 175  winrate=  36%  expectancy=+0.57R  avg=+0.54R
    └ 롱                    setups= 4046  filled= 2587  W/L= 927/1514  timeout= 146  winrate=  38%  expectancy=+0.68R  avg=+0.65R
    └ 숏                    setups= 1403  filled=  942  W/L= 282/ 631  timeout=  29  winrate=  31%  expectancy=+0.27R  avg=+0.27R

  ── 연도별 (전체) ──
  2020                   setups= 1860  filled= 1133  W/L= 473/ 598  timeout=  62  winrate=  44%  expectancy=+0.92R  avg=+0.87R
    └ 롱                    setups= 1635  filled=  974  W/L= 428/ 488  timeout=  58  winrate=  47%  expectancy=+1.04R  avg=+0.98R
    └ 숏                    setups=  225  filled=  159  W/L=  45/ 110  timeout=   4  winrate=  29%  expectancy=+0.17R  avg=+0.17R
  2021                   setups= 3267  filled= 2070  W/L= 816/1195  timeout=  59  winrate=  41%  expectancy=+0.74R  avg=+0.72R
    └ 롱                    setups= 2775  filled= 1725  W/L= 714/ 956  timeout=  55  winrate=  43%  expectancy=+0.85R  avg=+0.82R
    └ 숏                    setups=  492  filled=  345  W/L= 102/ 239  timeout=   4  winrate=  30%  expectancy=+0.20R  avg=+0.19R
  2022                   setups= 3617  filled= 2579  W/L= 762/1734  timeout=  83  winrate=  31%  expectancy=+0.34R  avg=+0.33R
    └ 롱                    setups= 2542  filled= 1855  W/L= 525/1262  timeout=  68  winrate=  29%  expectancy=+0.31R  avg=+0.30R
    └ 숏                    setups= 1075  filled=  724  W/L= 237/ 472  timeout=  15  winrate=  33%  expectancy=+0.42R  avg=+0.41R
  2023                   setups= 3295  filled= 2064  W/L= 675/1334  timeout=  55  winrate=  34%  expectancy=+0.47R  avg=+0.45R
    └ 롱                    setups= 2400  filled= 1492  W/L= 506/ 941  timeout=  45  winrate=  35%  expectancy=+0.55R  avg=+0.53R
    └ 숏                    setups=  895  filled=  572  W/L= 169/ 393  timeout=  10  winrate=  30%  expectancy=+0.26R  avg=+0.26R
  2024                   setups= 3328  filled= 2024  W/L= 753/1217  timeout=  54  winrate=  38%  expectancy=+0.67R  avg=+0.66R
    └ 롱                    setups= 2727  filled= 1658  W/L= 642/ 971  timeout=  45  winrate=  40%  expectancy=+0.76R  avg=+0.74R
    └ 숏                    setups=  601  filled=  366  W/L= 111/ 246  timeout=   9  winrate=  31%  expectancy=+0.28R  avg=+0.27R
  2025                   setups= 3367  filled= 2190  W/L= 762/1334  timeout=  94  winrate=  36%  expectancy=+0.59R  avg=+0.57R
    └ 롱                    setups= 2421  filled= 1568  W/L= 566/ 930  timeout=  72  winrate=  38%  expectancy=+0.69R  avg=+0.66R
    └ 숏                    setups=  946  filled=  622  W/L= 196/ 404  timeout=  22  winrate=  33%  expectancy=+0.36R  avg=+0.34R
  2026                   setups= 1990  filled= 1276  W/L= 422/ 774  timeout=  80  winrate=  35%  expectancy=+0.53R  avg=+0.49R
    └ 롱                    setups= 1583  filled=  990  W/L= 345/ 572  timeout=  73  winrate=  38%  expectancy=+0.65R  avg=+0.60R
    └ 숏                    setups=  407  filled=  286  W/L=  77/ 202  timeout=   7  winrate=  28%  expectancy=+0.13R  avg=+0.13R

  ── OOS 확신도별 ──
  high                   setups= 2400  filled= 1491  W/L= 459/ 983  timeout=  49  winrate=  32%  expectancy=+0.44R  avg=+0.42R
    └ 롱                    setups= 1615  filled= 1002  W/L= 337/ 633  timeout=  32  winrate=  35%  expectancy=+0.58R  avg=+0.56R
    └ 숏                    setups=  785  filled=  489  W/L= 122/ 350  timeout=  17  winrate=  26%  expectancy=+0.14R  avg=+0.14R
  medium                 setups= 8648  filled= 5939  W/L=2197/3578  timeout= 164  winrate=  38%  expectancy=+0.59R  avg=+0.57R
    └ 롱                    setups= 6195  filled= 4296  W/L=1664/2493  timeout= 139  winrate=  40%  expectancy=+0.68R  avg=+0.66R
    └ 숏                    setups= 2453  filled= 1643  W/L= 533/1085  timeout=  25  winrate=  33%  expectancy=+0.35R  avg=+0.35R
  low                    setups= 4227  filled= 2377  W/L= 798/1480  timeout=  99  winrate=  35%  expectancy=+0.66R  avg=+0.63R
    └ 롱                    setups= 4227  filled= 2377  W/L= 798/1480  timeout=  99  winrate=  35%  expectancy=+0.66R  avg=+0.63R
```

**과최적화 폭 -0.01R (IS +0.57R − OOS +0.58R)**

비교 대상 — 팩터 가중치 워크포워드(`data/walkforward_result.json`)는 IS +0.0169 / OOS −0.0046, 과최적화 폭 +0.0215 였다.

생성: `python scripts/measure_trade_plan_oos.py`
