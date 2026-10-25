"""Plain data types shared across the package. No logic in here."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProcessingMode (str , Enum):
    BLACK_SCREEN = "black_screen"
    ORIGINAL = "original"

@dataclass
class Link:
    platform: str
    url: str


@dataclass
class MediaInfo:
    width: int | None
    height: int | None
    duration: float | None
    has_audio: bool
