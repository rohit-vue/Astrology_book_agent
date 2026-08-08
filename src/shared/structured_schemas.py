"""JSON Schemas for OpenAI Structured Outputs (Architect + birth parse)."""
from __future__ import annotations


def _string_object(properties: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {"type": "string"} for key in properties},
        "required": list(properties),
    }


def book_structure_schema(min_chapters: int, max_chapters: int) -> dict:
    """Schema matching validate_book_structure / Architect outline."""
    if min_chapters < 1 or max_chapters < min_chapters:
        raise ValueError(f"invalid chapter bounds: min={min_chapters} max={max_chapters}")

    chapter_item = _string_object(("title", "theme", "description"))
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
                        "items": chapter_item,
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
