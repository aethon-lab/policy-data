"""Contracts that preserve the origin and epistemic layer of every fact."""

from __future__ import annotations

from dataclasses import dataclass

from policy_data.domain.enums import FactLayer


@dataclass(frozen=True, slots=True)
class FactValue:
    layer: FactLayer
    value: str | int | float | bool | None
    source_record_id: str | None = None
    method: str | None = None

    def __post_init__(self) -> None:
        if self.layer in {FactLayer.SOURCE, FactLayer.NORMALIZED}:
            if not self.source_record_id:
                raise ValueError(
                    "source and normalized facts require a source_record_id"
                )
        elif not self.method:
            raise ValueError("derived and interpreted facts require a method")
