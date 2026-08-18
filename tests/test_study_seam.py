"""우리가 만든 요청을 Claude API가 실제로 받아주는지 확인한다.

다른 테스트는 전부 가짜 client를 쓴다. 그래서 문서 블록의 모양이 틀려도,
금지된 항목을 넣어도 아무도 잡지 못한다. 이 파일만이 진짜로 보낸다.

픽스처는 **글자가 한 자도 없는 PDF**다 — 그림·수식·표가 전부 픽셀로만
그려져 있다. 그래서 요약에 내용이 나온다는 것은 API가 페이지를 이미지로
보고 있다는 뜻이고, 그것이 이 기능 전체의 전제다.

돈이 든다 (1회 수백 원). 기본으로는 건너뛰고, 확인하고 싶을 때만 켠다:

    RUN_PDF_SEAM=1 PYTHONUTF8=1 python -m pytest tests/test_study_seam.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from assistant.config import load_settings
from tools.study_reader import summarize_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "그림과수식.pdf"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PDF_SEAM") != "1",
    reason="진짜 API를 부르고 요금이 듭니다. RUN_PDF_SEAM=1 로 켜세요.",
)


def test_api_accepts_our_document_block():
    """요청 모양이 맞는지. 400이 나면 여기서 걸린다."""
    # Arrange
    settings = load_settings()

    # Act
    result = summarize_pdf(settings, FIXTURE)

    # Assert
    assert result["title"]
    assert result["one_line"]
    assert result["note_body"]


def test_the_model_actually_sees_the_picture_and_the_formula():
    """글자 추출로는 절대 못 얻는 것을 읽어냈는지.

    픽스처에는 추출 가능한 텍스트가 0바이트다. 요약이 수식이나 그래프를
    언급한다면 페이지를 이미지로 읽었다는 증거다.
    """
    # Arrange
    settings = load_settings()

    # Act
    result = summarize_pdf(settings, FIXTURE)
    whole = (result["title"] + result["one_line"] + result["note_body"]).lower()

    # Assert
    assert any(word in whole for word in
               ("mc", "제곱", "적분", "그래프", "곡선", "수식", "에너지")), whole
