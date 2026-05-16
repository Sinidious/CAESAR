"""Load and validate the YAML allow-list (ADR-0013).

The first cut of the policy engine is a flat list of ``domain.service``
identifiers under ``allowed_services``. We validate the file at startup
and fail fast on missing/invalid input so the operator notices before
the brain accepts requests.

The schema is intentionally small. Conditions, ``require_confirm``
verdicts, and per-entity globs are deferred to a follow-up.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SERVICE_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


class PolicyRulesError(RuntimeError):
    """Raised when the rules file is missing or invalid."""


class RulesConfig(BaseModel):
    """Parsed shape of the policy YAML."""

    version: Annotated[int, Field(ge=1, le=1)]
    allowed_services: list[str] = Field(default_factory=list)

    @field_validator("allowed_services")
    @classmethod
    def _services_are_well_formed(cls, value: list[str]) -> list[str]:
        bad = [s for s in value if not SERVICE_PATTERN.fullmatch(s)]
        if bad:
            raise ValueError(f"invalid service identifiers (expected 'domain.service'): {bad!r}")
        return value


def load_rules(path: Path) -> RulesConfig:
    """Read and validate a rules YAML file.

    Raises :class:`PolicyRulesError` if the file is missing, isn't a
    mapping at the top level, or fails schema validation.
    """

    if not path.is_file():
        raise PolicyRulesError(f"policy rules file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyRulesError(f"policy rules YAML parse error in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyRulesError(
            f"policy rules root must be a mapping; got {type(raw).__name__} in {path}"
        )
    try:
        return RulesConfig.model_validate(raw)
    except ValidationError as exc:
        raise PolicyRulesError(f"policy rules schema error in {path}: {exc}") from exc
