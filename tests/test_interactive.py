from __future__ import annotations

from pathlib import Path

from release_proof.interactive import (
    RECOMMENDATION_LABELS,
    _looks_vague_requirement,
    run_interactive,
)


def test_interactive_can_exit_without_starting_a_service() -> None:
    answers = iter(["0"])
    output: list[str] = []

    result = run_interactive(input_fn=lambda _: next(answers), output=output.append)

    assert result == 0
    assert any("AI 变更验收助手" in line for line in output)
    assert output[-1] == "已退出。"


def test_all_recommendations_have_plain_chinese_labels() -> None:
    assert len(RECOMMENDATION_LABELS) == 5
    assert all(RECOMMENDATION_LABELS.values())


def test_interactive_rejects_a_non_git_directory(tmp_path: Path) -> None:
    answers = iter(["3", str(tmp_path / "missing"), "n"])
    output: list[str] = []

    result = run_interactive(input_fn=lambda _: next(answers), output=output.append)

    assert result == 0
    assert "这不是 Git 仓库，请检查路径。" in output


def test_vague_requirement_warning_matches_user_facing_phrases() -> None:
    assert _looks_vague_requirement("AI 功能是完善的")
    assert _looks_vague_requirement("核心体验是完成了的")
    assert not _looks_vague_requirement("点击查询后显示答案、引用和版本号")
