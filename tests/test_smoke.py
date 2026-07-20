"""pytest가 이 레포에서 동작하는지 확인하는 최소 테스트."""
import pyarrow
import pandas as pd


def test_pyarrow_roundtrip(tmp_path):
    """parquet 읽기/쓰기가 되는지 — price_panel의 전제 조건."""
    df = pd.DataFrame({"a": [1, 2, 3]}, index=pd.date_range("2026-01-01", periods=3))
    path = tmp_path / "t.parquet"
    df.to_parquet(path)
    assert pd.read_parquet(path).equals(df)
