"""
불스해적단 백본 팩터(bulls) IC 측정 — 측정 전용
================================================
run_per_factor_ic_analysis 로 bulls 팩터의 walk-forward IC 를 기존 6팩터와 함께
측정해 출력만 한다. **ic_weights.json 등 라이브 설정은 건드리지 않는다.**
(bulls-pirate-course-notes 방침: IC 검증 후에만 시그널 편입)

수동 실행:
  python scripts/measure_bulls_ic.py            # 기본 2년
  python scripts/measure_bulls_ic.py 3          # 3년
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from modules.factor_validator import run_per_factor_ic_analysis

# 유동성·데이터 커버리지 높은 섹터별 대표 종목 (첫 측정용 소규모 유니버스)
TICKERS = [
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "QCOM",
    "GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS",
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG",
    "WMT", "COST", "PG", "KO", "PEP", "PM",
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP",
    "GE", "CAT", "RTX", "XOM", "CVX", "LIN", "NEE",
]


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    print(f"[measure_bulls_ic] 유니버스 {len(TICKERS)}종목 | lookback={lookback}년 "
          f"| rebal=21 forward=21")
    print("[measure_bulls_ic] 데이터 로딩 + walk-forward IC 계산 중...")

    res = run_per_factor_ic_analysis(
        TICKERS, lookback_years=lookback, rebal_days=21, forward_days=21,
    )
    if not res:
        print("[measure_bulls_ic] 측정 실패 (데이터 부족)", file=sys.stderr)
        sys.exit(1)

    print("\n팩터별 IC (bulls = 이번 신규 팩터):")
    print(f"  {'factor':<10} {'mean_IC':>9} {'ICIR':>7} {'양(+)%':>7} {'n':>5}")
    print(f"  {'-'*42}")
    order = ["mom_3m", "mom_1m", "low_vol", "value", "quality", "ict", "bulls"]
    for f in order:
        s = res.get(f)
        if not s:
            continue
        mark = "  <<<" if f == "bulls" else ""
        print(f"  {f:<10} {s['mean_ic']:>+9.4f} {s['icir']:>7.3f} "
              f"{s['pct_positive']:>7.1f}% {s['n']:>5}{mark}")

    b = res.get("bulls", {})
    print("\n[판정 가이드] mean_IC > 0.02 = 유효, 0~0.02 = 미약, <=0 = 예측력 없음")
    if b:
        verdict = ("유효 -> 편입 검토" if b["mean_ic"] > 0.02
                   else "미약/무효 -> 편입 보류" if b["mean_ic"] > 0
                   else "예측력 없음/역방향 -> 편입 보류")
        print(f"[bulls] mean_IC={b['mean_ic']:+.4f}  ICIR={b['icir']:+.3f}  "
              f"양(+)={b['pct_positive']:.1f}%  n={b['n']}  ->  {verdict}")


if __name__ == "__main__":
    main()
