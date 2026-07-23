"""
팩터 정의 비교 — 기존 IC 정의 vs 프로덕션 스캔 정의 (이중화 해소 2단계)
======================================================================
IC 파이프라인은 64/22봉 단순수익률·21봉 변동성으로 예측력을 재 왔고,
프로덕션 스캔은 12-1 모멘텀·252봉 변동성으로 랭킹한다. 즉 **주간 가중치가
프로덕션이 쓰지 않는 팩터의 IC 로 배분돼 왔다.**

이 스크립트는 두 정의를 **같은 실행·같은 리밸런싱 날짜·같은 종목 단면**
위에서 나란히 재고, 시점별 IC 차이로 짝지어(paired) 판정한다.
두 정의를 따로 돌려 mean_IC 를 비교하면 안 된다 — 재실행 노이즈가
IC 추정치 자체보다 크다(같은 조건 재실행에서 quality IC 가 25% 움직인 실측).

**측정 전용.** ic_weights.json 을 읽지도 쓰지도 않고 가중치를 바꾸지 않는다.
결과를 보고 표준 정의를 정하는 것이 3단계다.

수동 실행:
  python scripts/compare_factor_definitions.py               # 기본 5년, SP500
  python scripts/compare_factor_definitions.py 3             # 3년
  python scripts/compare_factor_definitions.py 5 out.json    # 결과 JSON 저장
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from modules.factor_validator import (
    PAIRED_T_THRESHOLD,
    run_factor_definition_comparison,
)
from modules.universe import SP500

TICKERS = SP500


def _print_report(res: dict) -> None:
    uni = res["universe"]
    win = res["window"]
    n_periods = len(res["dates"])

    print(f"\n유니버스 {uni['resolved']}/{uni['requested']}종목 | "
          f"lookback={win['lookback_years']}년 | "
          f"rebal={win['rebal_days']} forward={win['forward_days']} | "
          f"리밸런싱 {n_periods}회")
    print(f"측정 구간: {res['dates'][0]} ~ {res['dates'][-1]}")

    for p in res["pairs"]:
        print(f"\n── {p['label']} ────────────────────────────────────")
        print(f"  기존 IC 정의 ({p['legacy_desc']})")
        print(f"    mean_IC = {p['legacy_mean_ic']:+.4f}  ± {p['legacy_se']:.4f} (SE)")
        print(f"  프로덕션 정의 ({p['prod_desc']})")
        print(f"    mean_IC = {p['prod_mean_ic']:+.4f}  ± {p['prod_se']:.4f} (SE)")
        print(f"  짝지은 차이 (프로덕션 − 기존), n={p['n']}")
        print(f"    mean = {p['mean_diff']:+.4f}  SE = {p['se_diff']:.4f}  "
              f"t = {p['t_stat']:+.3f}")
        print(f"    프로덕션이 이긴 시점 = {p['pct_prod_wins']:.1f}%")
        print(f"  -> {p['verdict']}")

    print(f"\n[판정 기준] |t| >= {PAIRED_T_THRESHOLD} 여야 차이가 노이즈보다 크다.")
    print("[주의] 각 정의의 mean_IC 는 SE 안에 묻히는 게 정상이다. 판정은 t 로 한다.")
    undecided = [p["label"] for p in res["pairs"]
                 if abs(p["t_stat"]) < PAIRED_T_THRESHOLD]
    if undecided:
        print(f"[결론 보류] {', '.join(undecided)} — 근거 없이 구간을 합치지 말 것. "
              f"기간·유니버스를 늘려 재측정하거나 3단계를 미룬다.")


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"[compare_factor_definitions] 유니버스 {len(TICKERS)}종목 | "
          f"lookback={lookback}년")
    print("[compare_factor_definitions] 데이터 로딩 + walk-forward IC 계산 중...")
    print("[compare_factor_definitions] 프로덕션 정의 워밍업(253봉)을 "
          "lookback 창 앞쪽에 추가로 내려받는다 — 첫 실행은 오래 걸린다.")

    res = run_factor_definition_comparison(TICKERS, lookback_years=lookback)
    if not res:
        print("[compare_factor_definitions] 측정 실패 (데이터 부족)", file=sys.stderr)
        sys.exit(1)

    _print_report(res)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        print(f"\n[저장] {out_path}")


if __name__ == "__main__":
    main()
