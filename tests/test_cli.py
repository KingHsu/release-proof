from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import release_proof.cli as cli
from release_proof.cli import _requirement_source, build_parser, main


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


def test_llm_probe_requires_explicit_paid_call_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["probe-llm"]) == 2
    assert '"paid_api_calls": 0' in capsys.readouterr().out


def test_llm_probe_reports_one_bounded_successful_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        deepseek_api_key="configured-but-not-used",
        deepseek_base_url="https://example.invalid/anthropic",
        deepseek_model="deepseek-test",
        llm_timeout_seconds=10,
    )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["max_retries"] == 0

        def structured(self, **kwargs):
            assert kwargs["max_tokens"] == 128
            return kwargs["schema"](status="ok"), {
                "input_tokens": 5,
                "output_tokens": 2,
                "model": "deepseek-test",
            }

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "DeepSeekAnthropicClient", FakeClient)

    assert main(["probe-llm", "--confirm-paid-call"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["paid_api_calls"] == 1
    assert payload["usage"]["input_tokens"] == 5


def test_no_subcommand_opens_interactive_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    monkeypatch.setattr(
        "release_proof.interactive.run_interactive",
        lambda: called.append(True) or 0,
    )

    assert main([]) == 0
    assert called == [True]
