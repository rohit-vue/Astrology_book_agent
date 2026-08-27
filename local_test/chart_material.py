"""Helpers for chapter_input_material_used (local pipelines).

Modes (CHAPTER_MATERIAL_MODE env):
- structured (default): chapter_focus cue arrays + notes; soft chart_coverage checks
- freeform: open object (additionalProperties true). Architect selects
  source_paths into astrology_data; Python copies those records into
  chapter_input_material_used.source_records for the writer (no coverage).
  Hard safeguards still reject unguided Western/Vedic Ascendant-sign clashes
  and same house-number mixes in one chapter (notes must keep systems distinct).
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy

from structured_schemas import (
    CHAPTER_INPUT_MATERIAL_MAX_CHARS,
    CHAPTER_INPUT_NOTES_MAX_LENGTH,
)

CUE_FAMILY_KEYS = (
    "western_cues",
    "planets_cues",
    "shadbala_cues",
    "bhavabala_cues",
    "vdasha_cues",
    "transit_cues",
)

# Families that must be prefixed so western / vedic / bhavabala houses stay distinct.
LABELED_FAMILY_PREFIX = {
    "western_cues": "western",
    "planets_cues": "vedic",
    "bhavabala_cues": "bhavabala",
}

SOURCE_PATH_KEYS = (
    "source_paths",
    "source_record_paths",
    "astrology_source_paths",
)


def chapter_material_mode() -> str:
    """structured (default) | freeform. FREEFORM_CHAPTER_MATERIAL=1 aliases freeform."""
    if os.environ.get("FREEFORM_CHAPTER_MATERIAL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return "freeform"
    mode = os.environ.get("CHAPTER_MATERIAL_MODE", "structured").strip().lower()
    if mode in ("freeform", "free", "json_object", "json-object", "paths", "source_paths"):
        return "freeform"
    return "structured"


def is_freeform_chapter_material() -> bool:
    return chapter_material_mode() == "freeform"


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


def _prepare_chapter_input_material_structured(material) -> dict:
    """Normalize writer payload: chapter_focus + notes only (no chart_snapshot)."""
    if not isinstance(material, dict):
        material = {}
    focus = material.get("chapter_focus")
    if not isinstance(focus, dict):
        focus = {}
    notes_raw = material.get("notes")
    notes = "" if notes_raw is None else str(notes_raw).strip()
    if len(notes) > CHAPTER_INPUT_NOTES_MAX_LENGTH:
        notes = notes[:CHAPTER_INPUT_NOTES_MAX_LENGTH]
    return {"chapter_focus": focus, "notes": notes}


def _prepare_chapter_input_material_freeform(
    material,
    astrology_data: dict | None = None,
) -> dict:
    """Pass through architect blob; hydrate source_paths from astrology_data."""
    if not isinstance(material, dict):
        material = {}
    cleaned = {k: v for k, v in material.items() if k != "chart_snapshot"}
    return hydrate_chapter_input_material(cleaned, astrology_data)


def prepare_chapter_input_material(
    material,
    astrology_data: dict | None = None,
) -> dict:
    if is_freeform_chapter_material():
        return _prepare_chapter_input_material_freeform(material, astrology_data)
    return _prepare_chapter_input_material_structured(material)


def normalize_chapters_input_material(
    chapters: list,
    astrology_data: dict | None = None,
) -> None:
    """Mutate chapters: normalize (+ hydrate freeform paths) for active mode."""
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        material = prepare_chapter_input_material(
            chapter.get("chapter_input_material_used"),
            astrology_data,
        )
        chapter["chapter_input_material_used"] = material


def normalize_chapters_focus_only(
    chapters: list,
    astrology_data: dict | None = None,
) -> None:
    """Backward-compatible alias."""
    normalize_chapters_input_material(chapters, astrology_data)


def normalize_structure_focus_only(structure: dict, astrology_data: dict | None = None) -> dict:
    """Normalize every chapter's chapter_input_material_used for the active mode."""
    chapters = []
    if isinstance(structure, dict):
        if isinstance(structure.get("chapters"), list):
            chapters = structure["chapters"]
        else:
            inner = structure.get("structure")
            if isinstance(inner, dict) and isinstance(inner.get("chapters"), list):
                chapters = inner["chapters"]
    normalize_chapters_focus_only(chapters, astrology_data)
    return structure


# Backward-compatible aliases (older call sites).
enrich_chapters_with_chart_snapshots = normalize_chapters_focus_only
enrich_structure_with_chart_snapshots = normalize_structure_focus_only


def _validate_chapter_input_material_used_structured(material, idx: int) -> list[str]:
    errors = []
    if not isinstance(material, dict):
        return [f"chapter {idx} chapter_input_material_used missing or not an object"]
    focus = material.get("chapter_focus")
    if not isinstance(focus, dict):
        return [f"chapter {idx} chapter_input_material_used.chapter_focus missing or not an object"]
    rationale = str(focus.get("rationale", "") or "").strip()
    if not rationale:
        errors.append(f"chapter {idx} chapter_focus.rationale missing or empty")
    total_cues = 0
    for key in CUE_FAMILY_KEYS:
        val = focus.get(key)
        if not isinstance(val, list):
            errors.append(f"chapter {idx} chapter_focus.{key} must be an array")
            continue
        total_cues += sum(1 for x in val if str(x).strip())
        prefix = LABELED_FAMILY_PREFIX.get(key)
        if prefix:
            for cue in val:
                text = str(cue or "").strip()
                if not text:
                    continue
                if not text.casefold().startswith(prefix):
                    errors.append(
                        f"chapter {idx} chapter_focus.{key} must start with "
                        f"'{prefix} ' (got {text[:80]!r})"
                    )
                    break
    if total_cues == 0:
        errors.append(
            f"chapter {idx} chapter_focus has no chart cues "
            "(need at least one non-empty cue across western/planets/shadbala/"
            "bhavabala/vdasha/transit)"
        )
    western = focus.get("western_cues")
    if isinstance(western, list) and not any(str(x).strip() for x in western):
        errors.append(f"chapter {idx} chapter_focus.western_cues is empty")
    if "notes" in material and not isinstance(material.get("notes"), str):
        errors.append(f"chapter {idx} chapter_input_material_used.notes must be a string")
    return errors


def _path_system(path: str) -> str | None:
    """Classify a source path as western | vedic | bhavabala (or None)."""
    pu = str(path or "").upper().replace("/", ".")
    if "WESTERN_HOROSCOPE" in pu:
        return "western"
    if "BHAVABALA" in pu:
        return "bhavabala"
    # Vedic PLANETS branch (not western planets).
    if "PLANETS" in pu and "WESTERN" not in pu:
        return "vedic"
    return None


def _record_house_number(record) -> int | None:
    if not isinstance(record, dict):
        return None
    for key in ("house", "id"):
        raw = record.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            n = int(raw)
            if 1 <= n <= 12:
                return n
        if isinstance(raw, str) and raw.strip().isdigit():
            n = int(raw.strip())
            if 1 <= n <= 12:
                return n
    return None


def _is_ascendant_name(name: str) -> bool:
    n = str(name or "").casefold().strip()
    return n in {"ascendant", "asc", "rising", "lagna"}


def _notes_separate_western_vedic(notes: str) -> bool:
    """True when notes explicitly name Western and Vedic as distinct lenses."""
    n = str(notes or "").casefold()
    if not n.strip():
        return False
    has_western = "western" in n
    has_vedic = "vedic" in n or "sidereal" in n
    # Require both system names so unrelated "do not merge" prose cannot pass.
    return has_western and has_vedic


def _iter_source_records(material: dict):
    records = material.get("source_records")
    if not isinstance(records, list):
        return
    for item in records:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        yield path, item.get("record")


def validate_freeform_system_separation(material: dict, idx: int) -> list[str]:
    """
    Hard freeform safeguards (Derek-run learnings):

    1) Ascendant clash — Western Asc sign and Vedic Asc sign in the same chapter
       with different signs must have notes that keep the systems distinct.
    2) House-number mix — same house number from Western and Vedic planet/cusp
       records in one chapter must have the same notes separation.

    Multi-system chapters remain allowed (freeform essence); only unguided mixes fail.
    """
    if not isinstance(material, dict):
        return []

    western_asc_sign = None
    vedic_asc_sign = None
    western_houses: set[int] = set()
    vedic_houses: set[int] = set()

    for path, record in _iter_source_records(material):
        system = _path_system(path)
        if system is None or not isinstance(record, dict):
            continue
        pu = path.upper().replace("/", ".")
        house = _record_house_number(record)
        name = str(record.get("name") or "")

        if system == "western":
            if ".HOUSES." in pu and house is not None:
                western_houses.add(house)
                if house == 1 and record.get("sign"):
                    western_asc_sign = str(record.get("sign")).strip()
            elif house is not None:
                # Natal bodies / points carrying a western house arena.
                western_houses.add(house)
            if _is_ascendant_name(name) and record.get("sign"):
                western_asc_sign = str(record.get("sign")).strip() or western_asc_sign

        elif system == "vedic":
            if house is not None:
                vedic_houses.add(house)
            if _is_ascendant_name(name) and record.get("sign"):
                vedic_asc_sign = str(record.get("sign")).strip()

    errors: list[str] = []
    notes = material.get("notes") if isinstance(material.get("notes"), str) else ""
    separated = _notes_separate_western_vedic(notes)

    if (
        western_asc_sign
        and vedic_asc_sign
        and western_asc_sign.casefold() != vedic_asc_sign.casefold()
        and not separated
    ):
        errors.append(
            f"chapter {idx} mixes conflicting Ascendant signs without system-separation "
            f"notes (western {western_asc_sign} vs vedic {vedic_asc_sign}). "
            f"Keep them in different chapters, or add notes that name Western and Vedic "
            f"as distinct maps and forbid reconciling them."
        )

    overlap = sorted(western_houses & vedic_houses)
    if overlap and not separated:
        shown = ", ".join(str(h) for h in overlap[:6])
        suffix = "..." if len(overlap) > 6 else ""
        errors.append(
            f"chapter {idx} mixes western and vedic house number(s) [{shown}{suffix}] "
            f"without system-separation notes. Split systems across chapters, or add "
            f"notes that keep Western and Vedic house maps distinct (do not merge)."
        )

    return errors


def _validate_chapter_input_material_used_freeform(material, idx: int) -> list[str]:
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
        errors.extend(validate_freeform_system_separation(material, idx))
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


def validate_chapter_input_material_used(material, idx: int) -> list[str]:
    if is_freeform_chapter_material():
        return _validate_chapter_input_material_used_freeform(material, idx)
    return _validate_chapter_input_material_used_structured(material, idx)


def build_architect_validation_retry_guide(errors: list[str]) -> str:
    """
    Compact retry feedback for Architect (no previous JSON — keeps tokens down).

    Attach as an extra user message on attempt 2+.
    """
    cleaned = [str(e).strip() for e in (errors or []) if str(e).strip()]
    if not cleaned:
        cleaned = ["unknown validation failure"]

    blob = " ".join(cleaned).casefold()
    fix_lines: list[str] = []

    if "conflicting ascendant" in blob or (
        "ascendant" in blob and "western" in blob and "vedic" in blob
    ):
        fix_lines.append(
            "Ascendant clash: do not put conflicting Western vs Vedic Ascendant signs "
            "in the same chapter unless notes explicitly name both Western and Vedic "
            "as distinct maps and forbid reconciling them. Prefer splitting them across "
            "chapters."
        )
    if "house number" in blob and "western" in blob and "vedic" in blob:
        fix_lines.append(
            "House-number mix: if the same house number appears in both Western and "
            "Vedic records in one chapter, notes MUST contain the words Western and "
            "Vedic and keep those house maps distinct (do not merge). Otherwise move "
            "one system’s paths to another chapter."
        )
    if "theme must differ from title" in blob or "theme equal to title" in blob:
        fix_lines.append(
            "Theme vs title: each chapter theme must be conceptually distinct from its "
            "title (not a copy or paraphrase of the title)."
        )
    if "source_paths" in blob or "unresolved source_paths" in blob:
        fix_lines.append(
            "source_paths: use exact dotted paths that exist in the Comprehensive "
            "Astrological Data; every listed path must resolve."
        )
    if "invalid json" in blob:
        fix_lines.append(
            "JSON: return one complete valid JSON object matching the required schema."
        )
    if "incomplete" in blob:
        fix_lines.append(
            "Completeness: return the full book structure in one response; do not "
            "truncate mid-JSON."
        )
    if not fix_lines:
        fix_lines.append(
            "Revise chapter_input_material_used (source_paths and/or notes) and any "
            "other flagged fields so every validation error below is resolved."
        )

    error_block = "\n".join(f"- {e}" for e in cleaned)
    fix_block = "\n".join(f"- {line}" for line in fix_lines)
    return (
        "VALIDATION FEEDBACK — previous attempt failed. "
        "Return a complete corrected JSON book structure only (no commentary).\n\n"
        "Errors to fix:\n"
        f"{error_block}\n\n"
        "How to fix:\n"
        f"{fix_block}\n\n"
        "Rules:\n"
        "- Do not include the previous JSON; produce a fresh full structure.\n"
        "- Keep freeform source_paths selection, but satisfy the errors above.\n"
        "- Multi-system chapters are allowed only when notes explicitly name "
        "Western and Vedic as distinct maps."
    )


def build_architect_model_input(
    system_prompt: str,
    user_prompt: str,
    last_errors: list[str] | None = None,
) -> list[dict]:
    """Messages for Architect call; append retry guide when last_errors is set."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if last_errors:
        messages.append(
            {
                "role": "user",
                "content": build_architect_validation_retry_guide(last_errors),
            }
        )
    return messages


def chapter_material_preview(material) -> str:
    """Short log line for architect output."""
    if is_freeform_chapter_material():
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
    material = prepare_chapter_input_material(material)
    focus = material.get("chapter_focus") or {}
    rationale = str(focus.get("rationale", "") or "")
    notes = str(material.get("notes", "") or "")
    parts = [f"focus: {rationale[:80]}"]
    if notes:
        parts.append(f"notes: {notes[:60]}...")
    return " | ".join(parts)


def writer_chart_material_rule() -> str:
    if is_freeform_chapter_material():
        return (
            "**Chart material rule:** Treat chapter_input_material_used as authoritative. "
            "Prefer source_records (exact copies from the birth chart artifact selected by "
            "path). Use notes / other keys only as writer guidance. Do not invent chart "
            "facts beyond that object. Do not merge western, vedic, and bhavabala house "
            "systems into one house story."
        )
    return (
        "**Chart focus rule:** Ground this chapter in "
        "chapter_input_material_used.chapter_focus (dense chart cues). "
        "Use chapter_input_material_used.notes for additional architect guidance "
        "(narrative angle, emphasis, craft direction). Do not invent chart factors "
        "beyond chapter_focus."
    )


def build_significant_coverage_requirements(astrology_data: dict) -> list | None:
    """Structured mode: real requirements from chart_coverage. Freeform: None."""
    if is_freeform_chapter_material():
        return None
    from chart_coverage import (
        build_significant_coverage_requirements as _build,
    )

    return _build(astrology_data)


def validate_significant_coverage(chapters: list, astrology_data: dict) -> list[str]:
    """Structured mode only — freeform has no chapter_focus cue families."""
    if is_freeform_chapter_material():
        return []
    from chart_coverage import validate_significant_coverage as _validate

    return _validate(chapters, astrology_data)


def validate_cue_reuse(chapters: list, max_chapters: int = 3) -> list[str]:
    """Structured mode only — freeform has no chapter_focus cue families."""
    if is_freeform_chapter_material():
        return []
    from chart_coverage import validate_cue_reuse as _validate

    return _validate(chapters, max_chapters=max_chapters)


def validate_book_chart_coverage(chapters: list, astrology_data: dict | None) -> list[str]:
    """Soft coverage + reuse warnings in structured mode; no-op for freeform."""
    if is_freeform_chapter_material():
        return []
    from chart_coverage import validate_book_chart_coverage as _validate

    return _validate(chapters, astrology_data)


# Source-data contract for FetchAstrology. Missing/empty required branches
# must fail the stage instead of writing a hollow artifact for Architect.
REQUIRED_CHART_DATA_TYPES = {
    "WESTERN_HOROSCOPE": dict,
    "NATAL_TRANSITS": dict,
    "PLANETS": list,
    "SHADBALA": list,
    "BHAVABALA": dict,
    "VDASHA": dict,
}


def _nonempty_collection(value) -> bool:
    return isinstance(value, (dict, list)) and len(value) > 0


def validate_astrology_artifact(payload: dict) -> None:
    """Raise ValueError if META or a required CHARTS branch is missing/empty."""
    if not isinstance(payload, dict):
        raise ValueError("astrology artifact failed contract: payload is not an object")

    errors: list[str] = []
    meta = payload.get("META")
    if not isinstance(meta, dict) or not meta:
        errors.append("META missing or empty")

    charts = payload.get("CHARTS")
    if not isinstance(charts, dict):
        errors.append("CHARTS missing")
        raise ValueError("astrology artifact failed contract: " + "; ".join(errors))

    for key, expected_type in REQUIRED_CHART_DATA_TYPES.items():
        block = charts.get(key)
        data = block.get("Data") if isinstance(block, dict) else None
        path = f"CHARTS.{key}.Data"
        if not isinstance(data, expected_type) or len(data) == 0:
            errors.append(f"{path} missing or empty")
            continue
        if key == "WESTERN_HOROSCOPE" and not _nonempty_collection(data.get("planets")):
            errors.append(f"{path}.planets missing or empty")
        elif key == "NATAL_TRANSITS" and not _nonempty_collection(data.get("transit_relation")):
            errors.append(f"{path}.transit_relation missing or empty")
        elif key == "BHAVABALA":
            if not (
                _nonempty_collection(data.get("summary"))
                or _nonempty_collection(data.get("houses"))
            ):
                errors.append(f"{path} missing summary and houses")

    if errors:
        raise ValueError("astrology artifact failed contract: " + "; ".join(errors))
