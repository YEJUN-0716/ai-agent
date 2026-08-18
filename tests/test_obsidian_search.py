import pytest

from assistant.config import Settings
from tools.obsidian_search import (
    MAX_LIMIT,
    SearchError,
    read_note,
    resolve_in_vault,
    search_notes,
)


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


def _note(settings, rel: str, text: str):
    path = settings.obsidian_vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_finds_note_by_body_text(settings):
    """본문에 든 단어로 노트를 찾는다."""
    # Arrange
    _note(settings, "학업/트랜스포머.md", "# 트랜스포머\n\n어텐션은 가중치다.\n")
    _note(settings, "딴것.md", "# 딴것\n\n관계없는 내용.\n")

    # Act
    result = search_notes(settings, "가중치")

    # Assert
    assert result["found"] == 1
    assert result["matches"][0]["path"] == "학업/트랜스포머.md"
    assert result["matches"][0]["title"] == "트랜스포머"


def test_matches_korean_word_with_particle_attached(settings):
    """조사가 붙어도 찾는다 — '어텐션'으로 '어텐션은'을 건진다."""
    # Arrange
    _note(settings, "노트.md", "# 노트\n\n어텐션은 핵심이다.\n")

    # Act
    result = search_notes(settings, "어텐션")

    # Assert
    assert result["found"] == 1
    assert result["matches"][0]["lines"] == ["어텐션은 핵심이다."]


def test_ranks_note_matching_all_terms_first(settings):
    """검색어를 다 품은 노트가, 한 단어만 여러 번 나오는 노트보다 위다."""
    # Arrange
    _note(settings, "둘다.md", "# 둘다\n\n어텐션 그리고 트랜스포머.\n")
    _note(settings, "하나만.md", "# 하나만\n\n어텐션 어텐션 어텐션 어텐션.\n")

    # Act
    result = search_notes(settings, "어텐션 트랜스포머")

    # Assert
    assert [m["path"] for m in result["matches"]] == ["둘다.md", "하나만.md"]
    assert result["matches"][0]["matched_terms"] == 2


def test_title_match_outranks_body_only_match(settings):
    """제목에 걸린 노트가 본문에만 있는 노트보다 위다."""
    # Arrange
    _note(settings, "제목.md", "# 손절 규칙\n\n내용.\n")
    _note(settings, "본문.md", "# 잡담\n\n손절 얘기가 여기 한 번.\n")

    # Act
    result = search_notes(settings, "손절")

    # Assert
    assert result["matches"][0]["path"] == "제목.md"


def test_skips_obsidian_config_folder(settings):
    """.obsidian 안의 파일은 노트가 아니므로 훑지 않는다."""
    # Arrange
    _note(settings, ".obsidian/plugins/설정.md", "비밀번호 토큰")
    _note(settings, "진짜노트.md", "# 진짜노트\n\n토큰 이야기.\n")

    # Act
    result = search_notes(settings, "토큰")

    # Assert
    assert result["scanned_notes"] == 1
    assert [m["path"] for m in result["matches"]] == ["진짜노트.md"]


def test_reports_nothing_found_instead_of_empty_silence(settings):
    """못 찾으면 못 찾았다고 말한다 — 지어내지 않게."""
    # Arrange
    _note(settings, "노트.md", "# 노트\n\n내용.\n")

    # Act
    result = search_notes(settings, "없는단어")

    # Assert
    assert result["matches"] == []
    assert "찾지 못했습니다" in result["note"]


def test_caps_result_count(settings):
    """결과 개수에 상한이 있다 — 대화에 통째로 들어가므로."""
    # Arrange
    for i in range(MAX_LIMIT + 5):
        _note(settings, f"노트{i}.md", "# 노트\n\n공통단어\n")

    # Act
    result = search_notes(settings, "공통단어", limit=99)

    # Assert
    assert result["found"] == MAX_LIMIT + 5
    assert len(result["matches"]) == MAX_LIMIT


def test_empty_query_is_rejected(settings):
    """검색어가 비면 볼트 전체를 쏟아내지 않고 거절한다."""
    with pytest.raises(SearchError):
        search_notes(settings, "   ")


def test_reads_note_found_by_search(settings):
    """검색으로 찾은 경로를 그대로 넣으면 본문이 나온다."""
    # Arrange
    _note(settings, "학업/자료.md", "# 자료\n\n본문 전체.\n")

    # Act
    result = read_note(settings, "학업/자료.md")

    # Assert
    assert result["title"] == "자료"
    assert "본문 전체." in result["text"]
    assert result["truncated"] is False


def test_rejects_path_escaping_the_vault(settings, tmp_path):
    """볼트 밖은 못 연다 — 이게 뚫리면 .env가 통째로 샌다."""
    # Arrange
    (tmp_path / "비밀.md").write_text("ANTHROPIC_API_KEY=sk-fake", encoding="utf-8")

    # Act & Assert
    with pytest.raises(SearchError, match="볼트 밖"):
        read_note(settings, "../비밀.md")


def test_rejects_absolute_path_outside_vault(settings, tmp_path):
    """절대경로로 우회하는 것도 막는다."""
    # Arrange
    secret = tmp_path / "비밀.md"
    secret.write_text("비밀", encoding="utf-8")

    # Act & Assert
    with pytest.raises(SearchError, match="볼트 밖"):
        read_note(settings, str(secret))


def test_rejects_non_markdown_file(settings):
    """볼트 안이어도 노트(.md)가 아니면 열지 않는다."""
    # Arrange
    (settings.obsidian_vault / ".env").write_text("KEY=1", encoding="utf-8")

    # Act & Assert
    with pytest.raises(SearchError, match="노트"):
        read_note(settings, ".env")


def test_rejects_missing_note(settings):
    """없는 노트는 없다고 말한다."""
    with pytest.raises(SearchError, match="없습니다"):
        read_note(settings, "없는노트.md")


def test_resolves_path_inside_vault(settings):
    """볼트 안 경로는 실제 파일로 풀린다."""
    # Arrange
    path = _note(settings, "학업/자료.md", "# 자료\n")

    # Act
    resolved = resolve_in_vault(settings, "학업/자료.md")

    # Assert
    assert resolved == path.resolve()
