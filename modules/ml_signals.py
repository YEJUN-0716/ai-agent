"""
④ 머신러닝 신호 결합
=================================================================
9개 기술지표를 feature로, "N일 후 상승/하락"을 label로 하는 분류 모델.

⚠️ 시계열에서 일반 K-Fold는 정보 유출이 생긴다.
   → Lopez de Prado의 Purged K-Fold + Embargo 적용:
     - Purge: 검증 구간과 label 기간이 겹치는 훈련 샘플 제거
     - Embargo: 검증 구간 이후 일정 기간 추가 제외
"""
import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


class PurgedKFold:
    """
    시계열 전용 K-Fold. 검증구간과 시간적으로 겹치는 훈련 샘플을 제거(purge)하고,
    검증구간 뒤 embargo 기간도 추가 제외한다.
    """
    def __init__(self, n_splits: int = 5, embargo_frac: float = 0.02,
                 label_horizon: int = 20):
        self.n_splits = n_splits
        self.embargo_frac = embargo_frac
        self.label_horizon = label_horizon

    def split(self, X: pd.DataFrame):
        n = len(X)
        indices = np.arange(n)
        fold_bounds = np.linspace(0, n, self.n_splits + 1).astype(int)
        embargo = int(n * self.embargo_frac)

        for i in range(self.n_splits):
            test_start, test_end = fold_bounds[i], fold_bounds[i + 1]
            test_idx = indices[test_start:test_end]
            purge_start = max(0, test_start - self.label_horizon)
            purge_end = min(n, test_end + self.label_horizon + embargo)
            train_idx = np.concatenate([indices[:purge_start], indices[purge_end:]])
            if len(train_idx) == 0 or len(test_idx) == 0:
                continue
            yield train_idx, test_idx


def build_features_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    app.py의 bt_signals_full 내부에서 쓰이는 기술지표들을 직접 계산해
    feature DataFrame으로 반환. (app.py 의존성 없이 독립 실행 가능)
    """
    close = df['Close']
    high = df['High'] if 'High' in df.columns else close
    low = df['Low'] if 'Low' in df.columns else close
    vol = df['Volume'] if 'Volume' in df.columns else pd.Series(1, index=df.index)

    feats = {}

    # 이동평균 정렬
    ma5   = close.rolling(5).mean()
    ma20  = close.rolling(20).mean()
    ma60  = close.rolling(60).mean()
    feats['ma_align'] = ((close > ma5).astype(int) +
                          (ma5 > ma20).astype(int) +
                          (ma20 > ma60).astype(int)) / 3.0

    # RSI(14)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feats['rsi'] = (gain / (gain + loss + 1e-9)) * 100

    # MACD 히스토그램
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9).mean()
    feats['macd_hist'] = macd - sig

    # 볼린저 밴드 위치
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    feats['bb_pos'] = ((close - bb_mid) / (2 * bb_std + 1e-9)).clip(-1, 1)

    # 거래량 상대강도
    feats['vol_ratio'] = (vol / vol.rolling(20).mean()).clip(0, 5)

    # 모멘텀
    feats['mom_20'] = close.pct_change(20) * 100
    feats['mom_5']  = close.pct_change(5) * 100

    # 고가/저가 위치
    feats['hl_pos'] = ((close - low.rolling(20).min()) /
                        (high.rolling(20).max() - low.rolling(20).min() + 1e-9)).clip(0, 1)

    # ATR 기반 변동성
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    feats['atr_ratio'] = (tr.rolling(14).mean() / close).clip(0, 0.1) * 100

    return pd.DataFrame(feats, index=df.index)


def make_labels(close: pd.Series, horizon: int = 20,
                threshold_pct: float = 0.0) -> pd.Series:
    """N일 후 수익률이 threshold_pct를 넘으면 1(상승), 아니면 0."""
    fwd_ret = close.pct_change(horizon).shift(-horizon) * 100
    return (fwd_ret > threshold_pct).astype(int)


def train_and_validate_ml_signal(df: pd.DataFrame, horizon: int = 20,
                                  n_splits: int = 5,
                                  embargo_frac: float = 0.02) -> dict:
    """
    Purged K-Fold로 out-of-fold 검증한 ML 신호의 AUC를 계산.
    df: OHLCV DataFrame (app.py의 download_stock 결과를 그대로 전달)
    """
    if not SKLEARN_AVAILABLE:
        raise ImportError("scikit-learn이 필요합니다: pip install scikit-learn")

    features = build_features_from_df(df)
    labels   = make_labels(df['Close'], horizon)
    data = pd.concat([features, labels.rename('label')], axis=1).dropna()

    if len(data) < n_splits * 30:
        raise ValueError(f"데이터 부족 (n={len(data)}). n_splits={n_splits}에 비해 너무 적음.")

    X = data.drop(columns=['label'])
    y = data['label']

    pkf = PurgedKFold(n_splits=n_splits, embargo_frac=embargo_frac,
                       label_horizon=horizon)
    fold_aucs = []
    oof_pred = pd.Series(np.nan, index=X.index)

    for train_idx, test_idx in pkf.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
        if y_train.nunique() < 2 or y_test.nunique() < 2:
            continue

        model = GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                            random_state=42)
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]
        fold_aucs.append(roc_auc_score(y_test, proba))
        oof_pred.iloc[test_idx] = proba

    if not fold_aucs:
        raise RuntimeError("모든 fold에서 유효한 AUC를 계산하지 못했습니다.")

    # 피처 중요도 (마지막 fold 모델 기준)
    feat_importance = dict(zip(X.columns, model.feature_importances_))

    return {
        'fold_aucs': [round(a, 4) for a in fold_aucs],
        'mean_auc': round(float(np.mean(fold_aucs)), 4),
        'std_auc': round(float(np.std(fold_aucs)), 4),
        'n_folds_used': len(fold_aucs),
        'oof_predictions': oof_pred,
        'feature_importance': {k: round(float(v), 4)
                                for k, v in sorted(feat_importance.items(),
                                                    key=lambda x: -x[1])},
        'interpretation': (
            "AUC 0.5 = 동전던지기, 1.0 = 완벽 예측. "
            "fold별 편차(std_auc)가 크면 특정 시기에만 통하는 불안정한 신호. "
            "0.55~0.60도 금융에서는 의미 있는 edge일 수 있음."
        ),
    }
