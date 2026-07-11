"""
IC 기반 팩터 가중치 자동 업데이트
=====================================
매주 일요일 GitHub Actions (ic-update.yml)이 실행.
최근 5년 walk-forward IC를 계산하여 ic_weights.json에 저장.
factor_engine.py의 calc_factor_scores()가 이 파일을 읽어 가중치에 반영.

수동 실행:
  python ic_weight_updater.py
"""
import json
import sys
from datetime import datetime, timezone, timedelta

from modules.factor_engine import REGIME_WEIGHTS
from modules.factor_validator import run_per_factor_ic_analysis, run_out_of_sample_validation
from modules.survivorship_check import survivorship_bias_warning, known_failures_in_period

# 대표 유니버스 (다양한 섹터 포함)
TICKERS = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","JNJ",
    "UNH","XOM","PG","HD","MA","ABBV","MRK","KO","PEP","COST",
    "AVGO","LLY","WMT","MCD","CRM","ADBE","CSCO","ACN","TMO",
    "AMD","INTC","QCOM","NFLX","AMAT","MU","TXN","KLAC",
]

IC_WEIGHT_FILE = "ic_weights.json"
IC_FLOOR       = 0.005   # 팩터 IC가 음수/0일 때 최소 스케일 (완전 배제 방지)
LOOKBACK_YEARS = 5        # 2년 → 5년으로 확장 (더 강건한 IC 추정)


def derive_ic_regime_weights(per_factor_ic: dict, base_weights: dict) -> dict:
    """
    기존 REGIME_WEIGHTS를 per_factor_ic로 스케일링 후 재정규화.
    IC가 낮은 팩터는 가중치 감소, 높은 팩터는 증가.
    """
    ic_regime_weights = {}
    for regime, bw in base_weights.items():
        scaled = {}
        for factor, base in bw.items():
            ic_val = max(per_factor_ic.get(factor, {}).get("mean_ic", IC_FLOOR), IC_FLOOR)
            scaled[factor] = base * ic_val
        total = sum(scaled.values())
        ic_regime_weights[regime] = {f: round(v / total, 4) for f, v in scaled.items()}
    return ic_regime_weights


def progress_cb(pct: float):
    bar_len = 30
    filled  = int(bar_len * pct)
    bar     = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {pct*100:.0f}%", end="", flush=True)


def main():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"IC 가중치 업데이트 시작  {now_str}")
    print(f"유니버스: {len(TICKERS)}개 티커  |  lookback={LOOKBACK_YEARS}년  rebal=21일  forward=21일")
    print("데이터 수집 및 IC 계산 중 (30~60분 소요)...")

    per_factor = run_per_factor_ic_analysis(
        TICKERS,
        lookback_years=LOOKBACK_YEARS,
        rebal_days=21,
        forward_days=21,
        progress_cb=progress_cb,
    )
    print()  # progress bar 개행

    if not per_factor:
        print("[오류] IC 분석 실패 — ic_weights.json 업데이트 없음", file=sys.stderr)
        sys.exit(1)

    print("\n팩터별 IC 분석 결과:")
    print(f"  {'팩터':<12} {'mean_IC':>8} {'ICIR':>7} {'양(+)%':>7} {'n':>5}")
    print(f"  {'-'*44}")
    for f, stats in per_factor.items():
        sign = "✅" if stats["mean_ic"] > 0.02 else ("⚠️" if stats["mean_ic"] > 0 else "❌")
        print(f"  {f:<12} {stats['mean_ic']:>+8.4f} {stats['icir']:>7.3f} "
              f"{stats['pct_positive']:>7.1f}% {stats['n']:>5}  {sign}")

    ic_rw = derive_ic_regime_weights(per_factor, REGIME_WEIGHTS)
    print("\nIC 조정 가중치 (기존 → 신규):")
    for regime in REGIME_WEIGHTS:
        old_w = REGIME_WEIGHTS[regime]
        new_w = ic_rw[regime]
        print(f"  [{regime}]")
        for factor in old_w:
            delta = new_w[factor] - old_w[factor]
            print(f"    {factor:<12}: {old_w[factor]:.4f} → {new_w[factor]:.4f}  ({delta:+.4f})")

    # OOS 검증
    print(f"\n📊 OOS 검증 실행 중 (lookback={LOOKBACK_YEARS}년, test_fraction=0.25)...")
    oos = {}
    try:
        oos = run_out_of_sample_validation(
            TICKERS,
            lookback_years=LOOKBACK_YEARS,
            test_fraction=0.25,
            rebal_days=21,
            forward_days=21,
            progress_cb=progress_cb,
        )
        print()
        if oos:
            print(f"  학습 기간: {oos['train_period']['start']} ~ {oos['train_period']['end']}")
            print(f"  검증 기간: {oos['test_period']['start']}  ~ {oos['test_period']['end']}")
            print(f"  학습 composite IC: {oos['train_composite_ic']:+.4f}")
            print(f"  검증 composite IC: {oos['test_composite_ic']:+.4f}")
            if oos.get("overfitting_flag"):
                print("  ⚠️  과적합 의심: 검증 IC가 학습 IC의 50% 미만")
            else:
                print("  ✅ OOS 검증 통과 (과적합 없음)")
    except Exception as _e:
        print(f"\n  [경고] OOS 검증 실패: {_e}")

    # 생존자 편향 경고
    lookback_start = (datetime.now() - timedelta(days=LOOKBACK_YEARS * 365 + 90)).strftime("%Y-%m-%d")
    lookback_end   = datetime.now().strftime("%Y-%m-%d")
    warning  = survivorship_bias_warning(universe_size=len(TICKERS))
    failures = known_failures_in_period(lookback_start, lookback_end)
    if failures:
        print(f"\n⚠️  룩백 기간 내 알려진 실패 사례 {len(failures)}건:")
        for fail in failures:
            print(f"  [{fail['date']}] {fail['ticker']} — {fail['event']}")

    output = {
        "updated":               datetime.now(timezone.utc).isoformat(),
        "universe_size":         len(TICKERS),
        "lookback_years":        LOOKBACK_YEARS,
        "per_factor_ic":         per_factor,
        "regime_weights":        ic_rw,
        "out_of_sample_validation": oos,
        "caveats": {
            "survivorship_bias_warning":      warning,
            "known_failures_in_lookback":     failures,
        },
    }
    with open(IC_WEIGHT_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n✅ {IC_WEIGHT_FILE} 저장 완료.")


if __name__ == "__main__":
    main()
