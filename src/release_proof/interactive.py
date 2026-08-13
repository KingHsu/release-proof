from __future__ import annotations

import io
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from release_proof.config import get_settings
from release_proof.domain.models import (
    AnalysisRequest,
    AnalysisRun,
    Recommendation,
    RequirementSource,
    ResumeRequest,
    RunStatus,
)
from release_proof.graph.service import ReleaseProofService

Input = Callable[[str], str]
Output = Callable[[str], None]

RECOMMENDATION_LABELS = {
    Recommendation.READY_FOR_HUMAN_REVIEW: "可以进入人工评审",
    Recommendation.CONDITIONAL: "有条件进入人工评审",
    Recommendation.NOT_READY: "证据不足，暂不建议发布",
    Recommendation.INSUFFICIENT_EVIDENCE: "证据不足",
    Recommendation.ANALYSIS_FAILED: "分析失败",
}


def _project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        if (candidate / "skills").is_dir() and (candidate / "scripts").is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()


def _ask(input_fn: Input, prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input_fn(f"{prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or default


def _print_header(output: Output) -> None:
    output("")
    output("ReleaseProof · AI 变更验收助手")
    output("只读取需求、Git 差异和测试证据；不会修改代码或批准发布。")
    output("=" * 62)


def _print_run(run: AnalysisRun, output: Output, reports_dir: Path) -> None:
    output("")
    output(f"运行状态：{run.status.value}  ·  Run ID：{run.run_id}")
    if run.errors:
        for item in run.errors:
            output(f"  ! {item}")
    if run.trace:
        output("")
        output("Agent 取证过程")
        visible = [
            event
            for event in run.trace
            if event.node
            in {
                "extract_acceptance_criteria",
                "choose_next_action",
                "validate_and_execute_readonly_tool",
                "policy_gate_and_report",
            }
        ]
        for index, event in enumerate(visible, start=1):
            if event.node == "extract_acceptance_criteria":
                summary = "从需求中提取验收条件"
            elif event.node == "choose_next_action" and event.tool:
                summary = f"Planner 选择只读工具：{event.tool}"
            elif event.node == "choose_next_action":
                summary = "Planner 判断取证可以结束"
            elif event.node == "validate_and_execute_readonly_tool":
                summary = (
                    f"Harness 已安全执行：{event.tool}"
                    if event.status == "completed"
                    else f"Harness 拒绝执行：{event.tool}"
                )
            else:
                summary = "确定性 Policy Gate 生成最终建议"
            output(f"  {index}. {summary}")
    if run.interrupt is not None:
        output("")
        output("还需要补充材料：")
        for item in run.interrupt.requested_inputs:
            output(f"  - {item}")
        return
    if run.report is None:
        return

    report = run.report
    output("")
    output(f"验收建议：{RECOMMENDATION_LABELS[report.recommendation]}")
    output("（这只是证据建议，最终发布决定始终由人完成。）")
    output("")
    output("验收条件")
    for item in report.acceptance_matrix:
        output(
            f"  - [{item.status.value}] {item.criterion} "
            f"· 实现证据 {len(item.implementation_evidence)} "
            f"· 验证证据 {len(item.verification_evidence)}"
        )
        for missing in item.missing_evidence:
            output(f"      缺口：{missing}")
    if report.human_checks:
        output("")
        output("需要人工确认")
        for check in report.human_checks:
            output(f"  - {check.question}")
    output("")
    output(f"完整报告：{reports_dir / f'{run.run_id}.md'}")


def _service(*, online: bool) -> ReleaseProofService:
    settings = get_settings().model_copy(
        update={
            "release_proof_offline": not online,
            "llm_max_retries": 0 if online else 2,
            "release_proof_max_llm_calls": 4 if online else 6,
            "release_proof_max_output_tokens": 1200 if online else 1800,
        }
    )
    return ReleaseProofService(settings, project_root=_project_root())


def _finish_interrupted(
    service: ReleaseProofService,
    run: AnalysisRun,
    input_fn: Input,
    output: Output,
) -> AnalysisRun:
    while run.status == RunStatus.AWAITING_INPUT:
        _print_run(run, output, service.settings.generated_reports_dir)
        output("")
        answer = _ask(
            input_fn,
            "输入仓库内测试报告路径；没有则输入 continue 生成不完整报告",
            "continue",
        )
        resume = (
            ResumeRequest(continue_without_reports=True)
            if answer.casefold() == "continue"
            else ResumeRequest(report_paths=[answer])
        )
        run = service.resume(run.run_id, resume)
    return run


def _run_analysis(
    request: AnalysisRequest,
    *,
    online: bool,
    input_fn: Input,
    output: Output,
) -> None:
    service = _service(online=online)
    try:
        output("")
        output("正在只读分析，请稍候……")
        run = service.start(request)
        run = _finish_interrupted(service, run, input_fn, output)
        _print_run(run, output, service.settings.generated_reports_dir)
    finally:
        service.close()


def _create_demo_repository(root: Path) -> Path:
    repository = root / "runtime" / "interactive-demo" / f"run-{uuid.uuid4().hex[:8]}"
    source = repository / "src" / "api" / "health.py"
    source.parent.mkdir(parents=True)

    def git(*arguments: str) -> None:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "git demo command failed")

    git("init", "-q")
    git("config", "user.name", "ReleaseProof Demo")
    git("config", "user.email", "release-proof@example.invalid")
    source.write_text(
        "def health_api():\n    return {'status': 'starting'}\n",
        encoding="utf-8",
    )
    git("add", "src/api/health.py")
    git("commit", "-q", "-m", "initial health endpoint")
    source.write_text("def health_api():\n    return {'status': 'ok'}\n", encoding="utf-8")
    git("add", "src/api/health.py")
    git("commit", "-q", "-m", "return ok health status")
    reports = repository / "reports"
    reports.mkdir()
    (reports / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="demo" tests="1" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" time="0.01" />
</testsuite>
""",
        encoding="utf-8",
    )
    return repository


def _run_demo(input_fn: Input, output: Output) -> None:
    root = _project_root()
    try:
        repository = _create_demo_repository(root)
    except (OSError, RuntimeError) as exc:
        output(f"演示仓库创建失败：{type(exc).__name__}")
        return
    _run_analysis(
        AnalysisRequest(
            repository_path=str(repository),
            base_ref="HEAD~1",
            head_ref="HEAD",
            requirement_source=RequirementSource(
                kind="inline",
                content="- Health API returns an ok status",
            ),
            report_paths=["reports/junit.xml"],
            mode="single",
        ),
        online=False,
        input_fn=input_fn,
        output=output,
    )


def _run_repository(input_fn: Input, output: Output) -> None:
    repository = Path(_ask(input_fn, "Git 仓库路径", str(Path.cwd()))).resolve()
    git_check = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if git_check.returncode or git_check.stdout.strip() != "true":
        output("这不是 Git 仓库，请检查路径。")
        return
    requirement = _ask(input_fn, "用一句话描述验收要求")
    if not requirement:
        output("验收要求不能为空。")
        return
    reports = _ask(input_fn, "测试报告路径（仓库内路径，可留空）")
    online_answer = _ask(input_fn, "使用真实 DeepSeek Planner？输入 y 才会继续", "n")
    online = online_answer.casefold() == "y"
    if online:
        output("真实模式会产生最多 4 次模型请求。")
        if _ask(input_fn, "请输入 ONLINE 确认费用") != "ONLINE":
            output("未确认费用，已切换为零费用离线模式。")
            online = False
    _run_analysis(
        AnalysisRequest(
            repository_path=str(repository),
            base_ref="HEAD~1",
            head_ref="HEAD",
            requirement_source=RequirementSource(kind="inline", content=requirement),
            report_paths=[reports] if reports else [],
            mode="auto",
            continue_without_reports=not reports,
        ),
        online=online,
        input_fn=input_fn,
        output=output,
    )


def run_interactive(
    *,
    input_fn: Input = input,
    output: Output = print,
) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    while True:
        _print_header(output)
        output("1. 体验内置示例（零费用，推荐第一次使用）")
        output("2. 验收本地 Git 项目")
        output("0. 退出")
        choice = _ask(input_fn, "请选择", "1")
        if choice == "0":
            output("已退出。")
            return 0
        if choice == "1":
            _run_demo(input_fn, output)
        elif choice == "2":
            _run_repository(input_fn, output)
        else:
            output("请输入 0、1 或 2。")
            continue
        if _ask(input_fn, "继续使用？(y/N)", "n").casefold() != "y":
            return 0


__all__ = ["run_interactive"]
