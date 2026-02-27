"""Agent OS Governance Bot — GitHub App webhook handler."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ScanPattern:
    """A single pattern to scan for in file content."""

    pattern: str
    severity: str  # "error", "warning", "info"
    message: str
    file_glob: Optional[str] = None  # restrict to matching filenames

    def __post_init__(self) -> None:
        self._compiled: re.Pattern[str] = re.compile(self.pattern, re.IGNORECASE)

    @property
    def regex(self) -> re.Pattern[str]:
        return self._compiled


@dataclass
class Finding:
    """A single governance finding in a file."""

    file: str
    line: int
    severity: str  # "error", "warning", "info"
    message: str
    suggestion: Optional[str] = None
    rule: str = ""


@dataclass
class Review:
    """A complete review to post on a pull request."""

    conclusion: str  # "approve", "request_changes"
    body: str
    comments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AppConfig:
    """Parsed .github/agent-governance.yml configuration."""

    profiles: List[str] = field(default_factory=lambda: ["security"])
    block_on: str = "error"
    include: List[str] = field(
        default_factory=lambda: ["**/*.py", "**/*.yaml", "**/*.yml", "**/*.json", "**/*.md"]
    )
    exclude: List[str] = field(
        default_factory=lambda: ["node_modules/**", "*.lock", "dist/**"]
    )
    custom_patterns: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Governance profiles
# ---------------------------------------------------------------------------

class GovernanceProfile:
    """A named set of scan patterns for a governance domain."""

    def __init__(
        self,
        name: str,
        patterns: List[ScanPattern],
        severity_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.name = name
        self.patterns = patterns
        self.severity_map = severity_map or {}

    # -- factory class methods -----------------------------------------------

    @classmethod
    def security(cls) -> GovernanceProfile:
        """Built-in security profile."""
        return cls(
            name="security",
            patterns=[
                # Secrets
                ScanPattern(
                    pattern=r"AKIA[0-9A-Z]{16}",
                    severity="error",
                    message="Hardcoded AWS access key detected",
                    file_glob="*",
                ),
                ScanPattern(
                    pattern=r"ghp_[A-Za-z0-9]{36}",
                    severity="error",
                    message="Hardcoded GitHub personal access token detected",
                    file_glob="*",
                ),
                ScanPattern(
                    pattern=r"""(?:api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*['"][A-Za-z0-9]{16,}['"]""",
                    severity="error",
                    message="Possible hardcoded API key or secret",
                    file_glob="*",
                ),
                # Dangerous code
                ScanPattern(
                    pattern=r"\beval\s*\(",
                    severity="error",
                    message="Use of eval() is a security risk",
                    file_glob="*.py",
                ),
                ScanPattern(
                    pattern=r"\bexec\s*\(",
                    severity="error",
                    message="Use of exec() is a security risk",
                    file_glob="*.py",
                ),
                ScanPattern(
                    pattern=r"\bsubprocess\.(?:call|run|Popen)\s*\(",
                    severity="warning",
                    message="subprocess usage — ensure input is sanitized",
                    file_glob="*.py",
                ),
                ScanPattern(
                    pattern=r"\bos\.system\s*\(",
                    severity="error",
                    message="os.system() is unsafe — use subprocess with shell=False",
                    file_glob="*.py",
                ),
                # Injection phrases
                ScanPattern(
                    pattern=r"ignore\s+(all\s+)?previous\s+instructions",
                    severity="error",
                    message="Prompt injection pattern: instruction override attempt",
                    file_glob="*",
                ),
                ScanPattern(
                    pattern=r"system\s+prompt\s+override",
                    severity="error",
                    message="Prompt injection pattern: system prompt override",
                    file_glob="*",
                ),
                # Insecure config
                ScanPattern(
                    pattern=r"""(?:DEBUG|debug)\s*[:=]\s*(?:true|True|1|yes)""",
                    severity="warning",
                    message="Debug mode enabled — disable in production",
                    file_glob="*.yaml",
                ),
                ScanPattern(
                    pattern=r"""(?:verify|VERIFY)\s*[:=]\s*(?:false|False|0|no)""",
                    severity="error",
                    message="TLS/SSL verification disabled",
                    file_glob="*",
                ),
            ],
        )

    @classmethod
    def compliance(cls) -> GovernanceProfile:
        """Built-in compliance profile."""
        return cls(
            name="compliance",
            patterns=[
                # PII patterns
                ScanPattern(
                    pattern=r"\b\d{3}-\d{2}-\d{4}\b",
                    severity="error",
                    message="Possible SSN pattern detected in code",
                    file_glob="*",
                ),
                ScanPattern(
                    pattern=r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b",
                    severity="error",
                    message="Possible credit card number detected in code",
                    file_glob="*",
                ),
                ScanPattern(
                    pattern=r"""['"][a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}['"]""",
                    severity="warning",
                    message="Hardcoded email address — consider using config or environment variable",
                    file_glob="*.py",
                ),
            ],
        )

    @classmethod
    def agent_safety(cls) -> GovernanceProfile:
        """Built-in agent-safety profile."""
        return cls(
            name="agent-safety",
            patterns=[
                # Prompt injection in agent/prompt files
                ScanPattern(
                    pattern=r"ignore\s+(all\s+)?previous\s+instructions",
                    severity="error",
                    message="Prompt injection detected in agent prompt file",
                    file_glob="*.prompt.md",
                ),
                ScanPattern(
                    pattern=r"ignore\s+(all\s+)?previous\s+instructions",
                    severity="error",
                    message="Prompt injection detected in agent config file",
                    file_glob="*.agent.md",
                ),
                ScanPattern(
                    pattern=r"you\s+are\s+now\b",
                    severity="error",
                    message="Role override detected in agent prompt file",
                    file_glob="*.prompt.md",
                ),
                ScanPattern(
                    pattern=r"you\s+are\s+now\b",
                    severity="error",
                    message="Role override detected in agent config file",
                    file_glob="*.agent.md",
                ),
                # Unsafe MCP config
                ScanPattern(
                    pattern=r"""allow[_-]?all\s*[:=]\s*(?:true|True|1|yes)""",
                    severity="error",
                    message="Unsafe MCP config: allow_all is enabled — use explicit allowlists",
                    file_glob="*.yaml",
                ),
                ScanPattern(
                    pattern=r"""allow[_-]?all\s*[:=]\s*(?:true|True|1|yes)""",
                    severity="error",
                    message="Unsafe MCP config: allow_all is enabled — use explicit allowlists",
                    file_glob="*.yml",
                ),
                ScanPattern(
                    pattern=r"""allow[_-]?all\s*[:=]\s*(?:true|True|1|yes)""",
                    severity="error",
                    message="Unsafe MCP config: allow_all is enabled — use explicit allowlists",
                    file_glob="*.json",
                ),
                # Missing tool allowlists
                ScanPattern(
                    pattern=r"""tools\s*:\s*\[\s*["']\*["']\s*\]""",
                    severity="warning",
                    message="Wildcard tool allowlist — specify explicit tools",
                    file_glob="*.yaml",
                ),
                ScanPattern(
                    pattern=r"""tools\s*:\s*\[\s*["']\*["']\s*\]""",
                    severity="warning",
                    message="Wildcard tool allowlist — specify explicit tools",
                    file_glob="*.yml",
                ),
            ],
        )

    @classmethod
    def all_profiles(cls) -> GovernanceProfile:
        """Combined profile merging all built-in profiles."""
        security = cls.security()
        compliance = cls.compliance()
        agent_safety = cls.agent_safety()
        combined_patterns = security.patterns + compliance.patterns + agent_safety.patterns
        return cls(name="all", patterns=combined_patterns)

    def merge(self, other: GovernanceProfile) -> GovernanceProfile:
        """Merge another profile's patterns into this one."""
        return GovernanceProfile(
            name=f"{self.name}+{other.name}",
            patterns=self.patterns + other.patterns,
            severity_map={**self.severity_map, **other.severity_map},
        )


# ---------------------------------------------------------------------------
# File analyzer
# ---------------------------------------------------------------------------

def _matches_glob(filename: str, glob_pattern: str) -> bool:
    """Check if a filename matches a glob pattern."""
    return fnmatch.fnmatch(filename, glob_pattern) or fnmatch.fnmatch(
        filename.split("/")[-1], glob_pattern
    )


class FileAnalyzer:
    """Runs governance patterns against file content."""

    def __init__(self, profile: GovernanceProfile) -> None:
        self.profile = profile

    def analyze_file(
        self,
        filename: str,
        content: str,
        patch: Optional[str] = None,
    ) -> List[Finding]:
        """Analyze a file's content against the profile's patterns.

        Returns a list of Finding objects for each matched pattern.
        """
        findings: List[Finding] = []
        lines = content.splitlines()

        for scan_pattern in self.profile.patterns:
            # Check file_glob filter
            if scan_pattern.file_glob and scan_pattern.file_glob != "*":
                if not _matches_glob(filename, scan_pattern.file_glob):
                    continue

            # Scan each line
            for line_num, line_text in enumerate(lines, start=1):
                if scan_pattern.regex.search(line_text):
                    findings.append(
                        Finding(
                            file=filename,
                            line=line_num,
                            severity=scan_pattern.severity,
                            message=scan_pattern.message,
                            suggestion=None,
                            rule=scan_pattern.pattern,
                        )
                    )

        return findings


# ---------------------------------------------------------------------------
# Review builder
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "info": "🔵"}


class ReviewBuilder:
    """Builds a GitHub PR review from governance findings."""

    def build_review(
        self,
        findings: List[Finding],
        files: Optional[Dict[str, List[Finding]]] = None,
    ) -> Review:
        """Build a Review from a flat list of findings.

        Args:
            findings: All findings across all files.
            files: Optional pre-grouped dict of filename → findings.
                   If not provided, grouped from *findings*.
        """
        if files is None:
            files = self._group_by_file(findings)

        error_count = sum(1 for f in findings if f.severity == "error")
        warning_count = sum(1 for f in findings if f.severity == "warning")
        info_count = sum(1 for f in findings if f.severity == "info")

        conclusion = "request_changes" if error_count > 0 else "approve"

        body = self._build_body(
            error_count, warning_count, info_count, files, conclusion
        )
        comments = self._build_comments(findings)

        return Review(conclusion=conclusion, body=body, comments=comments)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _group_by_file(findings: List[Finding]) -> Dict[str, List[Finding]]:
        grouped: Dict[str, List[Finding]] = {}
        for f in findings:
            grouped.setdefault(f.file, []).append(f)
        return grouped

    @staticmethod
    def _build_body(
        errors: int,
        warnings: int,
        infos: int,
        files: Dict[str, List[Finding]],
        conclusion: str,
    ) -> str:
        lines = ["## 🛡️ Agent OS Governance Review\n"]

        if conclusion == "approve" and errors == 0 and warnings == 0 and infos == 0:
            lines.append("✅ **No governance findings.** All checks passed.\n")
            return "\n".join(lines)

        # Summary table
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        if errors:
            lines.append(f"| 🔴 Error | {errors} |")
        if warnings:
            lines.append(f"| 🟡 Warning | {warnings} |")
        if infos:
            lines.append(f"| 🔵 Info | {infos} |")
        lines.append("")

        if conclusion == "request_changes":
            lines.append(
                "❌ **Blocking merge** — critical findings must be resolved.\n"
            )
        else:
            lines.append(
                "⚠️ **Advisory** — warnings found but not blocking merge.\n"
            )

        # Per-file breakdown
        if files:
            lines.append("### Files\n")
            for fname, ffindings in files.items():
                lines.append(f"- `{fname}` — {len(ffindings)} finding(s)")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_comments(findings: List[Finding]) -> List[Dict[str, Any]]:
        comments: List[Dict[str, Any]] = []
        for f in findings:
            emoji = _SEVERITY_EMOJI.get(f.severity, "ℹ️")
            body_parts = [f"{emoji} **{f.severity.upper()}**: {f.message}"]
            if f.suggestion:
                body_parts.append(f"\n💡 **Suggestion**: {f.suggestion}")
            comments.append(
                {"path": f.file, "line": f.line, "body": "\n".join(body_parts)}
            )
        return comments


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

_PROFILE_FACTORIES = {
    "security": GovernanceProfile.security,
    "compliance": GovernanceProfile.compliance,
    "agent-safety": GovernanceProfile.agent_safety,
    "all": GovernanceProfile.all_profiles,
}


def parse_config(yaml_content: Optional[str] = None) -> AppConfig:
    """Parse .github/agent-governance.yml content into AppConfig.

    Returns defaults when *yaml_content* is ``None`` or invalid YAML.
    """
    if yaml_content is None:
        return AppConfig()

    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        return AppConfig()

    if not isinstance(data, dict):
        return AppConfig()

    profiles_raw = data.get("profile", "security")
    if isinstance(profiles_raw, str):
        profiles = [profiles_raw]
    elif isinstance(profiles_raw, list):
        profiles = profiles_raw
    else:
        profiles = ["security"]

    return AppConfig(
        profiles=profiles,
        block_on=data.get("block_on", "error"),
        include=data.get("include", AppConfig().include),
        exclude=data.get("exclude", AppConfig().exclude),
        custom_patterns=data.get("custom_patterns", []),
    )


def _build_profile(config: AppConfig) -> GovernanceProfile:
    """Build a merged GovernanceProfile from config profile names."""
    profiles: List[GovernanceProfile] = []
    for name in config.profiles:
        factory = _PROFILE_FACTORIES.get(name)
        if factory:
            profiles.append(factory())

    if not profiles:
        profiles.append(GovernanceProfile.security())

    # Add custom patterns
    custom_scan_patterns: List[ScanPattern] = []
    for cp in config.custom_patterns:
        custom_scan_patterns.append(
            ScanPattern(
                pattern=cp["pattern"],
                severity=cp.get("severity", "warning"),
                message=cp.get("message", "Custom pattern match"),
                file_glob=cp.get("file_glob"),
            )
        )

    result = profiles[0]
    for p in profiles[1:]:
        result = result.merge(p)

    if custom_scan_patterns:
        result.patterns.extend(custom_scan_patterns)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _glob_match(filename: str, pattern: str) -> bool:
    """Match filename against a glob pattern, handling ``**/`` prefix."""
    if fnmatch.fnmatch(filename, pattern):
        return True
    # ``**/X`` should also match ``X`` at the root level
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(filename, pattern[3:])
    return False


def _should_scan(filename: str, config: AppConfig) -> bool:
    """Check if a file should be scanned based on include/exclude globs."""
    included = any(_glob_match(filename, pat) for pat in config.include)
    excluded = any(_glob_match(filename, pat) for pat in config.exclude)
    return included and not excluded


def handle_pull_request(event: dict) -> Review:
    """Main entry point: process a PR webhook event and return a Review.

    Args:
        event: Dict with keys ``config_yaml`` (optional str),
               ``files`` (dict mapping filename → content).

    Returns:
        A Review with conclusion, body, and inline comments.
    """
    config = parse_config(event.get("config_yaml"))
    profile = _build_profile(config)
    analyzer = FileAnalyzer(profile)

    all_findings: List[Finding] = []
    files_dict: Dict[str, List[Finding]] = {}

    for filename, content in event.get("files", {}).items():
        if not _should_scan(filename, config):
            continue
        file_findings = analyzer.analyze_file(filename, content)
        if file_findings:
            all_findings.extend(file_findings)
            files_dict[filename] = file_findings

    builder = ReviewBuilder()
    return builder.build_review(all_findings, files_dict)
