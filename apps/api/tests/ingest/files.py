"""Small generated fixtures for every input type. No real document, no real identifier: IDs are synthetic and made
valid with the same check-digit algorithms the gate uses."""

from __future__ import annotations

import io
import json
import math
import sqlite3
import struct
import tempfile
import wave
import zipfile
from pathlib import Path

from naadi_api.ingest.sensitivity import mrz_check, verhoeff_digit

LOREM = (
    "The nephron is the functional unit of the kidney. Plasma is filtered at the glomerulus, and the proximal "
    "tubule reabsorbs most of the filtered sodium, glucose and bicarbonate. "
)


def pdf(
    pages: int = 3,
    *,
    title: str = "Renal physiology notes",
    text: str = LOREM,
    image: bool = True,
    scanned_pages: int = 0,
    extra: str = "",
) -> bytes:
    import pymupdf
    from PIL import Image

    doc = pymupdf.open()
    doc.set_metadata({"title": title})
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72), f"Chapter {i + 1}: Section heading {i + 1}", fontsize=20)
        y = 110
        if i == 0 and extra:
            for line in extra.splitlines():  # kept line by line (an MRZ must stay one line)
                p.insert_text((40, y), line, fontsize=9, fontname="cour")
                y += 14
        for line in _wrap(text * 6, 90)[:40]:
            p.insert_text((72, y), line, fontsize=10)
            y += 14
        if image and i == 1:
            b = io.BytesIO()
            Image.new("RGB", (400, 300), (200, 30, 30)).save(b, "PNG")
            p.insert_image(pymupdf.Rect(72, 560, 372, 785), stream=b.getvalue())
    for _ in range(scanned_pages):
        doc.new_page()
    return doc.tobytes()


def big_pdf(target_mb: int = 50, preface: str = "") -> bytes:
    """A text PDF padded with incompressible images to just under `target_mb` MiB (the acceptance benchmark)."""
    import os

    import pymupdf
    from PIL import Image

    doc = pymupdf.open()
    size = 0
    i = 0
    # Each page adds ~1.5 MB of image; stop one page short of the cap (text and structure add the rest).
    while size < target_mb * 1024 * 1024 - 2_200_000:
        p = doc.new_page()
        p.insert_text((72, 72), f"Chapter {i // 10 + 1}: Topic {i + 1}", fontsize=18)
        y = 104
        if i == 0 and preface:
            p.insert_text((72, y), preface, fontsize=9)
            y += 16
        for line in _wrap(LOREM * 8, 95)[:44]:
            p.insert_text((72, y), line, fontsize=9)
            y += 13
        noise = Image.frombytes("RGB", (700, 700), os.urandom(700 * 700 * 3))
        b = io.BytesIO()
        noise.save(b, "PNG")
        p.insert_image(pymupdf.Rect(300, 600, 500, 800), stream=b.getvalue())
        size += len(b.getvalue())
        i += 1
    return doc.tobytes(garbage=0, deflate=False)


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for w in text.split():
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def png(w: int = 640, h: int = 480, text: str | None = None) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), "white")
    if text:
        d = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=28)
        for i, line in enumerate(text.splitlines()):
            d.text((20, 20 + i * 40), line, fill="black", font=font)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def jpeg_with_exif() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (320, 240), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SyntheticCam"  # Make
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}  # GPS (dropped by the conversion)
    b = io.BytesIO()
    img.save(b, "JPEG", exif=exif)
    return b.getvalue()


def heic() -> bytes:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    b = io.BytesIO()
    Image.new("RGB", (256, 256), (0, 160, 0)).save(b, "HEIF", quality=50)
    return b.getvalue()


def _zip(files: dict[str, bytes | str], *, stored: bool = False) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return b.getvalue()


def docx(paragraphs: list[str] | None = None, *, macro: bool = False) -> bytes:
    paras = paragraphs or ["Acid-base disorders", LOREM * 3, "Metabolic acidosis", LOREM * 2]
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{p}</w:t></w:r></w:p>'
        if i % 2 == 0
        else f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>"
        for i, p in enumerate(paras)
    )
    ct = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        + ("wordprocessingml.document.macroEnabled.main+xml" if macro else "wordprocessingml.document.main+xml")
        + '"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    files: dict[str, bytes | str] = {
        "[Content_Types].xml": ct,
        "_rels/.rels": rels,
        "word/document.xml": document,
        "word/styles.xml": styles,
        "word/_rels/document.xml.rels": doc_rels,
    }
    if macro:
        files["word/vbaProject.bin"] = b"\x00" * 64
    return _zip(files)


def pptx() -> bytes:
    from pptx import Presentation

    prs = Presentation()
    for i in range(3):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = f"Lecture slide {i + 1}: Renal tubule"
        s.placeholders[1].text = LOREM
    b = io.BytesIO()
    prs.save(b)
    return b.getvalue()


def xlsx_qa() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Front", "Back"])
    for i in range(12):
        ws.append([f"Question {i + 1}: which segment reabsorbs most sodium?", f"Answer {i + 1}: proximal tubule"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def epub() -> bytes:
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        "</container>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Synthetic renal reader</dc:title>'
        '<dc:identifier id="id">synthetic-1</dc:identifier><dc:language>en</dc:language></metadata>'
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    chapter = (
        "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Glomerular filtration</h1>"
        f"<p>{LOREM * 4}</p></body></html>"
    )
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c1.xhtml", chapter)
    return b.getvalue()


def html_doc() -> bytes:
    return (
        f"<!doctype html><html><head><title>Bicarbonate buffer</title></head><body><h1>Bicarbonate</h1>"
        f"<p>{LOREM * 3}</p><h2>Clinical</h2><p>{LOREM}</p></body></html>"
    ).encode()


def rtf() -> bytes:
    return (r"{\rtf1\ansi{\fonttbl\f0 Arial;}\f0\pard Renal tubular acidosis\par " + LOREM + r"\par }").encode()


def csv_qa() -> bytes:
    rows = ["front,back"] + [f"What does segment {i} reabsorb?,Sodium and water ({i})" for i in range(10)]
    return "\n".join(rows).encode()


def mcq_text() -> bytes:
    blocks = []
    for i in range(1, 4):
        blocks.append(
            f"{i}. A synthetic stem number {i} about renal physiology asks which segment reabsorbs most sodium?\n"
            "A. Proximal tubule\nB. Thin descending limb\nC. Collecting duct\nD. Distal tubule\n"
            "Answer: A\nExplanation: About two thirds of filtered sodium is reabsorbed proximally.\n"
        )
    return "\n".join(blocks).encode()


def wav(seconds: float = 1.5) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * t / 16000))) for t in range(int(16000 * seconds))
        )
        w.writeframes(frames)
    return b.getvalue()


def mp4(frames: int = 12) -> bytes:
    import av
    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        path = Path(d, "v.mp4")
        with av.open(str(path), "w") as c:
            s = c.add_stream("mpeg4", rate=12)
            s.width, s.height, s.pix_fmt = 160, 120, "yuv420p"
            for i in range(frames):
                img = np.full((120, 160, 3), (i * 20) % 255, dtype=np.uint8)
                for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                    c.mux(pkt)
            for pkt in s.encode():
                c.mux(pkt)
        return path.read_bytes()


def apkg(n: int = 5) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d, "collection.anki2")
        con = sqlite3.connect(db)
        con.execute("create table notes (id integer primary key, flds text)")
        for i in range(n):
            con.execute("insert into notes (flds) values (?)", (f"Front <b>{i}</b>\x1fBack {i}<br>line",))
        con.commit()
        con.close()
        return _zip({"collection.anki2": db.read_bytes(), "media": json.dumps({})})


def zip_bundle(files: dict[str, bytes]) -> bytes:
    return _zip(dict(files))


def zip_bomb() -> bytes:
    return _zip({"bomb.txt": b"0" * (20 * 1024 * 1024)})


def encrypted_zip_flag() -> bytes:
    data = bytearray(_zip({"a.txt": b"hello"}, stored=True))
    # Set the "encrypted" general-purpose flag in both the local and the central header.
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        i = data.find(sig)
        off = i + (6 if sig == b"PK\x03\x04" else 8)
        data[off] |= 0x01
    return bytes(data)


def encrypted_pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")


# ── synthetic identifiers ──


def aadhaar() -> str:
    body = "23456789012"[:11]
    return body + verhoeff_digit(body)


def mrz_td3() -> tuple[str, str]:
    number, birth, expiry = "Z1234567<", "900101", "300101"
    l1 = ("P<INDSYNTHETIC<<TEST<PERSON" + "<" * 44)[:44]
    l2 = number + mrz_check(number) + "IND" + birth + mrz_check(birth) + "M" + expiry + mrz_check(expiry)
    l2 = (l2 + "<" * 14)[:42] + "0" + "0"
    return l1, l2


def passport_text() -> str:
    l1, l2 = mrz_td3()
    return f"REPUBLIC OF INDIA\nPASSPORT\nSurname: SYNTHETIC\nGiven names: TEST PERSON\n{l1}\n{l2}\n"
