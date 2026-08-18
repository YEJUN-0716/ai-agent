"""팩터 가중치 워크포워드 측정 — 백테스트 성과가 과최적화인지 실데이터로 채점한다.

무엇을 재는가
-------------
ic_weights.json 의 가중치는 전체 구간 IC 를 보고 정해진다. 그 가중치로 같은 구간을
평가하면 미래를 알고 짠 조합으로 과거를 채점하는 것이라 성과가 부풀려진다.
이 스크립트는 각 시점에서 **그 이전 관측만으로** 가중치를 다시 정해(확장 윈도우)
같은 기간을 채점하고, 전체구간 가중치 결과와 나란히 비교한다.

실행
----
    python scripts/measure_weight_walkforward.py                 # 프로덕션 유니버스, 5년
    python scripts/measure_weight_walkforward.py --years 3 --limit 100

결과는 data/walkforward_result.json 에 저장되고, 앱의 '전략 검증 · 리서치' 그룹
'워크포워드 검증' 모듈이 그 파일을 읽어 표시한다. 앱은 재계산하지 않는다 —
이 측정은 수 분이 걸린다.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 기본 콘솔(cp949)에서 판정 문장의 em dash 때문에 죽던 것을 막는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from modules import weight_walkforward as wf                      # noqa: E402
from modules.factor_validator import run_per_factor_ic_analysis   # noqa: E402

OUT_PATH = os.path.join("data", "walkforward_result.json")


def _universe(limit=None):
    """프로덕션이 스캔하는 유니버스를 그대로 쓴다 — 측정 대상과 실사용 대상을 일치시킨다.
    (다른 measure_* 스크립트와 같은 출처인 modules.universe.SP500)"""
    from modules.universe import SP500
    return list(SP500[:limit]) if limit else list(SP500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5, help="관측 기간(년)")
    ap.add_argument("--limit", type=int, default=None, help="유니버스 상한(테스트용)")
    ap.add_argument("--min-periods", type=int, default=wf.DEFAULT_MIN_PERIODS,
                    help="가중치를 처음 정하기까지 필요한 최소 관측 수")
    args = ap.parse_args()

    tickers = _universe(args.limit)
    print(f"[walkforward] 유니버스 {len(tickers)}종목 · {args.years}년 · "
          f"min_periods={args.min_periods}")

    raw = run_per_factor_ic_analysis(
        tickers, lookback_years=args.years,
        include_prod_defs=True, return_periods=True,
        progress_cb=lambda p: print(f"\r  진행 {p * 100:5.1f}%", end="", flush=True))
    print()

    periods = raw["periods"]
    if len(periods) <= args.min_periods:
        print(f"[walkforward] 관측 {len(periods)}기간 — min_periods({args.min_periods})보다 "
              f"짧아 검증 구간이 없습니다. --years 를 늘리세요.")
        return 1

    result = wf.walk_forward(periods, min_periods=args.min_periods)
    s = result["summary"]

    print("\n=== 워크포워드 결과 ===")
    print(f"관측 기간      : {len(periods)} (검증 {s['oos']['n']})")
    print(f"OOS  평균 IC   : {s['oos']['mean_ic']:+.4f}  "
          f"ICIR {s['oos']['icir']:+.3f}  t {s['oos']['t_stat']:+.2f}  "
          f"양의 IC {s['oos']['pct_positive']:.1f}%")
    print(f"IS   평균 IC   : {s['in_sample']['mean_ic']:+.4f}  "
          f"ICIR {s['in_sample']['icir']:+.3f}  t {s['in_sample']['t_stat']:+.2f}")
    print(f"과최적화 폭    : {s['overfit_gap']:+.4f}")
    print(f"판정           : {s['verdict']}")

    os.makedirs("data", exist_ok=True)
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(tickers),
        "lookback_years": args.years,
        "min_periods": args.min_periods,
        "n_periods": len(periods),
        "summary": s,
        "oos": result["oos"],
        "in_sample": result["in_sample"],
        "full_sample_weights": result["full_sample_weights"],
        "last_weights": result["weights_by_period"][-1] if result["weights_by_period"] else None,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
