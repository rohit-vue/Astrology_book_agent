"""Helpers for chapter_input_material_used (local pipelines).

Focus-only mode: Architect fills dense chapter_focus cues; no chart_snapshot.
Also: house-system labels (hard), book-level coverage and limited reuse (soft warnings).
"""
from __future__ import annotations

import re
from collections import defaultdict

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

TIGHT_ASPECT_ORB = 2.0
MAX_NON_ANCHOR_REUSE_CHAPTERS = 3
OUTER_TRANSIT_PLANETS = ("Jupiter", "Saturn", "Uranus", "Neptune", "Pluto")
ANGLE_TRANSIT_POINTS = ("Ascendant", "ASC", "Midheaven", "MC", "IC", "DC")

_NAME_ALIASES = {
    "sun": ("sun", "sol"),
    "moon": ("moon", "luna"),
    "mercury": ("mercury", "mercurio"),
    "venus": ("venus",),
    "mars": ("mars", "marte"),
    "jupiter": ("jupiter", "júpiter"),
    "saturn": ("saturn", "saturno"),
    "uranus": ("uranus", "urano"),
    "neptune": ("neptune", "neptuno"),
    "pluto": ("pluto", "plutón", "pluton"),
    "node": ("node", "nodo"),
    "chiron": ("chiron", "quirón", "quiron"),
    "part of fortune": ("part of fortune", "fortune", "pof"),
    "lilith": ("lilith",),
    "ascendant": ("ascendant", "ascendente", "asc"),
    "midheaven": ("midheaven", "medio cielo", "mc"),
    "rahu": ("rahu",),
    "ketu": ("ketu",),
    "ic": ("ic",),
    "dc": ("dc", "descendant", "descendente"),
}


def normalize_chapters_focus_only(chapters: list) -> None:
    """Mutate chapters: keep chapter_focus only; drop any chart_snapshot."""
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        material = chapter.get("chapter_input_material_used")
        if not isinstance(material, dict):
            material = {}
        focus = material.get("chapter_focus")
        if not isinstance(focus, dict):
            focus = {}
        chapter["chapter_input_material_used"] = {"chapter_focus": focus}


def normalize_structure_focus_only(structure: dict, astrology_data: dict | None = None) -> dict:
    """
    Ensure every chapter's chapter_input_material_used is focus-only.

    astrology_data is unused (kept for call-site compatibility with older enrich).
    """
    del astrology_data  # unused in focus-only mode
    chapters = []
    if isinstance(structure, dict):
        if isinstance(structure.get("chapters"), list):
            chapters = structure["chapters"]
        else:
            inner = structure.get("structure")
            if isinstance(inner, dict) and isinstance(inner.get("chapters"), list):
                chapters = inner["chapters"]
    normalize_chapters_focus_only(chapters)
    return structure


# Backward-compatible aliases (older call sites).
enrich_chapters_with_chart_snapshots = normalize_chapters_focus_only
enrich_structure_with_chart_snapshots = normalize_structure_focus_only


def validate_chapter_input_material_used(material, idx: int) -> list[str]:
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
    return errors


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())).strip()


def _pad(text: str) -> str:
    return f" {_norm_text(text)} "


def _aliases_for(name: str) -> tuple[str, ...]:
    key = _norm_text(name)
    extra = _NAME_ALIASES.get(key, ())
    aliases = (name, key) + tuple(extra)
    return tuple(dict.fromkeys(a for a in aliases if str(a).strip()))


def _contains_name(blob: str, name: str) -> bool:
    padded = blob if blob.startswith(" ") else _pad(blob)
    for alias in _aliases_for(name):
        token = _norm_text(alias)
        if token and f" {token} " in padded:
            return True
    return False


def _iter_chapter_cues(chapters: list):
    for idx, chapter in enumerate(chapters or [], start=1):
        if not isinstance(chapter, dict):
            continue
        material = chapter.get("chapter_input_material_used") or {}
        focus = material.get("chapter_focus") if isinstance(material, dict) else {}
        if not isinstance(focus, dict):
            continue
        for family in CUE_FAMILY_KEYS:
            val = focus.get(family)
            if not isinstance(val, list):
                continue
            for cue in val:
                text = str(cue or "").strip()
                if text:
                    yield idx, family, text


def _charts(astrology_data: dict) -> dict:
    if not isinstance(astrology_data, dict):
        return {}
    charts = astrology_data.get("CHARTS") or {}
    return charts if isinstance(charts, dict) else {}


def _western_data(astrology_data: dict) -> dict:
    block = _charts(astrology_data).get("WESTERN_HOROSCOPE") or {}
    data = block.get("Data") if isinstance(block, dict) else {}
    return data if isinstance(data, dict) else {}


def build_significant_coverage_requirements(astrology_data: dict) -> list[tuple[str, tuple[str, ...], str | None]]:
    """
    Return (label, names_to_match, family_or_none).

    names_to_match: all names must appear in the SAME cue (AND) when len > 1;
    a 1-tuple may match anywhere in the selected cue pool.
    family_or_none: limit search to that cue family, or any family if None.
    """
    req: list[tuple[str, tuple[str, ...], str | None]] = []
    western = _western_data(astrology_data)
    seen_bodies = set()
    for planet in western.get("planets") or []:
        if not isinstance(planet, dict):
            continue
        name = str(planet.get("name") or "").strip()
        if not name or name.casefold() in seen_bodies:
            continue
        seen_bodies.add(name.casefold())
        req.append((f"western body {name}", (name,), "western_cues"))
    lilith = western.get("lilith")
    if isinstance(lilith, dict) and str(lilith.get("name") or "").strip():
        req.append(("western body Lilith", ("Lilith",), "western_cues"))
    req.append(("western Ascendant", ("Ascendant",), "western_cues"))
    req.append(("western Midheaven", ("Midheaven",), "western_cues"))

    for aspect in western.get("aspects") or []:
        if not isinstance(aspect, dict):
            continue
        try:
            orb = float(aspect.get("orb"))
        except (TypeError, ValueError):
            continue
        if orb > TIGHT_ASPECT_ORB:
            continue
        p1 = str(aspect.get("aspecting_planet") or "").strip()
        p2 = str(aspect.get("aspected_planet") or "").strip()
        atype = str(aspect.get("type") or "").strip()
        if not (p1 and p2):
            continue
        label = f"tight aspect {p1} {atype or 'aspect'} {p2} (orb {orb})"
        req.append((label, (p1, p2), "western_cues"))

    planets_block = _charts(astrology_data).get("PLANETS") or {}
    planets = planets_block.get("Data") if isinstance(planets_block, dict) else planets_block
    if isinstance(planets, list):
        for planet in planets:
            if not isinstance(planet, dict):
                continue
            name = str(planet.get("name") or "").strip()
            if name:
                req.append((f"vedic body {name}", (name,), "planets_cues"))

    shadbala_block = _charts(astrology_data).get("SHADBALA") or {}
    shadbala = shadbala_block.get("Data") if isinstance(shadbala_block, dict) else shadbala_block
    if isinstance(shadbala, list) and shadbala:
        strongest = None
        strongest_pct = None
        for row in shadbala:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            if row.get("is_strong") is False:
                req.append((f"shadbala not-strong {name}", (name,), "shadbala_cues"))
            try:
                pct = float(row.get("strength_percent_of_minimum"))
            except (TypeError, ValueError):
                continue
            if strongest_pct is None or pct > strongest_pct:
                strongest_pct = pct
                strongest = name
        if strongest:
            req.append((f"shadbala strongest {strongest}", (strongest,), "shadbala_cues"))

    bhavabala_block = _charts(astrology_data).get("BHAVABALA") or {}
    bhavabala = bhavabala_block.get("Data") if isinstance(bhavabala_block, dict) else {}
    if isinstance(bhavabala, dict):
        summary = bhavabala.get("summary") or {}
        houses = bhavabala.get("houses") or []
        by_id = {
            h.get("id"): h
            for h in houses
            if isinstance(h, dict) and h.get("id") is not None
        }
        for kind, hid in (
            ("strongest", summary.get("strongest_house_id")),
            ("weakest", summary.get("weakest_house_id")),
        ):
            house = by_id.get(hid)
            if not isinstance(house, dict):
                continue
            sanskrit = str(house.get("name") or "").strip()
            label = f"bhavabala {kind} house {hid} {sanskrit}".strip()
            if sanskrit:
                req.append((label, (sanskrit,), "bhavabala_cues"))
            else:
                req.append((label, (f"house {hid}",), "bhavabala_cues"))

    vdasha_block = _charts(astrology_data).get("VDASHA") or {}
    vdasha = vdasha_block.get("Data") if isinstance(vdasha_block, dict) else {}
    if isinstance(vdasha, dict):
        for key in ("major", "minor", "sub_minor", "sub_sub_minor", "sub_sub_sub_minor"):
            row = vdasha.get(key) or {}
            if not isinstance(row, dict):
                continue
            planet = str(row.get("planet") or "").strip()
            if planet:
                req.append((f"vdasha {key} {planet}", (planet,), "vdasha_cues"))

    transits_block = _charts(astrology_data).get("NATAL_TRANSITS") or {}
    transits = transits_block.get("Data") if isinstance(transits_block, dict) else {}
    relations = (transits.get("transit_relation") or []) if isinstance(transits, dict) else []
    seen_outers = set()
    seen_angles = set()
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        tplanet = str(rel.get("transit_planet") or "").strip()
        if tplanet in OUTER_TRANSIT_PLANETS and tplanet.casefold() not in seen_outers:
            seen_outers.add(tplanet.casefold())
            req.append((f"outer transit {tplanet}", (tplanet,), "transit_cues"))
        nplanet = str(rel.get("natal_planet") or "").strip()
        if nplanet in ANGLE_TRANSIT_POINTS and nplanet.casefold() not in seen_angles:
            seen_angles.add(nplanet.casefold())
            req.append((f"angle transit to {nplanet}", (nplanet,), "transit_cues"))
    return req


def _requirement_met(cues: list[str], names: tuple[str, ...]) -> bool:
    if len(names) == 1:
        blob = _pad(" ".join(cues))
        return _contains_name(blob, names[0])
    for cue in cues:
        padded = _pad(cue)
        if all(_contains_name(padded, name) for name in names):
            return True
    return False


def validate_significant_coverage(chapters: list, astrology_data: dict) -> list[str]:
    """Significant chart factors must appear at least once across all chapter cues."""
    if not astrology_data:
        return []
    by_family: dict[str, list[str]] = defaultdict(list)
    all_cues: list[str] = []
    for _idx, family, text in _iter_chapter_cues(chapters):
        by_family[family].append(text)
        all_cues.append(text)
    errors = []
    for label, names, family in build_significant_coverage_requirements(astrology_data):
        pool = by_family.get(family, []) if family else all_cues
        if not _requirement_met(pool, names):
            errors.append(f"book coverage missing {label}")
    return errors


def _normalize_cue_for_reuse(text: str) -> str:
    t = _norm_text(text)
    for prefix in ("western", "vedic", "bhavabala"):
        if t.startswith(prefix + " "):
            t = t[len(prefix) + 1 :]
            break
    return t.strip()


def validate_cue_reuse(
    chapters: list,
    max_chapters: int = MAX_NON_ANCHOR_REUSE_CHAPTERS,
) -> list[str]:
    """Non-dasha cues may repeat in at most max_chapters chapters."""
    by_cue: dict[str, set[int]] = defaultdict(set)
    for idx, family, text in _iter_chapter_cues(chapters):
        if family == "vdasha_cues":
            continue
        key = _normalize_cue_for_reuse(text)
        if key:
            by_cue[key].add(idx)
    errors = []
    for key, chapter_ids in sorted(by_cue.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(chapter_ids) > max_chapters:
            shown = key[:90]
            chapters_s = ",".join(str(i) for i in sorted(chapter_ids))
            errors.append(
                f"non-anchor cue reused in {len(chapter_ids)} chapters "
                f"(max {max_chapters}): {shown!r} (chapters {chapters_s})"
            )
    return errors


def validate_book_chart_coverage(chapters: list, astrology_data: dict | None) -> list[str]:
    """Soft coverage + uniqueness warnings. Never block book generation."""
    warnings: list[str] = []
    if astrology_data:
        warnings.extend(validate_significant_coverage(chapters, astrology_data))
    warnings.extend(validate_cue_reuse(chapters))
    return warnings


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
