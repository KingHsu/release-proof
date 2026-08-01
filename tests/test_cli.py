from __future__ import annotations

from pathlib import Path

import pytest

from release_proof.cli import _requirement_source, build_parser


def test_analyze_accepts_requirement_file_without_shell_quoting(tmp_path: Path) -> None:
    requirement_file = tmp_path / "release-issue.md"
    requirement_file.write_text(
        "- Health API returns ok\n- Rollback evidence is attached",
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "analyze",
            "repo",
            "--requirement-file",
            str(requirement_file),
        ]
    )

    source = _requirement_source(args)

    assert source.kind == "inline"
    assert source.path is None
    assert source.content == "- Health API returns ok\n- Rollback evidence is attached"
    assert source.source_uri is not None
    assert source.source_uri.startswith(
        "local-input://requirement/release-issue.md?sha256="
    )
    assert str(tmp_path) not in source.source_uri


def test_requirement_and_requirement_file_are_mutually_exclusive() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "analyze",
                "repo",
                "--requirement",
                "- works",
                "--requirement-file",
                "requirements/issue.md",
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["analyze", "repo"])


def test_requirement_file_rejects_nul_content(tmp_path: Path) -> None:
    requirement_file = tmp_path / "invalid.md"
    requirement_file.write_bytes(b"- valid prefix\x00hidden suffix")
    args = build_parser().parse_args(
        ["analyze", "repo", "--requirement-file", str(requirement_file)]
    )

    with pytest.raises(ValueError, match="NUL"):
        _requirement_source(args)
