"""A minimal protobuf reader: only what the Anki package formats need (varints, length-delimited fields).

Anki's newer collections keep note-type settings, template text and the media manifest as protobuf blobs. Reading
them needs no schema compiler: a message is a sequence of (field number, wire type, value) triples. Unknown fields
are skipped, truncated input raises `ProtoError`, and nothing is allocated beyond the input's own size.
"""

from __future__ import annotations

from collections.abc import Iterator


class ProtoError(ValueError):
    """The bytes are not a well-formed protobuf message."""


MAX_FIELDS = 5_000_000  # a media manifest of a very large deck; also bounds a hostile blob


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    out = 0
    while True:
        if pos >= len(buf):
            raise ProtoError("truncated varint")
        b = buf[pos]
        pos += 1
        out |= (b & 0x7F) << shift
        if not b & 0x80:
            return out, pos
        shift += 7
        if shift > 63:
            raise ProtoError("varint too long")


def fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield `(field_number, wire_type, value)`: an int for varint/fixed, bytes for length-delimited."""
    pos = 0
    n = 0
    while pos < len(buf):
        n += 1
        if n > MAX_FIELDS:
            raise ProtoError("too many fields")
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ProtoError("field number 0")
        if wire == 0:
            value, pos = _varint(buf, pos)
            yield number, wire, value
        elif wire == 2:
            size, pos = _varint(buf, pos)
            if pos + size > len(buf):
                raise ProtoError("truncated field")
            yield number, wire, buf[pos : pos + size]
            pos += size
        elif wire == 1:
            if pos + 8 > len(buf):
                raise ProtoError("truncated fixed64")
            yield number, wire, int.from_bytes(buf[pos : pos + 8], "little")
            pos += 8
        elif wire == 5:
            if pos + 4 > len(buf):
                raise ProtoError("truncated fixed32")
            yield number, wire, int.from_bytes(buf[pos : pos + 4], "little")
            pos += 4
        else:
            raise ProtoError(f"unsupported wire type {wire}")


def first_varint(buf: bytes, number: int, default: int = 0) -> int:
    for n, w, v in fields(buf):
        if n == number and w == 0 and isinstance(v, int):
            return v
    return default


def first_string(buf: bytes, number: int, default: str = "") -> str:
    for n, w, v in fields(buf):
        if n == number and w == 2 and isinstance(v, bytes):
            return v.decode("utf-8", "replace")
    return default


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_field(number: int, value: int | bytes | str) -> bytes:
    """Encode one field (used by tests to build fixtures; the importer only reads)."""
    if isinstance(value, int):
        return encode_varint(number << 3) + encode_varint(value)
    data = value.encode() if isinstance(value, str) else value
    return encode_varint((number << 3) | 2) + encode_varint(len(data)) + data
