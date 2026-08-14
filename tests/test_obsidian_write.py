import pytest

from assistant.config import Settings
from tools.assistant_notes import read_audit_log
from tools.obsidian_write import WriteError, append_note, create_note


@pytest.fixture
def settings(tmp_path) -> Settings:
    vault = tmp_path / "볼트"
    (vault / "학업").mkdir(parents=True)
    data = tmp_path / "assistant"
    data.mkdir()
    inbox = tmp_path / "자료넣는곳"
    inbox.mkdir()
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


def test_creates_note_in_assistant_folder(settings):
    result = create_note(settings, "회의 정리", "첫 줄")

    assert result["path"] == "비서/회의 정리.md"
    saved = (settings.obsidian_vault / "비서" / "회의 정리.md").read_text("utf-8")
    assert saved == "# 회의 정리\n\n첫 줄\n"


def test_refuses_to_overwrite_existing_note(settings):
    create_note(settings, "회의 정리", "원본")

    with pytest.raises(WriteError, match="이미 있습니다"):
        create_note(settings, "회의 정리", "덮어쓰기 시도")

    saved = (settings.obsidian_vault / "비서" / "회의 정리.md").read_text("utf-8")
    assert "덮어쓰기 시도" not in saved


def test_append_keeps_original_body(settings):
    create_note(settings, "메모", "원래 있던 줄")
    append_note(settings, "비서/메모.md", "새로 붙인 줄", heading="추가")

    saved = (settings.obsidian_vault / "비서" / "메모.md").read_text("utf-8")
    assert "원래 있던 줄" in saved
    assert saved.index("원래 있던 줄") < saved.index("새로 붙인 줄")
    assert "추가" in saved


@pytest.mark.parametrize(
    "path",
    [
        "../../.env.md",  # 볼트 밖
        "학업/요약.md",  # 볼트 안이지만 비서 폴더 밖
        "메모.md",  # 볼트 최상위
    ],
)
def test_append_refuses_outside_assistant_folder(settings, path):
    target = settings.obsidian_vault / "학업" / "요약.md"
    target.write_text("정리해 둔 자료", encoding="utf-8")

    with pytest.raises(WriteError):
        append_note(settings, path, "끼워넣기")

    assert target.read_text("utf-8") == "정리해 둔 자료"


def test_create_refuses_path_escape_in_name(settings):
    with pytest.raises(WriteError, match="쓸 수 없는 글자"):
        create_note(settings, "../바깥", "본문")


def test_writes_are_recorded_in_audit_log(settings):
    create_note(settings, "메모", "본문")
    append_note(settings, "비서/메모.md", "덧붙임")

    log = read_audit_log(settings)
    assert [line.split(" | ")[1] for line in log] == ["note_append", "note_create"]
    assert "비서/메모.md" in log[0]
