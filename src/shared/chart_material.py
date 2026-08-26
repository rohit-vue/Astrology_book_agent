"""Helpers for freeform chapter_input_material_used (prod Architect / WriteChapters).

Architect selects source_paths into astrology_data; Python copies those records into
chapter_input_material_used.source_records for the writer. Optional notes and other
keys are allowed. Book-level cue coverage is retired (no-op stubs below).
"""
from __future__ import annotations

import json
import re
from copy import deepcopy

from structured_schemas import (
    CHAPTER_INPUT_MATERIAL_MAX_CHARS,
    CHAPTER_INPUT_NOTES_MAX_LENGTH,
)

SOURCE_PATH_KEYS = (
    "source_paths",
    "source_record_paths",
    "astrology_source_paths",
)


def resolve_astrology_path(root, path: str):
    """
    Resolve a dotted / slash path into astrology_data.

    Examples:
      CHARTS.WESTERN_HOROSCOPE.Data.planets.0
      CHARTS/PLANETS/Data/3
      /CHARTS/VDASHA/Data/major
    """
    raw = str(path or "").strip()
    if not raw:
        raise KeyError("empty source path")
    if raw.startswith("#"):
        raw = raw[1:]
    parts = [p for p in re.split(r"[./]", raw.strip("/")) if p]
    if not parts:
        raise KeyError(f"empty source path: {path!r}")
    cur = root
    for part in parts:
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
                continue
            matched = None
            for key in cur:
                if str(key).casefold() == part.casefold():
                    matched = key
                    break
            if matched is None:
                raise KeyError(f"path not found at {part!r} in {path!r}")
            cur = cur[matched]
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError as exc:
                raise KeyError(f"list index expected at {part!r} in {path!r}") from exc
            if idx < 0 or idx >= len(cur):
                raise KeyError(f"list index out of range at {part!r} in {path!r}")
            cur = cur[idx]
        else:
            raise KeyError(
                f"cannot traverse into {type(cur).__name__} at {part!r} in {path!r}"
            )
    return cur


def _extract_source_paths(material: dict) -> list[str]:
    for key in SOURCE_PATH_KEYS:
        val = material.get(key)
        if isinstance(val, list):
            return [str(p).strip() for p in val if str(p).strip()]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
    return []


def hydrate_chapter_input_material(material: dict, astrology_data: dict | None) -> dict:
    """
    Copy source records from astrology_data for each source_paths entry.

    Final writer payload keeps architect extras (notes, etc.) and adds:
      source_records: [{ "path": "...", "record": <copied value> }, ...]
    Unresolved paths are listed in source_paths_unresolved.
    """
    if not isinstance(material, dict):
        return {}
    out = {k: deepcopy(v) for k, v in material.items() if k != "chart_snapshot"}
    out.pop("source_records", None)
    out.pop("source_paths_unresolved", None)

    notes_raw = out.get("notes")
    if notes_raw is not None and not isinstance(notes_raw, str):
        out["notes"] = str(notes_raw)
    if isinstance(out.get("notes"), str) and len(out["notes"]) > CHAPTER_INPUT_NOTES_MAX_LENGTH:
        out["notes"] = out["notes"][:CHAPTER_INPUT_NOTES_MAX_LENGTH]

    paths = _extract_source_paths(out)
    if not paths or not isinstance(astrology_data, dict):
        return out

    records = []
    unresolved = []
    for path in paths:
        try:
            value = resolve_astrology_path(astrology_data, path)
            records.append({"path": path, "record": deepcopy(value)})
        except (KeyError, TypeError, ValueError):
            unresolved.append(path)
    if records:
        out["source_records"] = records
    if unresolved:
        out["source_paths_unresolved"] = unresolved
    return out


def prepare_chapter_input_material(
    material,
    astrology_data: dict | None = None,
) -> dict:
    """Pass through architect blob; hydrate source_paths from astrology_data."""
    if not isinstance(material, dict):
        material = {}
    cleaned = {k: v for k, v in material.items() if k != "chart_snapshot"}
    return hydrate_chapter_input_material(cleaned, astrology_data)


def normalize_chapters_input_material(
    chapters: list,
    astrology_data: dict | None = None,
) -> None:
    """Mutate chapters: hydrate freeform paths for the writer."""
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        material = prepare_chapter_input_material(
            chapter.get("chapter_input_material_used"),
            astrology_data,
        )
        chapter["chapter_input_material_used"] = material


def normalize_structure_input_material(
    structure: dict,
    astrology_data: dict | None = None,
) -> dict:
    """Normalize every chapter's chapter_input_material_used (hydrate paths)."""
    chapters = []
    if isinstance(structure, dict):
        if isinstance(structure.get("chapters"), list):
            chapters = structure["chapters"]
        else:
            inner = structure.get("structure")
            if isinstance(inner, dict) and isinstance(inner.get("chapters"), list):
                chapters = inner["chapters"]
    normalize_chapters_input_material(chapters, astrology_data)
    return structure


# Aliases used by older call sites / local naming.
normalize_chapters_focus_only = normalize_chapters_input_material
normalize_structure_focus_only = normalize_structure_input_material
enrich_chapters_with_chart_snapshots = normalize_chapters_input_material
enrich_structure_with_chart_snapshots = normalize_structure_input_material


def _source_records_match_astrology(
    material: dict,
    astrology_data: dict | None,
    idx: int,
) -> list[str]:
    """Equality check: each hydrated record must equal astrology_data at path."""
    if not isinstance(astrology_data, dict):
        return []
    records = material.get("source_records")
    if not isinstance(records, list):
        return []
    errors = []
    for item in records:
        if not isinstance(item, dict):
            errors.append(f"chapter {idx} source_records entry is not an object")
            continue
        path = str(item.get("path", "") or "").strip()
        if not path:
            errors.append(f"chapter {idx} source_records entry missing path")
            continue
        try:
            expected = resolve_astrology_path(astrology_data, path)
        except (KeyError, TypeError, ValueError):
            errors.append(
                f"chapter {idx} source_records path unresolved for equality: {path!r}"
            )
            continue
        if item.get("record") != expected:
            errors.append(
                f"chapter {idx} source_records[{path!r}] does not equal astrology_data"
            )
    return errors


def validate_chapter_input_material_used(
    material,
    idx: int,
    astrology_data: dict | None = None,
) -> list[str]:
    """Validate freeform material (paths + hydrated records + size + equality)."""
    errors = []
    if not isinstance(material, dict):
        return [f"chapter {idx} chapter_input_material_used missing or not an object"]
    if not material:
        errors.append(f"chapter {idx} chapter_input_material_used is empty")
        return errors
    paths = _extract_source_paths(material)
    if not paths:
        errors.append(
            f"chapter {idx} chapter_input_material_used.source_paths missing or empty "
            "(Architect must select exact paths into astrology_data)"
        )
    else:
        records = material.get("source_records")
        if not isinstance(records, list) or not records:
            errors.append(
                f"chapter {idx} chapter_input_material_used.source_records missing or empty "
                "after hydrate (no paths resolved from astrology_data)"
            )
        unresolved = material.get("source_paths_unresolved")
        if isinstance(unresolved, list) and unresolved:
            shown = unresolved[:5]
            suffix = "..." if len(unresolved) > 5 else ""
            errors.append(
                f"chapter {idx} unresolved source_paths: {shown}{suffix}"
            )
        errors.extend(_source_records_match_astrology(material, astrology_data, idx))
    if "notes" in material and material.get("notes") is not None:
        if not isinstance(material.get("notes"), str):
            errors.append(
                f"chapter {idx} chapter_input_material_used.notes must be a string"
            )
    try:
        size = len(json.dumps(material, ensure_ascii=False))
    except (TypeError, ValueError):
        errors.append(f"chapter {idx} chapter_input_material_used is not JSON-serializable")
        return errors
    if size > CHAPTER_INPUT_MATERIAL_MAX_CHARS:
        errors.append(
            f"chapter {idx} chapter_input_material_used too large "
            f"({size} chars; max {CHAPTER_INPUT_MATERIAL_MAX_CHARS})"
        )
    return errors


def chapter_material_preview(material) -> str:
    """Short log line for architect / writer output."""
    if not isinstance(material, dict):
        return "freeform (invalid)"
    paths = _extract_source_paths(material)
    records = material.get("source_records")
    n_rec = len(records) if isinstance(records, list) else 0
    try:
        size = len(json.dumps(material, ensure_ascii=False))
    except (TypeError, ValueError):
        size = 0
    return f"freeform paths={len(paths)} hydrated={n_rec} (~{size} chars)"


def writer_chart_material_rule() -> str:
    return (
        "**Chart material rule:** Treat chapter_input_material_used as authoritative. "
        "Prefer source_records (exact copies from the birth chart artifact selected by "
        "path). Use notes / other keys only as writer guidance. Do not invent chart "
        "facts beyond that object. Do not merge western, vedic, and bhavabala house "
        "systems into one house story."
    )


def validate_book_chart_coverage(chapters: list, astrology_data: dict | None) -> list[str]:
    """Coverage retired for freeform (client: no requirements). Always empty."""
    del chapters, astrology_data
    return []
