"""Input kinds (`uploads.kind` / `ingest_items.input_kind`) and the MIME types the Storage bucket accepts for them."""

from __future__ import annotations

from typing import Final, Literal

InputKind = Literal[
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "csv",
    "txt",
    "md",
    "html",
    "epub",
    "rtf",
    "image",
    "audio",
    "video",
    "link",
    "anki",
    "zip",
    "paste",
]
FILE_KINDS: Final[frozenset[str]] = frozenset(
    {"pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "html", "epub", "rtf", "image", "audio", "video", "anki", "zip"}
)
# Office / e-book formats read with markitdown.
MARKITDOWN_KINDS: Final[frozenset[str]] = frozenset({"docx", "pptx", "xlsx", "epub", "html"})
TEXT_KINDS: Final[frozenset[str]] = frozenset({"csv", "txt", "md", "rtf", "paste"})

# Content type stored in Storage for each sniffed kind (the bucket's allow-list, migration 0002).
KIND_MIME: Final[dict[str, str]] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "epub": "application/epub+zip",
    "rtf": "application/rtf",
    "anki": "application/vnd.anki",
    "zip": "application/zip",
}
IMAGE_MIMES: Final[frozenset[str]] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif", "image/heic", "image/heif"}
)
AUDIO_MIMES: Final[frozenset[str]] = frozenset(
    {"audio/mpeg", "audio/mp4", "audio/aac", "audio/wav", "audio/x-wav", "audio/webm", "audio/ogg"}
)
VIDEO_MIMES: Final[frozenset[str]] = frozenset({"video/mp4", "video/webm", "video/quicktime"})
# What the client may declare when asking for an upload URL (Storage enforces the same list on the object).
UPLOAD_MIMES: Final[frozenset[str]] = frozenset(
    {*KIND_MIME.values(), "text/rtf", "application/x-zip-compressed", *IMAGE_MIMES, *AUDIO_MIMES, *VIDEO_MIMES}
)
# Vault bucket allow-list (migration 0002): personal documents of other types cannot be moved there.
VAULT_MIMES: Final[frozenset[str]] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
)
VAULT_MAX_BYTES: Final = 20 * 1024 * 1024
