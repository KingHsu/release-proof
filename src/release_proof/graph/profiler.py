from __future__ import annotations

from pathlib import PurePosixPath

from release_proof.domain.models import ChangeProfile, ChangeSummary, RiskDomain

API_HINTS = ("openapi", "swagger", "routes/", "controllers/", "schemas/", "api/")
MIGRATION_HINTS = ("migration", "migrations/", "alembic/", "schema.sql", "ddl/")
TEST_HINTS = ("test_", "tests/", "/test/", ".spec.", ".test.")
CONFIG_HINTS = (
    "dockerfile",
    "docker-compose",
    ".github/workflows/",
    "deploy/",
    "helm/",
    "k8s/",
    "config/",
    ".env.example",
)
ASYNC_HINTS = ("tasks/", "jobs/", "worker", "scheduler", "celery", "cron")
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}


def profile_change(summary: ChangeSummary) -> ChangeProfile:
    paths = [path.replace("\\", "/").lower() for path in summary.changed_files]
    domains: set[RiskDomain] = set()
    if paths and all(PurePosixPath(path).suffix in DOC_SUFFIXES for path in paths):
        domains.add(RiskDomain.DOCS_ONLY)
    else:
        if any(any(hint in path for hint in API_HINTS) for path in paths):
            domains.add(RiskDomain.API_CONTRACT)
        if any(any(hint in path for hint in MIGRATION_HINTS) or path.endswith(".sql") for path in paths):
            domains.add(RiskDomain.DATA_MIGRATION)
        if any(any(hint in path for hint in TEST_HINTS) for path in paths):
            domains.add(RiskDomain.TESTS)
        if any(any(hint in path for hint in CONFIG_HINTS) for path in paths):
            domains.add(RiskDomain.CONFIG_DEPLOYMENT)
        if any(any(hint in path for hint in ASYNC_HINTS) for path in paths):
            domains.add(RiskDomain.SCHEDULED_ASYNC)
        source_paths = [
            path
            for path in paths
            if PurePosixPath(path).suffix in {".py", ".java", ".go", ".js", ".ts", ".rs", ".cs"}
            and not any(hint in path for hint in TEST_HINTS)
        ]
        domain_specific_source = any(
            any(hint in path for hint in (*API_HINTS, *ASYNC_HINTS)) for path in source_paths
        )
        if (source_paths and not domain_specific_source) or not domains:
            domains.add(RiskDomain.BUSINESS_LOGIC)
    independent = domains - {RiskDomain.TESTS, RiskDomain.DOCS_ONLY}
    multi = len(independent) >= 2 and RiskDomain.DOCS_ONLY not in domains
    reasons = [f"deterministic path rules detected {domain.value}" for domain in sorted(domains, key=str)]
    if multi:
        reasons.append("at least two independent risk domains can be assessed separately")
    else:
        reasons.append("single flow is sufficient for this change profile")
    return ChangeProfile(
        changed_files=len(summary.changed_files),
        changed_lines=summary.additions + summary.deletions,
        risk_domains=domains,
        requires_human_input=False,
        recommended_mode="multi" if multi else "single",
        reasons=reasons,
    )


def choose_route(profile: ChangeProfile, requested_mode: str) -> tuple[str, list[str]]:
    independent = profile.risk_domains - {RiskDomain.TESTS, RiskDomain.DOCS_ONLY}
    eligible = len(independent) >= 2 and RiskDomain.DOCS_ONLY not in profile.risk_domains
    if requested_mode == "single":
        return "single", ["single mode explicitly requested"]
    if requested_mode == "multi" and eligible:
        return "multi", ["multi mode requested and deterministic eligibility gate passed"]
    if requested_mode == "multi" and not eligible:
        return "single", ["multi mode requested but deterministic eligibility gate rejected it"]
    return (
        ("multi", ["auto route enabled specialists for independent risk domains"])
        if eligible
        else ("single", ["auto route kept the bounded single-agent flow"])
    )
