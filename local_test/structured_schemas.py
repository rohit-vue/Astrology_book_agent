"""
Local mirror of src/shared/structured_schemas.py for Docker / local imports.

Prod (src/shared) is freeform-only for chapter_input_material_used.
Local keeps dual-mode (structured | freeform) via chapter_material_mode.
"""
from __future__ import annotations

import json

CHAPTER_TITLE_MAX_LENGTH = 70
CHAPTER_INPUT_NOTES_MAX_LENGTH = 2000
# Soft cap for freeform chapter_input_material_used JSON size (local experiment).
CHAPTER_INPUT_MATERIAL_MAX_CHARS = 12000


def _string_object(properties: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {"type": "string"} for key in properties},
        "required": list(properties),
    }


def _string_array() -> dict:
    return {
        "type": "array",
        "items": {"type": "string"},
    }


def _chapter_focus_schema() -> dict:
    """Architect-selected dense chart cues (focus-only; no chart_snapshot)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rationale": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Why these chart factors are the primary material for this chapter."
                ),
            },
            "western_cues": {
                **_string_array(),
                "description": (
                    "Prefix every cue with 'western '. Dense WESTERN_HOROSCOPE "
                    "cues for THIS chapter (soft cap ~6-10). Use western "
                    "signs/houses only. Include name/sign/house/norm_degree/"
                    "full_degree/is_retro; type+orb for aspects. Assign each "
                    "significant western factor to one primary chapter; do not "
                    "paste the same full list across chapters."
                ),
            },
            "planets_cues": {
                **_string_array(),
                "description": (
                    "Prefix every cue with 'vedic '. Dense PLANETS cues for "
                    "THIS chapter (soft cap ~4-8), including sign, nakshatra, "
                    "house, awastha, isRetro, normDegree, fullDegree. Vedic "
                    "houses/signs are not western houses."
                ),
            },
            "shadbala_cues": {
                **_string_array(),
                "description": (
                    "Shadbala cues for this chapter only (soft cap ~2-6). "
                    "Book must cover the strongest planet and any not-strong planet."
                ),
            },
            "bhavabala_cues": {
                **_string_array(),
                "description": (
                    "Prefix every cue with 'bhavabala '. Include Sanskrit name "
                    "(Sukha, Putra, Dhana, etc.). Soft cap ~2-6. Never treat "
                    "these as western houses."
                ),
            },
            "vdasha_cues": {
                **_string_array(),
                "description": (
                    "Current VDASHA periods for this chapter (soft cap ~2-5). "
                    "Copy planet + period dates only; no psychological meanings. "
                    "Dasha stack may repeat across chapters."
                ),
            },
            "transit_cues": {
                **_string_array(),
                "description": (
                    "Dense NATAL_TRANSITS cues for today for THIS chapter "
                    "(soft cap ~4-8). Include transit planet, aspect, natal "
                    "point, signs/houses, retro. Assign each significant transit "
                    "to one primary chapter; only 1-2 signature transits may "
                    "repeat (max 3 chapters)."
                ),
            },
        },
        "required": [
            "rationale",
            "western_cues",
            "planets_cues",
            "shadbala_cues",
            "bhavabala_cues",
            "vdasha_cues",
            "transit_cues",
        ],
    }


def _chapter_input_material_used_schema_structured() -> dict:
    """chapter_focus (structured cues) + notes. Default local structured mode."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chapter_focus": _chapter_focus_schema(),
            "notes": {
                "type": "string",
                "maxLength": CHAPTER_INPUT_NOTES_MAX_LENGTH,
                "description": (
                    "Free-form architect notes for the chapter writer: narrative angles, "
                    "emphasis, connections, or craft guidance not captured in chapter_focus. "
                    "Use empty string if nothing extra is needed."
                ),
            },
        },
        "required": ["chapter_focus", "notes"],
    }


def _chapter_input_material_used_schema_freeform() -> dict:
    """
    Client experiment: open object (additionalProperties true).

    Architect selects source_paths into astrology_data; Python hydrates records.
    Extra keys (notes, rationale, etc.) are allowed.
    """
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "source_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exact dotted paths into the Comprehensive Astrological Data "
                    "(e.g. CHARTS.WESTERN_HOROSCOPE.Data.planets.0). Python copies "
                    "those source records into the final material."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "Optional free-form writer guidance (narrative angle, emphasis). "
                    "Not a substitute for source_paths."
                ),
            },
        },
    }


def _chapter_item(*, chapter_material_mode: str = "structured") -> dict:
    """Chapter outline item (local: title length cap + chapter_input_material_used)."""
    if chapter_material_mode == "freeform":
        material_schema = _chapter_input_material_used_schema_freeform()
    else:
        material_schema = _chapter_input_material_used_schema_structured()
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": CHAPTER_TITLE_MAX_LENGTH,
            },
            "theme": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "chapter_input_material_used": material_schema,
        },
        "required": ["title", "theme", "description", "chapter_input_material_used"],
    }


def book_structure_schema(
    min_chapters: int,
    max_chapters: int,
    *,
    chapter_material_mode: str = "structured",
) -> dict:
    """Schema matching local validate_book_structure / Architect outline."""
    if min_chapters < 1 or max_chapters < min_chapters:
        raise ValueError(f"invalid chapter bounds: min={min_chapters} max={max_chapters}")
    if chapter_material_mode not in {"structured", "freeform"}:
        raise ValueError(
            f"invalid chapter_material_mode: {chapter_material_mode!r} "
            "(expected 'structured' or 'freeform')"
        )

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "metadata": _string_object(
                (
                    "title",
                    "subtitle",
                    "footer_text",
                    "preface_title",
                    "prologue_title",
                    "epilogue_title",
                    "dedication_title",
                )
            ),
            "ui_labels": _string_object(("toc_title", "chapter_prefix")),
            "structure": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "preface_description": {"type": "string"},
                    "prologue_description": {"type": "string"},
                    "epilogue_description": {"type": "string"},
                    "chapters": {
                        "type": "array",
                        "minItems": min_chapters,
                        "maxItems": max_chapters,
                        "items": _chapter_item(chapter_material_mode=chapter_material_mode),
                    },
                },
                "required": [
                    "preface_description",
                    "prologue_description",
                    "epilogue_description",
                    "chapters",
                ],
            },
        },
        "required": ["metadata", "ui_labels", "structure"],
    }


def book_structure_schema_strict(chapter_material_mode: str = "structured") -> bool:
    """Freeform material uses additionalProperties:true → OpenAI json_schema strict=false."""
    return chapter_material_mode != "freeform"


BIRTH_DATA_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "day": {"type": "integer"},
        "month": {"type": "integer"},
        "year": {"type": "integer"},
        "hour": {"type": "integer"},
        "min": {"type": "integer"},
        "lat": {"type": "number"},
        "lon": {"type": "number"},
        "tzone": {"type": "number"},
    },
    "required": ["day", "month", "year", "hour", "min", "lat", "lon", "tzone"],
}


WRITING_STYLE_FIELDS: tuple[str, ...] = (
    "core_voice",
    "narrative_cognitive",
    "temporal_rhythm",
    "energetic_texture",
    "sensory_hierarchy",
    "metaphoric_logic",
    "emotional_shadow",
    "silence_negative_space",
)

WRITING_STYLE_DOMAIN_KEYS: tuple[str, ...] = (
    "emphasize",
    "suppress_or_use_sparingly",
)

# Keep injected STYLE compact so chapter writer can follow it as a checklist.
STYLE_FIELD_MAX_LENGTH = 180


def _style_domain_object() -> dict:
    """One style domain: emphasize + suppress_or_use_sparingly."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "emphasize": {
                "type": "string",
                "minLength": 1,
                "maxLength": STYLE_FIELD_MAX_LENGTH,
                "description": (
                    f"One short executable craft instruction (max {STYLE_FIELD_MAX_LENGTH} chars)."
                ),
            },
            "suppress_or_use_sparingly": {
                "type": "string",
                "minLength": 1,
                "maxLength": STYLE_FIELD_MAX_LENGTH,
                "description": (
                    f"Short avoid / use-sparingly bans (max {STYLE_FIELD_MAX_LENGTH} chars)."
                ),
            },
        },
        "required": list(WRITING_STYLE_DOMAIN_KEYS),
    }


def writing_style_schema() -> dict:
    """Strict 8-domain STYLE profile (emphasize / suppress per domain). Local-first."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: _style_domain_object() for key in WRITING_STYLE_FIELDS},
        "required": list(WRITING_STYLE_FIELDS),
    }


def validate_writing_style_object(style_obj) -> list[str]:
    """Validate nested emphasize/suppress style object."""
    errors: list[str] = []
    if not isinstance(style_obj, dict):
        return ["style response is not an object"]
    extra = set(style_obj.keys()) - set(WRITING_STYLE_FIELDS)
    if extra:
        errors.append(f"unexpected style fields: {sorted(extra)}")
    for key in WRITING_STYLE_FIELDS:
        domain = style_obj.get(key)
        if not isinstance(domain, dict):
            errors.append(f"style field '{key}' missing or not an object")
            continue
        domain_extra = set(domain.keys()) - set(WRITING_STYLE_DOMAIN_KEYS)
        if domain_extra:
            errors.append(
                f"style field '{key}' unexpected keys: {sorted(domain_extra)}"
            )
        for sub in WRITING_STYLE_DOMAIN_KEYS:
            value = str(domain.get(sub, "") or "").strip()
            if not value:
                errors.append(f"style field '{key}.{sub}' missing or empty")
            # Over-length is truncated in normalize_writing_style (schema maxLength guides the model).
    return errors


def normalize_writing_style(style_obj: dict) -> dict:
    """Return only required domains/subfields, stripped (truncate overlong fields)."""
    out: dict = {}
    for key in WRITING_STYLE_FIELDS:
        domain = style_obj.get(key) or {}
        out[key] = {}
        for sub in WRITING_STYLE_DOMAIN_KEYS:
            value = str(domain.get(sub, "") or "").strip()
            if len(value) > STYLE_FIELD_MAX_LENGTH:
                value = value[:STYLE_FIELD_MAX_LENGTH].rstrip()
            out[key][sub] = value
    return out


def style_json_for_writer(style_obj: dict) -> str:
    """Compact JSON string for chapter/section Style injection."""
    return json.dumps(normalize_writing_style(style_obj), ensure_ascii=False, separators=(",", ":"))


def flatten_writing_style(style_obj: dict) -> str:
    """Human-readable STYLE brief (artifacts / debugging; not preferred for writer injection)."""
    normalized = normalize_writing_style(style_obj)
    lines: list[str] = []
    for key in WRITING_STYLE_FIELDS:
        domain = normalized[key]
        label = key.replace("_", " ").upper()
        lines.append(f"{label}")
        lines.append(f"  Emphasize: {domain['emphasize']}")
        lines.append(f"  Suppress or use sparingly: {domain['suppress_or_use_sparingly']}")
        lines.append("")
    return "\n".join(lines).strip()


def responses_text_format(
    name: str,
    schema: dict,
    *,
    verbosity: str | None = None,
    strict: bool = True,
) -> dict:
    """Build Responses API `text=` argument with json_schema format."""
    fmt: dict = {
        "format": {
            "type": "json_schema",
            "name": name,
            "strict": strict,
            "schema": schema,
        }
    }
    if verbosity is not None:
        fmt["verbosity"] = verbosity
    return fmt


def chat_response_format(name: str, schema: dict, *, strict: bool = True) -> dict:
    """Build Chat Completions `response_format=` argument with json_schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": strict,
            "schema": schema,
        },
    }
