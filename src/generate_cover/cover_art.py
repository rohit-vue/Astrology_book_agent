"""
Generate deep-space cover background from birth_data.

Uses OpenAI Images (gpt-image-2). Location context from lat/lon only.
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


def format_local_time_label(birth_data: dict) -> str:
    hour = int(birth_data.get("hour", 0)) % 24
    minute = int(birth_data.get("min", birth_data.get("minute", 0)))
    suffix = "AM" if hour < 12 else "PM"
    hour_12 = hour % 12 or 12
    return f"{hour:02d}:{minute:02d} ({hour_12}:{minute:02d} {suffix}) local time"


def format_geo_hint(birth_data: dict) -> str:
    lat = birth_data.get("lat")
    lon = birth_data.get("lon")
    if lat is None or lon is None:
        return "unknown coordinates"
    lat_f, lon_f = float(lat), float(lon)
    lat_dir = "N" if lat_f >= 0 else "S"
    lon_dir = "E" if lon_f >= 0 else "W"
    return f"{abs(lat_f):.2f}°{lat_dir}, {abs(lon_f):.2f}°{lon_dir}"


def get_cover_image_prompt_template() -> str:
    global _cover_prompt_template_cache
    if _cover_prompt_template_cache:
        return _cover_prompt_template_cache

    value = ssm_client.get_parameter(
        Name=COVER_PROMPT_SSM_NAME,
        WithDecryption=True,
    )["Parameter"]["Value"].strip()
    if not value:
        raise ValueError(f"SSM cover prompt parameter is empty: {COVER_PROMPT_SSM_NAME}")
    _cover_prompt_template_cache = value
    print(f"Loaded cover image prompt from SSM: {COVER_PROMPT_SSM_NAME}")
    return _cover_prompt_template_cache


def render_cover_image_prompt(template: str, birth_data: dict) -> str:
    replacements = {
        "__GEO_HINT__": format_geo_hint(birth_data),
        "__TIME_LABEL__": format_local_time_label(birth_data),
        "__DATE_LABEL__": format_birth_datetime_label(birth_data),
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()


def build_simple_sky_city_prompt(birth_data: dict) -> str:
    template = get_cover_image_prompt_template()
    return render_cover_image_prompt(template, birth_data)


def generate_cover_background_from_birth_data(birth_data: dict, api_key: str) -> Image.Image:
    prompt = build_simple_sky_city_prompt(birth_data)
    print(f"Cover art location: {format_geo_hint(birth_data)}")
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
