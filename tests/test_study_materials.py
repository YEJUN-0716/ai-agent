from pathlib import Path

import pytest

from assistant.config import Settings
from tools.study_materials import (
    MAX_PDF_BYTES,
    StudyError,
    list_materials,
    inbox_pdf,
    list_new,
    material_path,
    save_material,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    inbox = tmp_path / "자료넣는곳"
    inbox.mkdir()
    (inbox / "처리완료").mkdir()
    vault = tmp_path / "볼트"
    (vault / "학업").mkdir(parents=True)
    data = tmp_path / "assistant"
    data.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=tmp_path,
        assistant_data_dir=data,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=inbox,
        obsidian_vault=vault,
    )


def test_lists_waiting_pdfs(settings):
    """자료넣는곳의 PDF를 찾아낸다."""
    # Arrange
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_skips_already_processed(settings):
    """처리완료 폴더는 건너뛴다. 안 그러면 매번 다시 정리한다."""
    # Arrange
    (settings.study_inbox / "새자료.pdf").write_bytes(b"%PDF-1.4 fake")
    (settings.study_done_dir / "지난자료.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["새자료.pdf"]


def test_skips_files_that_are_not_pdf(settings):
    """PDF가 아닌 파일은 목록에 넣지 않는다."""
    # Arrange
    (settings.study_inbox / "메모.txt").write_text("안녕", encoding="utf-8")
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_a_folder_named_like_a_pdf_is_not_listed(settings):
    """이름이 .pdf로 끝나는 폴더를 자료로 착각하면 안 된다.

    압축을 풀면 이런 폴더가 생길 수 있다. 파일인 줄 알고 열면 터진다.
    """
    # Arrange
    (settings.study_inbox / "압축풀린것.pdf").mkdir()
    (settings.study_inbox / "진짜.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["진짜.pdf"]


def test_marks_oversized_files_instead_of_hiding_them(settings):
    """32MB 초과도 목록에는 넣고 표시만 한다.

    목록에서 빼버리면 사장님은 자기가 넣은 파일이 왜 안 보이는지 알 수 없다.
    """
    # Arrange
    (settings.study_inbox / "큰자료.pdf").write_bytes(b"x" * (MAX_PDF_BYTES + 1))

    # Act
    waiting = list_new(settings)

    # Assert
    assert waiting[0]["too_large"] is True


def test_lists_alphabetically_for_a_stable_order(settings):
    """순서가 실행마다 바뀌면 목록을 믿기 어렵다."""
    # Arrange
    for name in ("다.pdf", "가.pdf", "나.pdf"):
        (settings.study_inbox / name).write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["가.pdf", "나.pdf", "다.pdf"]


def _put_pdf(settings, name="논문.pdf"):
    path = settings.study_inbox / name
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_saving_writes_note_moves_original_and_records_index(settings):
    """세 가지가 한 덩어리로 일어난다."""
    # Arrange
    _put_pdf(settings)

    # Act
    result = save_material(
        settings, "논문.pdf", "논문제목", "한 줄 요약", "## 핵심\n내용"
    )

    # Assert
    note = Path(result["note_path"])
    assert note.exists()
    assert "논문제목" in note.read_text(encoding="utf-8")
    assert not (settings.study_inbox / "논문.pdf").exists()
    assert (settings.study_done_dir / "논문.pdf").exists()
    assert [m["title"] for m in list_materials(settings)] == ["논문제목"]


def test_original_is_moved_not_deleted(settings):
    """원본은 절대 사라지지 않는다. 지우는 것은 사장님 몫이다."""
    # Arrange
    _put_pdf(settings)
    original = (settings.study_inbox / "논문.pdf").read_bytes()

    # Act
    save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Assert
    assert (settings.study_done_dir / "논문.pdf").read_bytes() == original


def test_note_name_collision_does_not_overwrite(settings):
    """같은 날 같은 제목이어도 앞의 노트를 덮어쓰지 않는다."""
    # Arrange
    _put_pdf(settings, "첫번째.pdf")
    _put_pdf(settings, "두번째.pdf")

    # Act
    first = save_material(settings, "첫번째.pdf", "같은제목", "하나", "본문A")
    second = save_material(settings, "두번째.pdf", "같은제목", "둘", "본문B")

    # Assert
    assert first["note_path"] != second["note_path"]
    assert "본문A" in Path(first["note_path"]).read_text(encoding="utf-8")
    assert "본문B" in Path(second["note_path"]).read_text(encoding="utf-8")


def test_nothing_is_left_behind_when_the_move_fails(settings, monkeypatch):
    """원본을 못 옮기면 노트도 목록도 남기지 않는다.

    반쪽만 남으면 다음 실행이 같은 자료를 또 정리해 노트가 둘이 된다.
    """
    # Arrange
    _put_pdf(settings)
    import tools.study_materials as mod

    def refuse_move(src, dst):
        raise OSError("옮기지 못했습니다")

    monkeypatch.setattr(mod.shutil, "move", refuse_move)

    # Act
    with pytest.raises(StudyError):
        save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Assert
    assert (settings.study_inbox / "논문.pdf").exists()
    assert list(settings.study_notes_dir.glob("*.md")) == []
    assert list_materials(settings) == []


def test_missing_source_is_reported_clearly(settings):
    """없는 파일을 정리하라고 하면 분명히 말한다."""
    # Act / Assert
    with pytest.raises(StudyError) as exc:
        save_material(settings, "없는파일.pdf", "제목", "한 줄", "본문")
    assert "없는파일.pdf" in str(exc.value)


def test_material_path_finds_the_archived_original(settings):
    """나중에 세부 질문을 받으면 보관된 원본을 찾아야 한다."""
    # Arrange
    _put_pdf(settings)
    result = save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Act
    found = material_path(settings, result["material_id"])

    # Assert
    assert found == settings.study_done_dir / "논문.pdf"


def test_material_path_rejects_an_unknown_id(settings):
    # Act / Assert
    with pytest.raises(StudyError):
        material_path(settings, "없는번호")


@pytest.mark.parametrize("filename", [
    "../../.env",
    "../token.json",
    "처리완료/논문.pdf",
    "메모.txt",
])
def test_rejects_paths_outside_the_inbox(settings, filename, tmp_path):
    """파일 이름은 모델이 정한다. 폴더 밖을 가리키면 읽지도 옮기지도 않는다."""
    # Arrange — 밖에 진짜 비밀 파일이 있어도 통과하면 안 된다.
    (tmp_path / ".env").write_text("ALPACA_KEY=진짜키", encoding="utf-8")
    (settings.study_inbox / "메모.txt").write_text("안녕", encoding="utf-8")

    # Act / Assert
    with pytest.raises(StudyError, match="자료넣는곳"):
        inbox_pdf(settings, filename)


def test_accepts_a_pdf_sitting_in_the_inbox(settings):
    """정상 파일은 그대로 통과한다 — 관문이 일까지 막으면 안 된다."""
    # Arrange
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act / Assert
    assert inbox_pdf(settings, "논문.pdf") == settings.study_inbox / "논문.pdf"
