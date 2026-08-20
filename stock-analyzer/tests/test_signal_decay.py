"""겹치는 창 보정 — 화면이 없는 유의를 찍지 않는지."""
from modules.signal_decay_analysis import compute_signal_ic_decay, demo


def test_overlap_correction_self_check():
    demo()


def test_effective_sample_shrinks_with_horizon():
    """유효표본은 관측 수가 아니라 겹치지 않는 블록 수(n/h)다."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1200))))
    sig = pd.Series(rng.normal(0, 1, 1200))
    out = compute_signal_ic_decay(sig, close, [1, 10, 60])

    assert out.loc[1, 'n_eff'] == out.loc[1, 'n']
    assert out.loc[10, 'n_eff'] == int(out.loc[10, 'n'] / 10)
    assert out.loc[60, 'n_eff'] == int(out.loc[60, 'n'] / 60)
    # 창이 길수록 같은 IC 라도 덜 유의해야 한다 — 겹침을 벌준다
    assert out.loc[60, 'n_eff'] < out.loc[10, 'n_eff'] < out.loc[1, 'n_eff']
