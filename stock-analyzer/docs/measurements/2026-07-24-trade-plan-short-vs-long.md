# 트레이드 플랜 롱/숏 성능 측정 (2026-07-24)

- 유니버스: 276종목 (저장 패널 `data/price_panel_v1.parquet`)
- 구간: 각 종목 최근 400봉
- 체결 판정: 진입 구간에 되돌림 닿으면 체결, fill≤15봉 미도달이면 미체결
- 결판: 체결 후 hold≤30봉 내 손절/목표 선착, 같은 봉이면 손절 우선(보수적)
- R: 위험 1단위 기준. 목표=+R:R, 손절=-1.0, timeout=0

```
  전체  setups= 5280  filled= 3571  nofill= 1709  W/L=1427/1947  timeout= 197  winrate=  42%  expectancy=+0.61R  avg=+0.57R
  롱    setups= 3772  filled= 2538  nofill= 1234  W/L=1055/1332  timeout= 151  winrate=  44%  expectancy=+0.69R  avg=+0.65R
  숏    setups= 1508  filled= 1033  nofill=  475  W/L= 372/ 615  timeout=  46  winrate=  38%  expectancy=+0.40R  avg=+0.38R
  ── 확신도 × 방향 ──
  high   롱  setups=  541  filled=  349  nofill=  192  W/L= 144/ 184  timeout=  21  winrate=  44%  expectancy=+0.76R  avg=+0.72R
  high   숏  setups=  213  filled=  142  nofill=   71  W/L=  55/  79  timeout=   8  winrate=  41%  expectancy=+0.68R  avg=+0.64R
  medium 롱  setups= 1961  filled= 1434  nofill=  527  W/L= 634/ 730  timeout=  70  winrate=  46%  expectancy=+0.69R  avg=+0.66R
  medium 숏  setups=  873  filled=  618  nofill=  255  W/L= 235/ 356  timeout=  27  winrate=  40%  expectancy=+0.44R  avg=+0.42R
  low    롱  setups= 1270  filled=  755  nofill=  515  W/L= 277/ 418  timeout=  60  winrate=  40%  expectancy=+0.66R  avg=+0.60R
  low    숏  setups=  422  filled=  273  nofill=  149  W/L=  82/ 180  timeout=  11  winrate=  31%  expectancy=+0.16R  avg=+0.16R
```

생성: `python scripts/measure_trade_plan.py 400`
