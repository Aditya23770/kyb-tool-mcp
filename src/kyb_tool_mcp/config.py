"""Settings (environment) plus the providers and policy YAML files."""

from __future__ import annotations

from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from kyb_tool_mcp import __version__
from kyb_tool_mcp.schemas.decision import Severity

DATA_PACKAGE = "kyb_tool_mcp.data"


def read_package_text(name: str) -> str:
    return resources.files(DATA_PACKAGE).joinpath(name).read_text(encoding="utf-8")


class Settings(BaseSettings):
    """Runtime settings. Reads env vars and a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    companies_house_api_key: str | None = None

    kyb_providers_file: Path | None = None
    kyb_policy_file: Path | None = None
    kyb_data_dir: Path = Field(default_factory=lambda: Path.home() / ".kyb-tool-mcp")
    kyb_audit: bool = True
    kyb_http_cache: bool = True
    kyb_http_cache_ttl: int = 24 * 3600
    kyb_http_timeout: float = 20.0

    @property
    def user_agent(self) -> str:
        return f"kyb-tool-mcp/{__version__} (+https://github.com/Aditya23770/kyb-tool-mcp)"

    @property
    def index_path(self) -> Path:
        return self.kyb_data_dir / "sanctions.sqlite"

    @property
    def audit_path(self) -> Path:
        return self.kyb_data_dir / "audit.sqlite"

    @property
    def cache_path(self) -> Path:
        return self.kyb_data_dir / "http_cache.sqlite"


class ProvidersConfig(BaseModel):
    registry: list[str] = Field(default_factory=lambda: ["companies_house", "gleif"])
    screening: list[str] = Field(default_factory=lambda: ["ofac_sdn", "un_consolidated"])
    pep: list[str] = Field(default_factory=list)
    website: list[str] = Field(default_factory=lambda: ["website_http", "rdap"])


# ---- policy -----------------------------------------------------------------


class DecisionRules(BaseModel):
    reject_if_any: list[str] = ["critical"]
    escalate_if_any: list[str] = ["high"]
    escalate_if_score_gte: int = 30


class RiskLevels(BaseModel):
    low_max: int = 14
    medium_max: int = 39


class RegistryPolicy(BaseModel):
    name_ok: float = 96
    name_medium: float = 75
    address_min: float = 80
    new_entity_months: int = 6
    person_name_match: float = 88
    sic_match_min: float = 55
    inactive_statuses: list[str] = Field(default_factory=list)


class TrueMatchPolicy(BaseModel):
    name_min: float = 92
    require_dob_match: bool = True
    company_name_min: float = 95
    company_coverage_min: float = 0.97  # every distinctive word on BOTH sides must match to confirm
    company_full_name_min: float = 90  # ...and the WHOLE name (generic words included) must be close
    company_full_coverage_min: float = 0.9  # ...with every word of the whole name matched on both sides
    person_coverage_min: float = 0.97  # every name part on both sides must match to confirm a person


class CompanyReviewPolicy(BaseModel):
    name_min: float = 88  # companies have no DOB to clear them; below this similarity they are cleared
    subject_coverage_gt: float = 0.5  # most of the applicant's distinctive words must be in the listed name
    entity_coverage_min: float = 0.5  # at least half of the listed name must be present
    short_match_full_name_min: float = 90  # acronym-only overlaps (ADF, A7) need a close full-name match


class FalsePositivePolicy(BaseModel):
    dob_year_diff_gt: int = 1
    dob_conflict_keep_if_name_gte: float = 100  # exact names are never cleared on DOB alone
    nationality_conflict_name_below: float = 95
    schema_mismatch: bool = True


class ScreeningPolicy(BaseModel):
    candidate_min: float = 80
    max_candidates: int = 200
    true_match: TrueMatchPolicy = Field(default_factory=TrueMatchPolicy)
    false_positive: FalsePositivePolicy = Field(default_factory=FalsePositivePolicy)
    company_review: CompanyReviewPolicy = Field(default_factory=CompanyReviewPolicy)
    weak_alias_name_min: float = 97
    needs_review_escalates: bool = True
    llm_explain_needs_review: bool = True


class WebsitePolicy(BaseModel):
    parked_min_words: int = 200
    thin_content_words: int = 40
    new_domain_days: int = 90
    industry_mismatch_confidence: float = 0.7
    domain_name_min: float = 50
    max_bytes: int = 2_000_000
    timeout_seconds: float = 15


class Policy(BaseModel):
    version: str
    severity_points: dict[str, int] = Field(
        default_factory=lambda: {"critical": 100, "high": 40, "medium": 15, "low": 5}
    )
    decision: DecisionRules = Field(default_factory=DecisionRules)
    risk_levels: RiskLevels = Field(default_factory=RiskLevels)
    registry: RegistryPolicy = Field(default_factory=RegistryPolicy)
    screening: ScreeningPolicy = Field(default_factory=ScreeningPolicy)
    website: WebsitePolicy = Field(default_factory=WebsitePolicy)
    # Per-flag severity overrides, applied by the policy before deciding (e.g. WEBSITE_UNREACHABLE: high).
    flag_severity: dict[str, Severity] = Field(default_factory=dict)


def _load_yaml(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    return data


def default_policy_text() -> str:
    return read_package_text("policy.default.yaml")


def default_providers_text() -> str:
    return read_package_text("providers.default.yaml")


def load_policy(path: Path | None = None) -> Policy:
    text = path.read_text(encoding="utf-8") if path else default_policy_text()
    return Policy.model_validate(_load_yaml(text))


def load_providers(path: Path | None = None) -> ProvidersConfig:
    text = path.read_text(encoding="utf-8") if path else default_providers_text()
    return ProvidersConfig.model_validate(_load_yaml(text))


class Config(BaseModel):
    """Everything a pipeline needs: settings, providers, policy."""

    model_config = {"arbitrary_types_allowed": True}

    settings: Settings
    providers: ProvidersConfig
    policy: Policy


def load_config(
    settings: Settings | None = None,
    providers: ProvidersConfig | None = None,
    policy: Policy | None = None,
) -> Config:
    s = settings or Settings()
    return Config(
        settings=s,
        providers=providers or load_providers(s.kyb_providers_file),
        policy=policy or load_policy(s.kyb_policy_file),
    )


@cache
def default_config() -> Config:
    return load_config()
