"""Phantom definition: geometry of every test object in the canonical phantom frame.

Canonical frame: origin at phantom-face center, +x right / +y up as in the guide's
reference figure, units mm. Definitions are versioned JSON files; positions were
calibrated from reference scans (see ``provenance``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class PhantomDef:
    name: str
    version: str
    side_mm: float                # calibrated outer square side
    nominal_side_mm: float        # design-intent side (assumed)
    rulers: dict                  # side -> ruler geometry
    corner_marks: dict
    uniformity: dict
    linepairs: dict
    lowcontrast: dict
    wedge: dict
    tolerances: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version,
            "side_mm": self.side_mm, "nominal_side_mm": self.nominal_side_mm,
            "rulers": self.rulers, "corner_marks": self.corner_marks,
            "uniformity": self.uniformity, "linepairs": self.linepairs,
            "lowcontrast": self.lowcontrast, "wedge": self.wedge,
            "tolerances": self.tolerances, "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PhantomDef":
        return cls(**{k: d[k] for k in (
            "name", "version", "side_mm", "nominal_side_mm", "rulers",
            "corner_marks", "uniformity", "linepairs", "lowcontrast", "wedge",
            "tolerances", "provenance")})

    @classmethod
    def load(cls, path: str) -> "PhantomDef":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


def default_definition_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "phantom_definitions", "msf_v1.json")


def load_default() -> PhantomDef:
    return PhantomDef.load(default_definition_path())
