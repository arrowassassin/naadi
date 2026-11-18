"""Stable reason codes. Every failure the student can see is one of these (the web formats `ingest.error.<code>`)."""

from __future__ import annotations

from typing import Final

# Receive & secure
FILE_TOO_LARGE: Final = "file_too_large"
EMPTY_FILE: Final = "empty_file"
UNSUPPORTED_TYPE: Final = "unsupported_type"
EXECUTABLE: Final = "executable_rejected"
MACRO_OFFICE: Final = "macro_enabled_office"
LEGACY_OFFICE: Final = "legacy_office_unsupported"
ENCRYPTED_PDF: Final = "encrypted_pdf"
ENCRYPTED_ARCHIVE: Final = "encrypted_archive"
ZIP_BOMB: Final = "zip_bomb"
ZIP_TOO_MANY: Final = "zip_too_many_entries"
ZIP_TOO_LARGE: Final = "zip_too_large"
ZIP_TOO_DEEP: Final = "zip_too_deep"
ZIP_UNSAFE_PATH: Final = "zip_unsafe_path"
IMAGE_TOO_LARGE: Final = "image_too_large"
UNREADABLE: Final = "unreadable_file"
SVG_UNSUPPORTED: Final = "svg_unsupported"
STORAGE_FULL: Final = "storage_full"
DAILY_LIMIT: Final = "daily_limit"
UPLOAD_MISSING: Final = "upload_missing"
# Extraction
NO_TEXT: Final = "no_text_found"
TOO_MANY_PAGES: Final = "too_many_pages"
AUDIO_TOO_LONG: Final = "audio_too_long"
EXTRACT_TIMEOUT: Final = "extract_timeout"
EXTRACT_FAILED: Final = "extract_failed"
URL_UNSUPPORTED: Final = "url_unsupported"
URL_FETCH_FAILED: Final = "url_fetch_failed"
WEB_BUDGET: Final = "web_budget_exhausted"
ANKI_UNSUPPORTED: Final = "anki_format_unsupported"
# Vault
VAULT_TOO_LARGE: Final = "vault_too_large"
VAULT_TYPE: Final = "vault_type_unsupported"
DISCARDED: Final = "discarded"
# Placement
NO_PLACEMENT: Final = "no_placement"
PLACEMENT_UNAVAILABLE: Final = "placement_unavailable"
PLACEMENT_FAILED: Final = "placement_failed"
INTERNAL: Final = "internal_error"


class IngestRejected(Exception):
    """A file, link or paste that is refused (or failed) for a reason the student can see. Never carries content."""

    def __init__(self, code: str, **detail: int | str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
