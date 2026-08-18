"""애널리스트 점수 산식 — app.py 에서 추출한 뒤 동작이 같은지 고정."""
import ast
import inspect

import pytest

from modules import analyst_team as at


def test_chart_score_is_technical_70_momentum_30():
    assert at.chart_score(80.0, 40.0) == pytest.approx(80 * 0.7 + 40 * 0.3)


def test_ict_score_clips_to_0_100():
    assert at.ict_score(95.0, 20.0) == 100.0
    assert at.ict_score(5.0, -20.0) == 0.0
    assert at.ict_score(50.0, 5.0) == 55.0


def test_slugs_are_stable():
    """기록 파일의 키다 — 바꾸면 과거 기록과의 연결이 끊긴다."""
    assert at.ANALYST_SLUGS == ("chart", "quant", "ict")


def test_module_has_no_ui_dependency():
    """자동 경로에서 부를 수 있어야 한다 — streamlit·yfinance 를 끌고 오면 안 된다.

    본문 문자열이 아니라 **import 문**을 본다. 문서에 이름이 나오는 것과
    실제로 끌고 오는 것은 다르다.
    """
    tree = ast.parse(inspect.getsource(at))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "streamlit" not in imported
    assert "yfinance" not in imported


# ── 총괄 판정 블렌드 ─────────────────────────────────────────────────
#
# 화면(app.manager_consolidate)과 기록(signal_worker)이 같은 함수를 쓴다.
# 두 곳에 같은 식을 적으면 언젠가 갈라지고, 그러면 성적표가 화면과 다른 자로
# 재게 된다.

def test_blend_score_is_weighted_average():
    assert at.blend_score([80.0, 40.0], [75.0, 25.0]) == pytest.approx(70.0)


def test_blend_score_falls_back_to_equal_weight():
    """가중치를 못 읽었다고 판정 자체를 버리지는 않는다."""
    assert at.blend_score([80.0, 40.0], [0.0, 0.0]) == pytest.approx(60.0)


def test_blend_score_of_nothing_is_none():
    assert at.blend_score([], []) is None


def test_verdict_score_needs_all_three():
    """두 명만 섞은 값은 화면이 낸 판정이 아니다."""
    weights = {"chart": 40.0, "quant": 30.0, "ict": 30.0}
    assert at.verdict_score({"chart": 60.0, "ict": 70.0}, weights) is None
    assert at.verdict_score({"chart": 60.0, "quant": None, "ict": 70.0},
                            weights) is None


def test_verdict_score_matches_manager_blend():
    weights = {"chart": 50.0, "quant": 30.0, "ict": 20.0}
    got = at.verdict_score({"chart": 80.0, "quant": 40.0, "ict": 30.0}, weights)

    assert got == pytest.approx((80 * 50 + 40 * 30 + 30 * 20) / 100)
