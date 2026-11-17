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
# YouTube video thumbnails (the IFrame player's poster and the media mapper's previews, segment 20).
YOUTUBE_IMG_HOSTS = ("https://i.ytimg.com",)

# Violation reports (CSP and the Trusted Types report-only policy) go to this same-origin endpoint
# (naadi_api/csp_report.py): rate-limited per client, bodies never logged.
CSP_REPORT_PATH = "/api/csp-report"
CSP_REPORT_GROUP = "csp"


def content_security_policy(settings: Settings) -> str:
    supa = settings.supabase_origin
    connect = ["'self'"]
    img = ["'self'", "data:", "blob:", *SEARCH_MEDIA_HOSTS, *YOUTUBE_IMG_HOSTS]
    media = ["'self'", "blob:"]
    if supa:
        connect += [supa, supa.replace("https://", "wss://", 1)]
        img.append(supa)  # Supabase Storage serves from the project origin (/storage/v1/...)
        media.append(supa)  # generated clips (segment 15b) are signed Storage URLs
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        f"img-src {' '.join(img)}",
        f"connect-src {' '.join(connect)}",
        f"media-src {' '.join(media)}",
        # 'self' only: the sandboxed diagram renderer and the sandboxed YouTube player page (SANDBOX_DOCUMENTS), both
        # with an opaque origin. The official YouTube player is a frame of that player page, under ITS policy.
        "frame-src 'self'",
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
    directives += _reporting()
    return "; ".join(directives)


def _reporting() -> list[str]:
    # report-to (Reporting API, Chromium) plus the older report-uri, which WebKit and Firefox still use.
    return [f"report-uri {CSP_REPORT_PATH}", f"report-to {CSP_REPORT_GROUP}"]


def trusted_types_report_only_policy() -> str:
    """Trusted Types in report-only (segment 22 rollout step 1): any string reaching an HTML/script sink is
    reported, nothing is blocked. Enforcement moves into the main policy once the reports stay empty."""
    return "; ".join(["require-trusted-types-for 'script'", *_reporting()])


YOUTUBE_SANDBOX_DOCUMENT = "sandbox/youtube.html"

# Documents only ever loaded in `<iframe sandbox="allow-scripts">` (opaque origin: no cookies, storage or
# access to the app). Mermaid lays out SVG text with inline styles, so they get their own policy; the app's own
# CSP above stays strict.
SANDBOX_DOCUMENTS = frozenset({"sandbox/mermaid.html", YOUTUBE_SANDBOX_DOCUMENT})


def sandbox_content_security_policy() -> str:
    return "; ".join(
        [
            "default-src 'none'",
            # The opaque-origin document fetches its hashed modules from this server with CORS.
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src data:",
            "font-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
            "sandbox allow-scripts",
        ]
    )


def youtube_sandbox_content_security_policy() -> str:
    """The YouTube player page (segment 20): the ONLY document that may load the official IFrame Player API script
    from www.youtube.com, and the only one that may frame the privacy-enhanced player. It has an opaque origin
    (iframe sandbox without allow-same-origin, repeated here as a CSP sandbox), so that script can never read the
    app's storage or session. It may style itself inline (the API sets styles on its own frame) and nothing else."""
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self' https://www.youtube.com",
            "style-src 'self' 'unsafe-inline'",
            "frame-src https://www.youtube-nocookie.com",
            "img-src data:",
            "font-src 'none'",
            "connect-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
            "sandbox allow-scripts",
        ]
    )


PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(self)"


def security_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
    headers = {
        "content-security-policy": content_security_policy(settings),
        "strict-transport-security": "max-age=63072000; includeSubDomains",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": PERMISSIONS_POLICY,
        "cross-origin-opener-policy": "same-origin",
        "content-security-policy-report-only": trusted_types_report_only_policy(),
        "reporting-endpoints": f'{CSP_REPORT_GROUP}="{CSP_REPORT_PATH}"',
    }
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
