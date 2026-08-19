"""
② 통계적 유의성 검증 (과최적화 방지)
=================================================================
백테스트에서 좋은 Sharpe가 나와도, 파라미터를 여러 번 튜닝했다면
그중 하나는 "우연히" 좋아 보일 확률이 높다 (multiple testing problem).
이 모듈은 세 가지 검증 도구를 제공한다:

1. Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014)
2. Block Bootstrap 신뢰구간
3. Permutation Test (순열검정)
"""
import numpy as np
from scipy import stats


def deflated_sharpe_ratio(observed_sharpe: float, n_trials: int, n_obs: int,
                           skew: float = 0.0, kurtosis: float = 3.0,
                           benchmark_sharpe: float = 0.0) -> dict:
    """
    observed_sharpe : 관측된 **per-period(일별) Sharpe — 연율화하지 말 것**.
                      n_obs 와 같은 주기여야 한다. 연율화 값(√252 배)을 넘기면
                      기준선(expected_max_sharpe)만 일별 척도로 남아 z 가 20 가까이
                      나오고, 무엇을 넣든 dsr≈1.0 "유의미" 가 찍힌다 — 과최적화를
                      잡으라고 만든 장치가 아무것도 못 잡게 된다.
    n_trials        : 파라미터 튜닝 시도 횟수 (buy_th×sell_th 조합 수 등)
    n_obs           : 백테스트 일별 관측치 수
    """
    if n_trials < 1:
        n_trials = 1
    euler_gamma = 0.5772156649
    if n_trials > 1:
        expected_max_sharpe = (
            (1 - euler_gamma) * stats.norm.ppf(1 - 1.0 / n_trials) +
            euler_gamma * stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
        ) / np.sqrt(n_obs)
    else:
        expected_max_sharpe = 0.0
    expected_max_sharpe += benchmark_sharpe

    sr_std = np.sqrt(max(
        (1 - skew * observed_sharpe + (kurtosis - 1) / 4 * observed_sharpe**2) / (n_obs - 1),
        1e-12
    ))
    z = (observed_sharpe - expected_max_sharpe) / sr_std
    dsr = float(stats.norm.cdf(z))
    return {
        'expected_max_sharpe_by_chance': round(float(expected_max_sharpe), 3),
        'dsr_probability': round(dsr, 4),
        'is_significant_95pct': dsr > 0.95,
        'interpretation': (
            f"{n_trials}번 파라미터를 시도했다면, 순전히 운으로 Sharpe "
            f"{expected_max_sharpe:.2f} 정도는 나올 수 있음. 관측된 Sharpe가 "
            f"이보다 통계적으로 유의미하게 높을 확률(DSR) = {dsr*100:.1f}%"
        ),
    }


def block_bootstrap_sharpe_ci(daily_returns: np.ndarray, n_boot: int = 1000,
                               block_size: int = 20, ci: float = 0.90,
                               seed: int = None) -> dict:
    """
    블록 부트스트랩으로 연율화 Sharpe의 신뢰구간 추정.
    자기상관을 보존하기 위해 iid 리샘플링 대신 블록 단위로 재표본.
    """
    r = np.asarray(daily_returns)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < block_size * 3:
        raise ValueError(f"데이터 부족 (n={n}). block_size={block_size}의 3배 이상 필요.")

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_size))
    boot_sharpes = np.empty(n_boot)

    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate([
            np.take(r, np.arange(s, s + block_size) % n) for s in starts
        ])[:n]
        mu, sig = sample.mean(), sample.std()
        boot_sharpes[b] = (mu / sig * np.sqrt(252)) if sig > 0 else 0.0

    lo_pct = (1 - ci) / 2 * 100
    hi_pct = (1 + ci) / 2 * 100
    return {
        'point_estimate_sharpe': round(float(r.mean() / r.std() * np.sqrt(252)), 3) if r.std() > 0 else 0.0,
        f'ci_{int(ci*100)}pct_low': round(float(np.percentile(boot_sharpes, lo_pct)), 3),
        f'ci_{int(ci*100)}pct_high': round(float(np.percentile(boot_sharpes, hi_pct)), 3),
        'prob_sharpe_below_0': round(float(np.mean(boot_sharpes < 0)), 4),
        'n_boot': n_boot,
    }


def permutation_test_trades(trade_pnls: np.ndarray, n_perm: int = 2000,
                             seed: int = None) -> dict:
    """
    실제 매매 손익의 총합이, 같은 크기의 승/패를 무작위로 조합했을 때보다
    통계적으로 유의미하게 좋은지 검정.
    """
    pnls = np.asarray(trade_pnls, dtype=float)
    pnls = pnls[~np.isnan(pnls)]
    n = len(pnls)
    if n < 5:
        raise ValueError(f"거래 수 부족 (n={n}). 최소 5건 이상 필요.")

    observed_total = float(pnls.sum())
    rng = np.random.default_rng(seed)
    perm_totals = np.empty(n_perm)
    abs_pnls = np.abs(pnls)

    for i in range(n_perm):
        random_signs = rng.choice([-1, 1], size=n)
        perm_totals[i] = float((abs_pnls * random_signs).sum())

    p_value = float(np.mean(perm_totals >= observed_total))
    return {
        'observed_total_pnl_pct': round(observed_total, 2),
        'random_mean_pnl_pct': round(float(perm_totals.mean()), 2),
        'p_value': round(p_value, 4),
        'is_significant_95pct': p_value < 0.05,
        'interpretation': (
            f"실제 누적 손익 {observed_total:+.1f}%가, 같은 크기의 승/패를 무작위 "
            f"순서로 조합했을 때보다 좋을 확률(p-value) = {p_value:.3f}. "
            f"{'유의미함 (p<0.05)' if p_value < 0.05 else '우연과 통계적으로 구분 안 됨'}"
        ),
    }
