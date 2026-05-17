"""Load and validate the YAML allow-list (ADR-0013).

The policy schema is intentionally small. A v1 rules file lists which
HA services Praetor may invoke, optionally constraining the entity_id
each service may target.

Two YAML shapes are accepted under ``allowed_services``:

1. **Bare string** — fully permissive. ``light.turn_on`` allows any
   parameters. Equivalent to ``{service: light.turn_on}``.
2. **Object** — pins ``target.entity_id`` (SR-005). The call's
   ``target.entity_id`` must be a subset of the rule's list. Calls
   that target other entity IDs are denied. Calls with no
   ``target.entity_id`` against a constrained rule do not match.

Multiple entries for the same service are unioned (OR): if any
matches the call, the call is allowed.

Conditions, ``require_confirm`` verdicts, area/device/label
constraints, and per-key ``data`` whitelists are deferred. Operators
that need them today either fall back to bare-string entries (which
remain permissive) or wait for the follow-up SR-NNN row.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SERVICE_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
TOOL_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")


class PolicyRulesError(RuntimeError):
    """Raised when the rules file is missing or invalid."""


class TargetConstraint(BaseModel):
    """Optional constraints on a service call's ``target`` block.

    ``entity_id=None`` means "no constraint on entity_id". A non-None
    value pins the call's ``target.entity_id`` to a subset of the list.
    """

    entity_id: list[str] | None = None

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        bad = [e for e in value if not ENTITY_ID_PATTERN.fullmatch(e)]
        if bad:
            raise ValueError(
                f"invalid entity_id values (expected 'domain.entity'): {bad!r}",
            )
        return value


class AllowedServiceRule(BaseModel):
    """One row of ``allowed_services`` after normalisation."""

    service: str
    target: TargetConstraint | None = None

    @field_validator("service")
    @classmethod
    def _validate_service(cls, value: str) -> str:
        if not SERVICE_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid service identifier (expected 'domain.service'): {value!r}",
            )
        return value

    @property
    def is_permissive(self) -> bool:
        """True if the rule places no parameter constraints on the call."""

        return self.target is None or self.target.entity_id is None


class AllowedToolRule(BaseModel):
    """One row of ``allowed_tools`` after normalisation (ADR-0028).

    Bare entries (``- tool: calculator``) allow any input. The
    optional ``input`` block carries tool-specific constraints; the
    YAML loader passes it through as a free-form dict and the
    matcher for that tool decides what each key means.
    """

    tool: str
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def _validate_tool(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid tool name (expected snake_case identifier): {value!r}",
            )
        return value


class RulesConfig(BaseModel):
    """Parsed shape of the policy YAML."""

    version: Annotated[int, Field(ge=1, le=1)]
    allowed_services: list[AllowedServiceRule] = Field(default_factory=list)
    allowed_tools: list[AllowedToolRule] = Field(default_factory=list)

    @field_validator("allowed_services", mode="before")
    @classmethod
    def _normalise_entries(cls, value: Any) -> Any:
        """Accept bare strings or mappings; emit a uniform list of dicts."""

        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("allowed_services must be a list")
        normalised: list[Any] = []
        for entry in value:
            if isinstance(entry, str):
                normalised.append({"service": entry})
            elif isinstance(entry, dict | AllowedServiceRule):
                normalised.append(entry)
            else:
                raise ValueError(
                    f"unsupported allow-list entry (expected str or mapping): {entry!r}",
                )
        return normalised

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _normalise_tool_entries(cls, value: Any) -> Any:
        """Accept ``- tool: name`` mappings or bare strings.

        Bare-string entries (``- calculator``) shorthand for
        ``{tool: calculator}`` with no input constraints.
        """

        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("allowed_tools must be a list")
        normalised: list[Any] = []
        for entry in value:
            if isinstance(entry, str):
                normalised.append({"tool": entry})
            elif isinstance(entry, dict | AllowedToolRule):
                normalised.append(entry)
            else:
                raise ValueError(
                    f"unsupported allowed_tools entry (expected str or mapping): {entry!r}",
                )
        return normalised


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
