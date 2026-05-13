# FILE: src/generate_cover/app.py

import boto3
import json
import os
import io
from typing import Tuple
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

s3_client = boto3.client("s3")
secrets_manager = boto3.client("secretsmanager")

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")
API_KEYS_SECRET_ARN = os.environ.get("API_KEYS_SECRET_ARN")
LULU_API_BASE = os.environ.get("LULU_API_BASE", "https://api.lulu.com").rstrip("/")
LULU_POD_PACKAGE_ID = os.environ.get("LULU_POD_PACKAGE_ID", "0550X0850.BW.STD.LW.060UC444.MNG")

PROTECTIVE_COVER_TEXT = {
    "english": "Remove Protective Cover Before Reading",
    "spanish": "Retire la cubierta protectora antes de leer",
    "french": "Retirez la couverture de protection avant de lire",
    "german": "Schutzumschlag vor dem Lesen entfernen",
    "italian": "Rimuovere la copertina protettiva prima di leggere",
    "portuguese": "Remova a capa protetora antes de ler",
    "japanese": "\u8aad\u3080\u524d\u306b\u4fdd\u8b77\u30ab\u30d0\u30fc\u3092\u53d6\u308a\u5916\u3057\u3066\u304f\u3060\u3055\u3044",
    "hindi": "\u092a\u095d\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 \u0938\u0941\u0930\u0915\u094d\u0937\u093e \u0915\u0935\u0930 \u0939\u091f\u093e\u090f\u0901",
    "chinese": "\u9605\u8bfb\u524d\u8bf7\u53d4\u4e0b\u4fdd\u62a4\u5c01\u5957",
    "korean": "\uc77d\uae30 \uc804\uc5d0 \ubcf4\ud638 \ucee4\ubc84\ub97c \uc81c\uac70\ud558\uc138\uc694",
    "russian": "\u0421\u043d\u0438\u043c\u0438\u0442\u0435 \u0437\u0430\u0449\u0438\u0442\u043d\u0443\u044e \u043e\u0431\u043b\u043e\u0436\u043a\u0443 \u043f\u0435\u0440\u0435\u0434 \u0447\u0442\u0435\u043d\u0438\u0435\u043c",
}


def resolve_language_key(language: str) -> str:
    return (language or "English").strip().lower()


def resolve_cover_text(language: str) -> str:
    language_key = resolve_language_key(language)
    return PROTECTIVE_COVER_TEXT.get(language_key, PROTECTIVE_COVER_TEXT["english"])


def resolve_wrap_mode(language: str) -> str:
    if resolve_language_key(language) in {"chinese", "japanese"}:
        return "char"
    return "word"


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    wrap_mode: str = "word",
):
    if not text:
        return [text]

    if wrap_mode == "char":
        chunks = list(text)
        joiner = ""
    else:
        chunks = text.split()
        joiner = " "

    if not chunks:
        return [text]

    lines = []
    current = chunks[0]
    for chunk in chunks[1:]:
        candidate = f"{current}{joiner}{chunk}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = chunk
    lines.append(current)
    return lines


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
    wrap_mode: str = "word",
):
    x, y, w, h = box
    lines = wrap_text(draw, text, font, max(1, int(w * 0.9)), wrap_mode=wrap_mode)
    bbox_sample = font.getbbox("Aj")
    line_height = (bbox_sample[3] - bbox_sample[1]) + 18
    total_height = len(lines) * line_height
    current_y = y + (h - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x + (w - text_w) // 2, current_y), line, font=font, fill=(255, 255, 255))
        current_y += line_height


def _bundled_fonts_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def load_font_for_language(language: str, size: int):
    """Pick a TrueType font by language; search bundled and common Lambda/system directories."""
    bundle = _bundled_fonts_dir()
    lambda_fonts_dir = os.path.join(os.environ.get("LAMBDA_TASK_ROOT", ""), "fonts")
    fallback_dirs = [
        bundle,
        lambda_fonts_dir,
        "fonts",
        "/opt/fonts",
        "/usr/share/fonts/opentype/noto",
        "/usr/share/fonts/truetype/noto",
        "/usr/share/fonts/truetype/dejavu",
    ]

    language_key = resolve_language_key(language)
    if language_key in {"chinese", "japanese"}:
        font_candidates = [
            "NotoSansCJK-Regular.ttc",
            "NotoSansCJKsc-Regular.otf",
            "NotoSansCJKjp-Regular.otf",
            "DejaVuSans.ttf",
        ]
    elif language_key == "russian":
        font_candidates = [
            "DejaVuSans.ttf",
            "NotoSans-Regular.ttf",
            "LibreBaskerville-Regular.ttf",
        ]
    else:
        font_candidates = [
            "LibreBaskerville-Regular.ttf",
            "DejaVuSans.ttf",
        ]

    for font_name in font_candidates:
        for directory in fallback_dirs:
            if not directory:
                continue
            font_path = os.path.join(directory, font_name)
            if os.path.exists(font_path):
                try:
                    return ImageFont.truetype(font_path, size)
                except Exception:
                    continue

    return ImageFont.load_default()


def get_lulu_token(client_key: str, client_secret: str) -> str:
    auth_url = f"{LULU_API_BASE}/auth/realms/glasstree/protocol/openid-connect/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {"grant_type": "client_credentials"}
    r = requests.post(auth_url, headers=headers, auth=(client_key, client_secret), data=payload, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_lulu_cover_dimensions_inches(
    token: str, pod_package_id: str, interior_page_count: int
) -> Tuple[float, float]:
    """POST /cover-dimensions/ — authoritative full-spread size for this POD + page count (OpenAPI)."""
    url = f"{LULU_API_BASE}/cover-dimensions/"
    body = {
        "pod_package_id": pod_package_id,
        "interior_page_count": int(interior_page_count),
        "unit": "inch",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    r = requests.post(url, headers=headers, json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    return float(data["width"]), float(data["height"])


def _legacy_cover_size_inches(page_count: int) -> Tuple[float, float, float]:
    """Previous approximation (spine heuristic) — used only if Lulu API fails."""
    PAGE_MULTIPLIER = 0.002252
    COVER_WIDTH = 5.895
    COVER_HEIGHT = 9.0
    FLAP_WIDTH = 3.5
    BLEED = 0.125
    spine_width = page_count * PAGE_MULTIPLIER
    total_w = (BLEED * 2) + (FLAP_WIDTH * 2) + (COVER_WIDTH * 2) + spine_width
    total_h = (BLEED * 2) + COVER_HEIGHT
    return total_w, total_h, spine_width


def resolve_cover_layout_inches(
    page_count: int, pod_package_id: str
) -> Tuple[float, float, float, dict]:
    """
    Returns (total_w_inch, total_h_inch, spine_width_inch, meta_for_logs).
    Spine is derived from Lulu total width minus fixed trim bands so the spread matches validation.
    """
    COVER_WIDTH = 5.895
    FLAP_WIDTH = 3.5
    BLEED = 0.125
    fixed_band = (BLEED * 2) + (FLAP_WIDTH * 2) + (COVER_WIDTH * 2)

    meta: dict = {"source": "lulu_api"}
    try:
        if not API_KEYS_SECRET_ARN:
            raise ValueError("API_KEYS_SECRET_ARN not set")
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload["SecretString"])
        client_key = secrets.get("LuluApiClientKey")
        client_secret = secrets.get("LuluApiClientSecret")
        if not client_key or not client_secret:
            raise ValueError("Lulu API credentials missing in secret")
        token = get_lulu_token(client_key, client_secret)
        total_w, total_h = fetch_lulu_cover_dimensions_inches(token, pod_package_id, page_count)
        spine_width = total_w - fixed_band
        if spine_width <= 0:
            raise ValueError(f"Non-positive spine from Lulu width {total_w}: check trim constants vs product.")
        meta["lulu_width_in"] = total_w
        meta["lulu_height_in"] = total_h
        meta["derived_spine_in"] = spine_width
        return total_w, total_h, spine_width, meta
    except Exception as e:
        print(f"Lulu cover-dimensions unavailable ({e}); using legacy spine estimate.")
        total_w, total_h, spine_width = _legacy_cover_size_inches(page_count)
        meta = {"source": "legacy_fallback", "error": str(e)}
        return total_w, total_h, spine_width, meta


def lambda_handler(event, context):
    print(f"GenerateCover received event: {json.dumps(event, indent=2)}")
    payload = event.get("Payload", event)
    
    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    birth_data = payload.get("birth_data")
    page_count = payload.get("page_count")
    language = payload.get("language", "English")

    if not all([order_id, line_item_id, birth_data, page_count]):
        raise ValueError(f"Missing required fields: {order_id}, {line_item_id}, {birth_data}, {page_count}")

    page_count = int(page_count)

    try:
        DPI = 300
        COVER_WIDTH = 5.895
        FLAP_WIDTH = 3.5
        BLEED = 0.125

        total_w_inch, total_h_inch, spine_width, layout_meta = resolve_cover_layout_inches(
            page_count, LULU_POD_PACKAGE_ID
        )
        print(f"Cover layout: {json.dumps(layout_meta, indent=2)}")
        print(f"Spine width (in): {spine_width:.4f}; total spread (in): {total_w_inch:.4f} x {total_h_inch:.4f}")

        w_px = int(round(total_w_inch * DPI))
        h_px = int(round(total_h_inch * DPI))

        canvas = Image.new("RGB", (w_px, h_px), "black")
        draw = ImageDraw.Draw(canvas)
        font = load_font_for_language(language, 64)

        front_x = int(round((BLEED + FLAP_WIDTH + COVER_WIDTH + spine_width) * DPI))
        front_y = int(round(BLEED * DPI))
        front_w = int(round(COVER_WIDTH * DPI))
        front_h = int(round((total_h_inch - 2 * BLEED) * DPI))

        cover_text = resolve_cover_text(language)
        draw_centered_text(
            draw,
            cover_text,
            font,
            (front_x, front_y, front_w, front_h),
            wrap_mode=resolve_wrap_mode(language),
        )

        final_buffer = io.BytesIO()
        canvas.save(final_buffer, format="PDF", resolution=DPI)
        final_buffer.seek(0)

        key = f"book-covers/{order_id}/{line_item_id}_dust_jacket_final.pdf"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=key,
            Body=final_buffer,
            ContentType="application/pdf",
        )

        front_cover = canvas.crop((front_x, front_y, front_x + front_w, front_y + front_h))
        front_cover = ImageOps.fit(front_cover, (1024, 1792), Image.Resampling.LANCZOS)
        front_cover_buffer = io.BytesIO()
        front_cover.save(front_cover_buffer, format="JPEG", quality=95)
        front_cover_buffer.seek(0)

        front_key = f"book-covers/{order_id}/{line_item_id}_front_cover.jpg"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=front_key,
            Body=front_cover_buffer,
            ContentType="image/jpeg",
        )

        payload["cover_image_s3_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{key}"
        payload["hardcover_front_image_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{front_key}"
        return payload

    except Exception as e:
        print(f"ERROR: {e}")
        raise
