"""Security headers applied to every response (API, static files, errors)."""

from __future__ import annotations

from .settings import Settings

# Images that layered search (naadi_api/search.py) returns, rendered directly with attribution: Wikimedia
# Commons files and thumbnails, and PMC open-access figures from NCBI's PMC Article Datasets bucket. (YouTube is
# an embedded player, governed by frame-src.)
SEARCH_MEDIA_HOSTS = (
    "https://upload.wikimedia.org",
    "https://thumb.wikimedia.org",
    "https://pmc-oa-opendata.s3.amazonaws.com",
)


def content_security_policy(settings: Settings) -> str:
    supa = settings.supabase_origin
    connect = ["'self'"]
    img = ["'self'", "data:", "blob:", *SEARCH_MEDIA_HOSTS]
    if supa:
        connect += [supa, supa.replace("https://", "wss://", 1)]
        img.append(supa)  # Supabase Storage serves from the project origin (/storage/v1/...)
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        f"img-src {' '.join(img)}",
        f"connect-src {' '.join(connect)}",
        "media-src 'self' blob:",
        # YouTube only through the official IFrame player (privacy-enhanced domain first).
        "frame-src https://www.youtube-nocookie.com https://www.youtube.com",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        # HF renders Spaces inside an iframe on huggingface.co.
        "frame-ancestors 'self' https://huggingface.co",
    ]
    if not settings.is_local:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)


PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(self)"


def security_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
    headers = {
        "content-security-policy": content_security_policy(settings),
        "strict-transport-security": "max-age=63072000; includeSubDomains",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": PERMISSIONS_POLICY,
        "cross-origin-opener-policy": "same-origin",
    }
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
