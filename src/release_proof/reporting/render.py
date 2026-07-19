from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from release_proof.domain.models import ReleaseAssessment

PROHIBITED_AUTHORIZATION = re.compile(r"(?i)\b(approved|merge now|deploy now)\b")


def _safe(text: str) -> str:
    return PROHIBITED_AUTHORIZATION.sub("[authorization wording removed]", text)


def render_markdown(report: ReleaseAssessment) -> str:
    lines = [
        "# ReleaseProof 验收报告",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- 建议：`{report.recommendation.value}`",
        f"- 路由：`{report.route}`",
        f"- 停止原因：`{report.stop_reason}`",
        "- 边界：本报告只辅助人工发布评审，不构成上线批准。",
        "",
        "## 变更摘要",
        "",
        f"- 文件数：{len(report.change_summary.changed_files)}",
        f"- 新增/删除行：+{report.change_summary.additions}/-{report.change_summary.deletions}",
        f"- 风险域：{', '.join(sorted(item.value for item in report.change_profile.risk_domains)) or 'none'}",
        "",
        "## 验收矩阵",
        "",
        "| 条件 | 状态 | 实现证据 | 验证证据 | 缺口 |",
        "|---|---|---:|---:|---|",
    ]
    for item in report.acceptance_matrix:
        missing = "; ".join(item.missing_evidence) or "-"
        criterion = item.criterion.replace("|", "\\|").replace("\n", " ")
        safe_missing = _safe(missing).replace("|", "\\|")
        lines.append(
            f"| {criterion} | `{item.status.value}` | {len(item.implementation_evidence)} | "
            f"{len(item.verification_evidence)} | {safe_missing} |"
        )
    lines.extend(["", "## 风险", ""])
    if not report.domain_risks:
        lines.append("- 未产生结构化领域风险；这不等于不存在风险。")
    for risk in report.domain_risks:
        lines.append(f"- **{risk.severity} / {risk.domain.value}**：{_safe(risk.statement)}")
    lines.extend(["", "## 缺失证据与人工检查", ""])
    if not report.human_checks:
        lines.append("- 当前证据包没有生成额外人工问题；仍需负责人完成最终评审。")
    for check in report.human_checks:
        marker = "阻断" if check.blocking else "确认"
        lines.append(f"- [{marker}] {_safe(check.question)} — {_safe(check.reason)}")
    lines.extend(["", "## 回滚提示", ""])
    for note in report.rollback_notes or ["未观察到足够材料生成回滚提示，请人工确认。"]:
        lines.append(f"- {_safe(note.strip())}")
    lines.extend(["", "## 证据索引", ""])
    for ref in report.evidence_index:
        lines.append(
            f"- `{ref.evidence_id}` · `{ref.kind.value}` · `{ref.locator}` · `{ref.content_hash[:12]}`"
        )
    lines.extend(["", "## 限制", ""])
    for limitation in report.limitations:
        lines.append(f"- {_safe(limitation)}")
    return "\n".join(lines).strip() + "\n"


@dataclass
class ReportWriter:
    reports_dir: Path

    def write(self, report: ReleaseAssessment) -> dict[str, str]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.reports_dir / f"{report.run_id}.json"
        markdown_path = self.reports_dir / f"{report.run_id}.md"
        json_path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(markdown_path)}
