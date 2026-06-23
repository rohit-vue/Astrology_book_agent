"""
Generate sky-over-city cover background from birth_data.

Uses OpenAI Images (gpt-image-2) + Nominatim reverse geocode for place name.
"""
from __future__ import annotations

import base64
import io
import os
from datetime import datetime

import boto3
import requests
from openai import OpenAI
from PIL import Image

_aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
ssm_client = boto3.client("ssm", region_name=_aws_region)

MODEL_COVER_IMAGE = os.environ.get("MODEL_COVER_IMAGE", "gpt-image-2")
IMAGE_SIZE = os.environ.get("COVER_IMAGE_SIZE", "2560x1440")
COVER_PROMPT_SSM_NAME = os.environ.get(
    "COVER_PROMPT_SSM_NAME",
    "/AstrologyBookFactory/prompts/cover/image",
)
NOMINATIM_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "AstrologyBookFactory/1.0 (cover generation)",
)

COVER_IMAGE_PROMPT_FALLBACK = """Photorealistic wide horizontal landscape photograph of the open sky above __PLACE_NAME__.__GEO_HINT__
Captured at __TIME_LABEL__ on __DATE_LABEL__.
The scene must match __TIME_PERIOD__ at this exact local time and place.
__SKY_LIGHTING__ __SEASON_HINT__
Horizon line low in the frame; most of the image is sky.
A quiet, distant sense of the city far below — soft haze or far-off buildings only,
not a skyline silhouette and not a dramatic cityscape.
Do not show: __SKY_AVOID__.
No text, no letters, no people, no planets, no moon emphasis,
no constellations, no zodiac, no galaxy, no outer space effects.
Keep the original ground as it would be at __PLACE_NAME__; include a famous building or memorial of that city/country to help identify the location."""

_cover_prompt_template_cache: str | None = None


def format_birth_datetime_label(birth_data: dict) -> str:
    year = int(birth_data.get("year", 2000))
    month = int(birth_data.get("month", 1))
    day = int(birth_data.get("day", 1))
    hour = int(birth_data.get("hour", 0))
    minute = int(birth_data.get("min", birth_data.get("minute", 0)))
    try:
        dt = datetime(year, month, day, hour, minute)
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return f"{year}-{month:02d}-{day:02d}"


def format_local_time_label(birth_data: dict) -> tuple[str, int, int]:
    hour = int(birth_data.get("hour", 0)) % 24
    minute = int(birth_data.get("min", birth_data.get("minute", 0)))
    suffix = "AM" if hour < 12 else "PM"
    hour_12 = hour % 12 or 12
    label = f"{hour:02d}:{minute:02d} ({hour_12}:{minute:02d} {suffix}) local time"
    return label, hour, minute


def describe_season_hint(birth_data: dict) -> str:
    month = int(birth_data.get("month", 1))
    lat = float(birth_data.get("lat", 0))
    southern = lat < 0
    if month in (12, 1, 2):
        season = "summer" if southern else "winter"
    elif month in (3, 4, 5):
        season = "autumn" if southern else "spring"
    elif month in (6, 7, 8):
        season = "winter" if southern else "summer"
    else:
        season = "spring" if southern else "autumn"
    hemisphere = "southern" if southern else "northern"
    return f"Seasonal atmosphere for {hemisphere}-hemisphere {season}."


def describe_time_of_day_sky(hour: int, minute: int = 0) -> dict[str, str]:
    """Map local clock time to explicit sky/lighting language for image generation."""
    clock = hour + minute / 60.0

    if clock >= 22 or clock < 5:
        return {
            "period": "nighttime",
            "lighting": (
                "Deep night sky: dark navy to near-black, clearly nighttime. "
                "Stars visible; moon may appear if realistic. "
                "Only faint artificial glow from the distant city on the horizon. "
                "Cool, subdued tones — not daylight."
            ),
            "avoid": (
                "sunrise, sunset, golden hour, orange sky, pink clouds, "
                "warm haze, bright daylight, blue midday sky"
            ),
        }
    if clock < 7:
        return {
            "period": "pre-dawn",
            "lighting": (
                "Pre-dawn blue hour: deep blue-grey sky with the faintest light along the horizon. "
                "Cool colors only; still mostly dark."
            ),
            "avoid": "midday brightness, golden sunset orange, full daylight",
        }
    if clock < 10:
        return {
            "period": "early morning",
            "lighting": (
                "Early morning sky: soft cool-to-warm transition, low sun off-frame, "
                "gentle morning light — not sunset and not full midday glare."
            ),
            "avoid": "deep night, sunset orange overload, harsh noon sun overhead",
        }
    if clock < 14:
        return {
            "period": "midday",
            "lighting": (
                "Bright midday sky: clear blue or fair-weather clouds, "
                "strong daylight, high sun — not sunrise or sunset colors."
            ),
            "avoid": "orange sunset glow, twilight, night sky, golden hour",
        }
    if clock < 17:
        return {
            "period": "afternoon",
            "lighting": (
                "Afternoon sky: bright but softer than noon, neutral daylight tones, "
                "no dramatic sunset orange yet."
            ),
            "avoid": "deep night, sunrise pink, heavy golden-hour orange",
        }
    if clock < 20:
        return {
            "period": "evening golden hour",
            "lighting": (
                "Evening golden hour: warm low-angle sunlight, orange and amber near the horizon, "
                "long shadows, dusk atmosphere."
            ),
            "avoid": "bright midday blue sky, deep night, star-filled darkness",
        }
    return {
        "period": "twilight",
        "lighting": (
            "Late twilight: sky darkening after sunset, deep blue and purple tones, "
            "last traces of warmth low on the horizon, transitioning toward night."
        ),
        "avoid": "bright midday sun, full starry night, strong orange sunrise",
    }


def format_geo_hint(birth_data: dict) -> str:
    lat = birth_data.get("lat")
    lon = birth_data.get("lon")
    if lat is None or lon is None:
        return ""
    lat_f, lon_f = float(lat), float(lon)
    lat_dir = "N" if lat_f >= 0 else "S"
    lon_dir = "E" if lon_f >= 0 else "W"
    return f" Viewpoint at {abs(lat_f):.2f}°{lat_dir}, {abs(lon_f):.2f}°{lon_dir}."


def reverse_geocode_place(lat: float, lon: float) -> str:
    """Resolve city/region label from coordinates (OpenStreetMap Nominatim)."""
    response = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    address = data.get("address") or {}

    parts: list[str] = []
    for key in ("city", "town", "village", "municipality", "county", "state", "country"):
        value = address.get(key)
        if value and value not in parts:
            parts.append(str(value))

    if parts:
        return ", ".join(parts[:3])
    display = (data.get("display_name") or "").strip()
    if display:
        return display.split(",")[0]
    return f"{lat:.4f}, {lon:.4f}"


def resolve_place_name(birth_data: dict) -> str:
    explicit = (birth_data.get("place_name") or birth_data.get("place") or "").strip()
    if explicit:
        return explicit
    lat = birth_data.get("lat")
    lon = birth_data.get("lon")
    if lat is None or lon is None:
        raise ValueError("birth_data needs place_name or lat/lon for cover art")
    return reverse_geocode_place(float(lat), float(lon))


def get_cover_image_prompt_template() -> str:
    global _cover_prompt_template_cache
    if _cover_prompt_template_cache:
        return _cover_prompt_template_cache

    try:
        value = ssm_client.get_parameter(
            Name=COVER_PROMPT_SSM_NAME,
            WithDecryption=True,
        )["Parameter"]["Value"].strip()
        if not value:
            raise ValueError("SSM cover prompt parameter is empty")
        _cover_prompt_template_cache = value
        print(f"Loaded cover image prompt from SSM: {COVER_PROMPT_SSM_NAME}")
    except Exception as exc:
        print(f"SSM cover prompt unavailable; using code fallback: {exc}")
        _cover_prompt_template_cache = COVER_IMAGE_PROMPT_FALLBACK

    return _cover_prompt_template_cache


def render_cover_image_prompt(template: str, place_name: str, birth_data: dict) -> str:
    date_label = format_birth_datetime_label(birth_data)
    time_label, hour, minute = format_local_time_label(birth_data)
    sky = describe_time_of_day_sky(hour, minute)
    season = describe_season_hint(birth_data)
    geo = format_geo_hint(birth_data)

    replacements = {
        "__PLACE_NAME__": place_name,
        "__DATE_LABEL__": date_label,
        "__TIME_LABEL__": time_label,
        "__TIME_PERIOD__": sky["period"],
        "__SKY_LIGHTING__": sky["lighting"],
        "__SEASON_HINT__": season,
        "__SKY_AVOID__": sky["avoid"],
        "__GEO_HINT__": geo,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()


def build_simple_sky_city_prompt(place_name: str, birth_data: dict) -> str:
    template = get_cover_image_prompt_template()
    return render_cover_image_prompt(template, place_name, birth_data)


def generate_cover_background_from_birth_data(birth_data: dict, api_key: str) -> Image.Image:
    place_name = resolve_place_name(birth_data)
    prompt = build_simple_sky_city_prompt(place_name, birth_data)
    print(f"Cover art place: {place_name}")
    print(f"Cover art prompt: {prompt}")

    client = OpenAI(api_key=api_key)
    response = client.images.generate(
        model=MODEL_COVER_IMAGE,
        prompt=prompt,
        n=1,
        size=IMAGE_SIZE,
        quality="medium",
    )
    item = response.data[0]
    if getattr(item, "b64_json", None):
        raw = base64.b64decode(item.b64_json)
    elif getattr(item, "url", None):
        download = requests.get(item.url, timeout=120)
        download.raise_for_status()
        raw = download.content
    else:
        raise RuntimeError("Image API response has neither b64_json nor url")

    return Image.open(io.BytesIO(raw)).convert("RGB")
