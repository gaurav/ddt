"""Read CSV bytes into verbatim string rows, recording every decision made on the way.

The importer never "cleans" values. It only does what is needed to get from bytes to a table:
pick a text encoding, split records, and make header names unique. Each of those decisions is
returned in `SourceInfo` and stored in the mapping's `source` block, so a replay does not depend
on detection giving the same answer twice.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
from dataclasses import dataclass, field

# UTF-8 where it is valid, Windows-1252 for the stray bytes that are not. Files that were mostly
# UTF-8 but had a few Latin-1 cells pasted in are common, and decoding the whole file as either
# encoding alone would corrupt the other part.
UTF8_WITH_FALLBACK = "utf-8/cp1252"
_FALLBACK_ERRORS = "ddt_cp1252_fallback"


def _cp1252_fallback(err: UnicodeError) -> tuple[str, int]:
    if not isinstance(err, UnicodeDecodeError):
        raise err
    bad = err.object[err.start : err.end]
    # cp1252 leaves five bytes undefined; latin-1 maps every byte, so decoding cannot fail.
    return bad.decode("cp1252", errors="ignore") or bad.decode("latin-1"), err.end


codecs.register_error(_FALLBACK_ERRORS, _cp1252_fallback)


@dataclass
class SourceInfo:
    sha256: str
    encoding: str
    encoding_detected: bool
    # Number of bytes that were not valid UTF-8 and went through the cp1252 fallback.
    fallback_bytes: int = 0
    # [{"index": 7, "original": "scientificName", "name": "scientificName_1"}, ...]
    header_renames: list[dict] = field(default_factory=list)
    # Records with more or fewer cells than the header: {"row": 12, "cells": 67}
    ragged_rows: list[dict] = field(default_factory=list)


@dataclass
class ImportResult:
    columns: list[str]
    rows: list[list[str]]
    info: SourceInfo


def decode(raw: bytes, encoding: str) -> str:
    if encoding == UTF8_WITH_FALLBACK:
        return raw.decode("utf-8-sig", errors=_FALLBACK_ERRORS)
    if encoding.lower().replace("_", "-") in ("utf-8", "utf8"):
        return raw.decode("utf-8-sig")
    if encoding.lower() in ("cp1252", "windows-1252"):
        # cp1252 leaves five bytes undefined; read those as latin-1 instead of failing.
        return raw.decode("cp1252", errors=_FALLBACK_ERRORS)
    return raw.decode(encoding)


def detect_encoding(raw: bytes) -> tuple[str, int]:
    """Return (encoding, number of bytes that are not valid UTF-8).

    charset-normalizer was tried first and guessed cp1250 and cp775 for the two example datasets
    (really UTF-8 with stray Latin-1 bytes, and Mac Roman), so detection is a small explicit rule
    instead: UTF-8 if valid; UTF-8 with a cp1252 fallback if most non-ASCII bytes are valid UTF-8;
    otherwise whichever of cp1252 and Mac Roman turns more of the high bytes into lowercase
    letters, which is what accented text inside words looks like.
    """
    try:
        raw.decode("utf-8-sig")
        return "utf-8", 0
    except UnicodeDecodeError:
        pass
    high = sum(1 for b in raw if b > 127)
    invalid = raw.decode("utf-8", errors="replace").count("\ufffd")
    if invalid * 4 < high:
        return UTF8_WITH_FALLBACK, invalid

    def score(enc: str) -> int:
        text = raw.decode(enc, errors="replace")
        return sum(1 for ch in text if ord(ch) > 127 and ch.islower())

    return max(("cp1252", "mac_roman"), key=score), invalid


def unique_headers(header: list[str]) -> tuple[list[str], list[dict]]:
    """Trim header names and make them unique.

    When a name repeats, every copy is numbered (`name_1`, `name_2`), so no copy silently keeps
    the plain name and a mapping has to say which one it means.
    """
    trimmed = [h.strip() or f"column_{i + 1}" for i, h in enumerate(header)]
    counts: dict[str, int] = {}
    for name in trimmed:
        counts[name] = counts.get(name, 0) + 1
    seen: dict[str, int] = {}
    names, renames = [], []
    for i, (original, name) in enumerate(zip(header, trimmed, strict=True)):
        if counts[name] > 1:
            seen[name] = seen.get(name, 0) + 1
            name = f"{name}_{seen[name]}"
        names.append(name)
        if name != original:
            renames.append({"index": i, "original": original, "name": name})
    if len(set(names)) != len(names):
        raise ValueError("could not make header names unique")
    return names, renames


def import_csv(raw: bytes, encoding: str | None = None) -> ImportResult:
    detected = encoding is None
    if encoding is None:
        encoding, _ = detect_encoding(raw)
    text = decode(raw, encoding)
    fallback = 0
    if encoding == UTF8_WITH_FALLBACK:
        fallback = raw.decode("utf-8", errors="replace").count("\ufffd")

    # newline="" lets the csv module see \r, \n and \r\n itself, including inside quoted cells.
    records = list(csv.reader(io.StringIO(text, newline="")))
    if not records:
        raise ValueError("the file has no header row")
    columns, renames = unique_headers(records[0])
    width = len(columns)
    rows, ragged = [], []
    for n, rec in enumerate(records[1:], start=1):
        if not rec:
            continue  # blank line between records
        if len(rec) != width:
            ragged.append({"row": n, "cells": len(rec)})
            if len(rec) > width:
                raise ValueError(
                    f"row {n} has {len(rec)} cells but the header has {width}; "
                    "refusing to drop the extra cells"
                )
            rec = rec + [""] * (width - len(rec))
        rows.append(rec)
    info = SourceInfo(
        sha256=hashlib.sha256(raw).hexdigest(),
        encoding=encoding,
        encoding_detected=detected,
        fallback_bytes=fallback,
        header_renames=renames,
        ragged_rows=ragged,
    )
    return ImportResult(columns=columns, rows=rows, info=info)
