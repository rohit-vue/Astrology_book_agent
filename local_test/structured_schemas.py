"""
Local mirror of src/shared/structured_schemas.py for Docker / local imports.

Keep in sync with src/shared/structured_schemas.py (canonical for prod Lambdas).
"""
from __future__ import annotations

CHAPTER_TITLE_MAX_LENGTH = 70


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


def _chapter_input_material_used_schema() -> dict:
    """Client field name. Local focus-only: Architect fills chapter_focus only."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chapter_focus": _chapter_focus_schema(),
        },
        "required": ["chapter_focus"],
    }


def _chapter_item() -> dict:
    """Chapter outline item (local: title length cap + chapter_input_material_used)."""
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
            "chapter_input_material_used": _chapter_input_material_used_schema(),
        },
        "required": ["title", "theme", "description", "chapter_input_material_used"],
    }


def book_structure_schema(min_chapters: int, max_chapters: int) -> dict:
    """Schema matching local validate_book_structure / Architect outline."""
    if min_chapters < 1 or max_chapters < min_chapters:
        raise ValueError(f"invalid chapter bounds: min={min_chapters} max={max_chapters}")

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
                        "items": _chapter_item(),
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


def writing_style_schema() -> dict:
    """Strict 8-field writing STYLE profile (client style_analysis schema)."""
    return _string_object(WRITING_STYLE_FIELDS)


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
