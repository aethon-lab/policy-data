from __future__ import annotations

import pytest

from policy_data.domain.ids import (
    canonical_person_id,
    mandate_id,
    roll_call_id,
    source_identity_id,
)


def test_entity_specific_ids_are_stable_and_scope_sensitive() -> None:
    assert canonical_person_id("camera", "persona-42") == (
        "person:4f5758f2-7d1f-57d9-96e0-67d52630c4c0"
    )
    assert canonical_person_id("camera", "persona-42") == canonical_person_id(
        "camera", "persona-42"
    )

    camera_mandate = mandate_id(
        "person:4f5758f2-7d1f-57d9-96e0-67d52630c4c0", 19, "camera"
    )
    assert camera_mandate != mandate_id(
        "person:4f5758f2-7d1f-57d9-96e0-67d52630c4c0", 18, "camera"
    )
    assert camera_mandate != mandate_id(
        "person:4f5758f2-7d1f-57d9-96e0-67d52630c4c0", 19, "senato"
    )
    assert camera_mandate != roll_call_id(19, "camera", "persona-42")


def test_source_identity_is_authority_scoped_not_name_scoped() -> None:
    camera = source_identity_id("camera", "persona-42")
    senato = source_identity_id("senato", "persona-42")

    assert camera != senato
    assert camera != source_identity_id("camera", "persona-43")


@pytest.mark.parametrize(
    ("factory", "args"),
    [
        (canonical_person_id, ("", "persona-42")),
        (source_identity_id, ("camera", "")),
        (roll_call_id, (0, "camera", "1")),
        (mandate_id, ("person:abc", 19, "")),
    ],
)
def test_id_recipes_reject_missing_or_invalid_identity_components(
    factory: object, args: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        factory(*args)  # type: ignore[operator]
