"""Request / response models of the Anki API (OpenAPI -> zod in @naadi/shared). Codes, never English sentences."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MediaManifest = Literal["ok", "unreadable", "none"]


class DeckCount(BaseModel):
    deck: str
    cards: int = Field(description="Cards of yours in this deck")
    imported: int = Field(description="Of those, imported from an Anki package")


class DecksOut(BaseModel):
    decks: list[DeckCount]
    total_cards: int


class ImportedDeck(BaseModel):
    deck: str
    added: int
    existing: int


class ImportReport(BaseModel):
    """What one `.apkg` import did. Every number is a count; the web words it from `ui_copy` `anki.*`."""

    notes: int = Field(description="Notes in the package")
    cards_added: int
    cards_existing: int = Field(description="Cards already in your library (same Anki id): left untouched")
    cards_skipped: int = Field(description="Empty, too long, or of an unreadable note type")
    decks: list[ImportedDeck]
    images_added: int
    images_skipped: int = Field(description="Pictures that could not be imported (missing, too large, unsupported)")
    audio_skipped: int = Field(description="Sounds and videos are not imported")
    media_manifest: MediaManifest = Field(
        description="ok; unreadable: pictures could not be matched, the cards came in without them; none: no media"
    )


class DeletedOut(BaseModel):
    deleted: int
