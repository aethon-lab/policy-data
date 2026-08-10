"""Stable, entity-specific identifiers for canonical data.

The recipes in this module are persistence contracts. Component order and the
root namespace must not change after a release without a schema migration.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID, uuid5

ID_RECIPE_VERSION = 1
ID_ROOT_NAMESPACE = UUID("1ee8dbaa-5110-4a43-9876-4e55e83400aa")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _legislature_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("legislature must be a positive integer")
    return value


def _chamber_code(value: object) -> str:
    chamber = _required_text(value, "chamber").lower()
    if chamber not in {"camera", "senato"}:
        raise ValueError("chamber must be 'camera' or 'senato'")
    return chamber


def _stable_id(entity_type: str, components: Sequence[object]) -> str:
    namespace = uuid5(ID_ROOT_NAMESPACE, entity_type)
    serialized = json.dumps(list(components), ensure_ascii=False, separators=(",", ":"))
    return f"{entity_type}:{uuid5(namespace, serialized)}"


def canonical_person_id(authority: str, source_person_id: str) -> str:
    """Mint a canonical person ID from the first authoritative identity.

    Later reviewed matches become aliases; they never replace this ID. This
    keeps a person independent of mandate, legislature, and chamber.
    """

    return _stable_id(
        "person",
        (
            _required_text(authority, "authority").lower(),
            _required_text(source_person_id, "source_person_id"),
        ),
    )


def source_identity_id(authority: str, source_person_id: str) -> str:
    return _stable_id(
        "source_identity",
        (
            _required_text(authority, "authority").lower(),
            _required_text(source_person_id, "source_person_id"),
        ),
    )


def mandate_id(person_id: str, legislature: int, chamber: str) -> str:
    return _stable_id(
        "mandate",
        (
            _required_text(person_id, "person_id"),
            _legislature_number(legislature),
            _chamber_code(chamber),
        ),
    )


def roll_call_id(legislature: int, chamber: str, source_roll_call_id: str) -> str:
    return _stable_id(
        "roll_call",
        (
            _legislature_number(legislature),
            _chamber_code(chamber),
            _required_text(source_roll_call_id, "source_roll_call_id"),
        ),
    )
