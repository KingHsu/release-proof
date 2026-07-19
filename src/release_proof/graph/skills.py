from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from release_proof.domain.models import ChangeProfile, RiskDomain


class SkillValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path
    version: str = "1.0.0"


class SkillLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root.resolve()

    def discover(self) -> list[SkillMetadata]:
        if not self.skills_root.exists():
            return []
        skills: list[SkillMetadata] = []
        for skill_file in sorted(self.skills_root.glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                raise SkillValidationError(f"{skill_file} has no YAML frontmatter")
            try:
                _, frontmatter, _ = text.split("---", 2)
                payload = yaml.safe_load(frontmatter)
            except (ValueError, yaml.YAMLError) as exc:
                raise SkillValidationError(f"invalid frontmatter in {skill_file}") from exc
            if not isinstance(payload, dict) or not payload.get("name") or not payload.get("description"):
                raise SkillValidationError(f"{skill_file} needs name and description")
            skills.append(
                SkillMetadata(
                    name=str(payload["name"]),
                    description=str(payload["description"]),
                    version=str(
                        payload.get("version")
                        or (payload.get("metadata") or {}).get("version")
                        or "1.0.0"
                    ),
                    path=skill_file.parent,
                )
            )
        return skills

    def activate(self, profile: ChangeProfile) -> list[SkillMetadata]:
        available = {skill.name: skill for skill in self.discover()}
        selected: list[str] = ["release-readiness-review"]
        if RiskDomain.API_CONTRACT in profile.risk_domains:
            selected.append("api-compatibility-review")
        if RiskDomain.DATA_MIGRATION in profile.risk_domains:
            selected.append("database-migration-review")
        return [available[name] for name in selected if name in available]

    def read_instructions(self, skill: SkillMetadata, *, max_chars: int = 8000) -> str:
        skill_file = (skill.path / "SKILL.md").resolve(strict=True)
        try:
            skill_file.relative_to(self.skills_root)
        except ValueError as exc:
            raise SkillValidationError("skill path escaped skills root") from exc
        return skill_file.read_text(encoding="utf-8")[:max_chars]
