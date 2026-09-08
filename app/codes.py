"""Deterministic short-code generation."""

from __future__ import annotations

import hashlib
from typing import Protocol


class CodeGenerator(Protocol):
    def candidate(self, destination: str, collision_index: int) -> str: ...


class Sha256CodeGenerator:
    """Generate stable candidates while giving collisions a bounded next step."""

    def __init__(self, length: int = 10) -> None:
        if length < 6:
            raise ValueError("short-code length must be at least 6")
        self._length = length

    def candidate(self, destination: str, collision_index: int) -> str:
        if collision_index < 0:
            raise ValueError("collision index cannot be negative")
        material = f"{destination}\x00{collision_index}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()[: self._length]
