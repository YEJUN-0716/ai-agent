"""
시그널 디케이 분석 (IC 감쇠)
=================================================================
IC(Information Coefficient) = 시그널과 미래 수익률 사이의 순위상관.
보유기간(holding period)을 늘릴수록 IC가 얼마나 빠르게 떨어지는지 보면
"이 신호가 며칠 앞을 예측하는가"를 정량화할 수 있다.

⚠️ **겹치는 창은 독립 표본이 아니다.** 60일 수익률을 매일 계산하면 이웃한
관측이 59일치를 공유한다. 표본 수를 그대로 t 에 넣으면 유의성이 √h 배로
부풀고, 화면은 없는 엣지를 "유의함"으로 찍는다. 2026-08-20 실측(RSI14,
저장 패널 5년):

    AAPL 60일  IC -0.099   겹침 무시 p=0.0007  →  비겹침 p≈0.68
    NVDA 20일  IC +0.076   겹침 무시 p=0.0080  →  t=+0.58 (유의 아님)

18칸 중 3칸이 거짓 유의였다. 그래서 유효 표본을 **n/h**(겹치지 않는 블록
수)로 잡고 t·p 를 거기서 낸다. 보수적인 하한이다 — 진짜 유효 표본은 이보다
클 수 있지만, 틀리는 방향이 "덜 유의하게"여야 한다.
"""
import numpy as np
import pandas as pd
from scipy import stats


def compute_signal_ic_decay(signal: pd.Series, close: pd.Series,
                              horizons: list, method: str = 'spearman') -> pd.DataFrame:
    """
    horizons: [1, 5, 10, 20, 60] 등 일수 목록
    method: 'spearman'(순위 상관, 이상치 강건) or 'pearson'
    반환: horizon별 IC(상관계수), t통계량, p값, 관측수 n, 유효표본 n_eff

    t·p 는 scipy 가 주는 값이 아니라 **유효표본(n/h)** 으로 다시 낸 값이다
    (모듈 독스트링 참고). n 은 관측 수, n_eff 가 검정에 쓰인 수다.
    """
    rows = []
    for h in horizons:
        fwd_ret = close.shift(-h) / close - 1
        df_tmp = pd.concat([signal, fwd_ret], axis=1).dropna()
        df_tmp.columns = ['signal', 'fwd']
        n = len(df_tmp)
        if n < 10:
            rows.append({'horizon': h, 'IC': np.nan, 't_stat': np.nan,
                         'p_value': np.nan, 'n': n, 'n_eff': 0})
            continue
        if method == 'spearman':
            corr, _ = stats.spearmanr(df_tmp['signal'], df_tmp['fwd'])
        else:
            corr, _ = stats.pearsonr(df_tmp['signal'], df_tmp['fwd'])
        # 겹치는 창 보정: h봉마다 하나씩만 독립으로 센다.
        n_eff = max(n / h, 3.0)
        dof = n_eff - 2
        t_stat = corr * np.sqrt(dof) / np.sqrt(max(1 - corr ** 2, 1e-12))
        pval = 2 * float(stats.t.sf(abs(t_stat), df=dof))
        rows.append({'horizon': h, 'IC': round(float(corr), 4),
                     't_stat': round(float(t_stat), 3), 'p_value': round(pval, 4),
                     'n': n, 'n_eff': int(n_eff)})
    return pd.DataFrame(rows).set_index('horizon')


def demo() -> None:
    """겹침 보정이 실제로 걸리는지 — 신호 없는 데이터에서 유의가 나오면 실패."""
    rng = np.random.default_rng(0)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1500))))
    sig = pd.Series(rng.normal(0, 1, 1500))
    out = compute_signal_ic_decay(sig, close, [1, 20, 60])

    # 유효표본은 관측수가 아니라 겹치지 않는 블록 수여야 한다
    assert out.loc[60, 'n_eff'] < out.loc[60, 'n'] / 50, out.loc[60].to_dict()
    assert out.loc[1, 'n_eff'] == out.loc[1, 'n']

    # 같은 IC 라도 창이 길수록 t 가 작아져야 한다 (겹침을 벌준다)
    ic = 0.10
    t = [ic * np.sqrt(max(len(close) / h, 3.0) - 2) / np.sqrt(1 - ic ** 2)
         for h in (1, 20, 60)]
    assert t[0] > t[1] > t[2], t

    # 순수 잡음 신호가 유의하다고 나오면 안 된다
    assert (out['p_value'] > 0.05).all(), out
    print("signal_decay_analysis demo OK\n", out)


if __name__ == "__main__":
    demo()
