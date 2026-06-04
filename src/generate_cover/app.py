# FILE: src/generate_cover/app.py

import boto3
import json
import os
import io
from typing import Tuple
import requests
from PIL import Image, ImageDraw, ImageFont

_aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
s3_client = boto3.client("s3", region_name=_aws_region)
secrets_manager = boto3.client("secretsmanager", region_name=_aws_region)

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")
API_KEYS_SECRET_ARN = os.environ.get("API_KEYS_SECRET_ARN")
LULU_API_BASE = os.environ.get("LULU_API_BASE", "https://api.lulu.com").rstrip("/")
LULU_POD_PACKAGE_ID = os.environ.get("LULU_POD_PACKAGE_ID", "0550X0850.BW.STD.LW.060UC444.MNG")
TEXT_WRAP_WIDTH_RATIO = 0.85
FRONT_COVER_PREVIEW_SIZE = (1024, 1792)


def resolve_language_key(language: str) -> str:
    return (language or "English").strip().lower()


def resolve_wrap_mode(language: str) -> str:
    if resolve_language_key(language) in {"chinese", "japanese"}:
        return "char"
    return "word"


def _text_line_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_char_chunks(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if current and _text_line_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _wrap_word_chunks(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_line_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            if _text_line_width(draw, current, font) <= max_width:
                lines.append(current)
            else:
                lines.extend(_wrap_char_chunks(draw, current, font, max_width))
            current = word

    if _text_line_width(draw, current, font) <= max_width:
        lines.append(current)
    else:
        lines.extend(_wrap_char_chunks(draw, current, font, max_width))
    return lines


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    wrap_mode: str = "word",
) -> list[str]:
    if not text:
        return []

    if "\n" in text:
        lines: list[str] = []
        for segment in text.split("\n"):
            if segment.strip():
                lines.extend(wrap_text(draw, segment.strip(), font, max_width, wrap_mode=wrap_mode))
        return lines

    if wrap_mode == "char":
        return _wrap_char_chunks(draw, text, font, max_width)
    return _wrap_word_chunks(draw, text, font, max_width)


def resize_front_cover_preview(
    image: Image.Image,
    target_size: tuple[int, int] = FRONT_COVER_PREVIEW_SIZE,
    background: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Scale to fit inside target_size without cropping (letterbox on black)."""
    tw, th = target_size
    fitted = image.copy()
    fitted.thumbnail((tw, th), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (tw, th), background)
    paste_x = (tw - fitted.width) // 2
    paste_y = (th - fitted.height) // 2
    canvas.paste(fitted, (paste_x, paste_y))
    return canvas


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
    wrap_mode: str = "word",
):
    x, y, w, h = box
    lines = wrap_text(draw, text, font, max(1, int(w * TEXT_WRAP_WIDTH_RATIO)), wrap_mode=wrap_mode)
    bbox_sample = font.getbbox("Aj")
    line_height = (bbox_sample[3] - bbox_sample[1]) + 18
    total_height = len(lines) * line_height
    current_y = y + (h - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x + (w - text_w) // 2, current_y), line, font=font, fill=(255, 255, 255))
        current_y += line_height


def draw_centered_stacked_rows(
    draw: ImageDraw.ImageDraw,
    rows: list[tuple[str, int]],
    box: tuple[int, int, int, int],
    language: str,
    row_gap: int = 28,
):
    """Draw multiple centered rows (text, font_size) inside box, largest row first."""
    x, y, w, h = box
    wrap_mode = resolve_wrap_mode(language)
    usable = [(text.strip(), size) for text, size in rows if text and str(text).strip()]
    if not usable:
        return

    prepared: list[tuple[list[str], ImageFont.FreeTypeFont, int]] = []
    for text, size in usable:
        font = load_font_for_language(language, size)
        lines = wrap_text(draw, text, font, max(1, int(w * TEXT_WRAP_WIDTH_RATIO)), wrap_mode=wrap_mode)
        lh = (font.getbbox("Aj")[3] - font.getbbox("Aj")[1]) + 14
        prepared.append((lines, font, lh))

    total_height = sum(lh * len(lines) for lines, _, lh in prepared)
    total_height += row_gap * max(0, len(prepared) - 1)
    current_y = y + (h - total_height) // 2

    for block_idx, (lines, font, lh) in enumerate(prepared):
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            draw.text((x + (w - text_w) // 2, current_y), line, font=font, fill=(255, 255, 255))
            current_y += lh
        if block_idx < len(prepared) - 1:
            current_y += row_gap


def resolve_book_title(payload: dict) -> str:
    structure = payload.get("full_book_structure") or {}
    meta = structure.get("metadata") or structure.get("book_metadata") or {}
    return (
        (meta.get("title") or payload.get("book_title") or payload.get("cover_title") or "The Luminary Blueprint")
        .strip()
    )


def resolve_focus_line(payload: dict) -> str:
    return (payload.get("focus") or "Personality").strip()


def format_birth_details_text(birth_data: dict) -> str:
    """Front cover row 3: YYYY-MM-DD HH:MM from birth_data."""
    if not birth_data:
        return ""

    year = birth_data.get("year", 2000)
    month = birth_data.get("month", 1)
    day = birth_data.get("day", 1)
    hour = birth_data.get("hour", 0)
    minute = birth_data.get("min", birth_data.get("minute", 0))
    return f"{year}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}"


def build_front_cover_rows(payload: dict) -> list[tuple[str, int]]:
    language = payload.get("language", "English")
    birth_data = payload.get("birth_data") or {}

    title = resolve_book_title(payload)
    focus = resolve_focus_line(payload)
    birth_block = format_birth_details_text(birth_data)

    lang_key = resolve_language_key(language)
    if lang_key in {"chinese", "japanese", "korean"}:
        return [
            (title, 54),
            (focus, 44),
            (birth_block, 38),
        ]
    return [
        (title, 54),
        (focus, 44),
        (birth_block, 38),
    ]


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


def _get_lulu_api_keys() -> tuple[str | None, str | None]:
    key = os.environ.get("LULU_CLIENT_KEY") or os.environ.get("LuluApiClientKey")
    secret = os.environ.get("LULU_CLIENT_SECRET") or os.environ.get("LuluApiClientSecret")
    if key and secret:
        return key, secret
    if not API_KEYS_SECRET_ARN:
        return None, None
    secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    secrets = json.loads(secret_payload["SecretString"])
    return secrets.get("LuluApiClientKey"), secrets.get("LuluApiClientSecret")


def resolve_cover_layout_inches(page_count: int, pod_package_id: str) -> Tuple[float, float, float]:
    COVER_WIDTH = 5.895
    FLAP_WIDTH = 3.5
    BLEED = 0.125
    fixed_band = (BLEED * 2) + (FLAP_WIDTH * 2) + (COVER_WIDTH * 2)

    if os.environ.get("USE_LEGACY_COVER_LAYOUT", "").strip().lower() in ("1", "true", "yes"):
        return _legacy_cover_size_inches(page_count)

    try:
        client_key, client_secret = _get_lulu_api_keys()
        if not client_key or not client_secret:
            raise ValueError("Lulu API credentials not configured")
        token = get_lulu_token(client_key, client_secret)
        total_w, total_h = fetch_lulu_cover_dimensions_inches(token, pod_package_id, page_count)
        spine_width = total_w - fixed_band
        if spine_width <= 0:
            raise ValueError(f"Invalid spine width from Lulu dimensions: {total_w}")
        print(f"Lulu cover dimensions: {total_w:.4f}\" x {total_h:.4f}\", spine {spine_width:.4f}\"")
        return total_w, total_h, spine_width
    except Exception as e:
        print(f"Lulu cover-dimensions unavailable ({e}); using legacy spine estimate.")
        return _legacy_cover_size_inches(page_count)


def render_cover_canvas(payload: dict) -> tuple[Image.Image, dict]:
    """Build full dust-jacket image plus front-panel crop box (x, y, w, h) in pixels."""
    page_count = int(payload["page_count"])
    language = payload.get("language", "English")

    DPI = 300
    COVER_WIDTH = 5.895
    FLAP_WIDTH = 3.5
    BLEED = 0.125

    total_w_inch, total_h_inch, spine_width = resolve_cover_layout_inches(
        page_count, LULU_POD_PACKAGE_ID
    )

    w_px = int(round(total_w_inch * DPI))
    h_px = int(round(total_h_inch * DPI))
    canvas = Image.new("RGB", (w_px, h_px), "black")
    draw = ImageDraw.Draw(canvas)

    front_x = int(round((BLEED + FLAP_WIDTH + COVER_WIDTH + spine_width) * DPI))
    front_y = int(round(BLEED * DPI))
    front_w = int(round(COVER_WIDTH * DPI))
    front_h = int(round((total_h_inch - 2 * BLEED) * DPI))

    draw_centered_stacked_rows(
        draw,
        build_front_cover_rows(payload),
        (front_x, front_y, front_w, front_h),
        language,
    )

    front_box = {"x": front_x, "y": front_y, "w": front_w, "h": front_h, "dpi": DPI}
    return canvas, front_box


def render_cover_pdf_bytes(payload: dict) -> bytes:
    canvas, front_box = render_cover_canvas(payload)
    final_buffer = io.BytesIO()
    canvas.save(final_buffer, format="PDF", resolution=front_box["dpi"])
    final_buffer.seek(0)
    return final_buffer.read()


def generate_cover_artifact(
    payload: dict,
    *,
    output_dir: str | None = None,
    upload_s3: bool = True,
) -> dict:
    """Render cover PDF; optionally save locally and/or upload to S3."""
    order_id = payload["order_id"]
    line_item_id = payload["line_item_id"]

    canvas, front_box = render_cover_canvas(payload)
    dpi = front_box["dpi"]
    pdf_buffer = io.BytesIO()
    canvas.save(pdf_buffer, format="PDF", resolution=dpi)
    pdf_bytes = pdf_buffer.getvalue()
    payload = dict(payload)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        local_pdf = os.path.join(output_dir, f"{line_item_id}_dust_jacket_final.pdf")
        with open(local_pdf, "wb") as f:
            f.write(pdf_bytes)
        payload["local_cover_pdf_path"] = local_pdf
        print(f"Saved local cover PDF -> {local_pdf}")

        fx, fy, fw, fh = front_box["x"], front_box["y"], front_box["w"], front_box["h"]
        front_cover = canvas.crop((fx, fy, fx + fw, fy + fh))
        front_cover = resize_front_cover_preview(front_cover)
        local_jpg = os.path.join(output_dir, f"{line_item_id}_front_cover.jpg")
        front_cover.save(local_jpg, format="JPEG", quality=95)
        payload["local_front_cover_jpg"] = local_jpg
        print(f"Saved local front cover preview -> {local_jpg}")

    if upload_s3 and ARTIFACTS_BUCKET:
        key = f"book-covers/{order_id}/{line_item_id}_dust_jacket_final.pdf"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
        payload["cover_image_s3_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{key}"

        fx, fy, fw, fh = front_box["x"], front_box["y"], front_box["w"], front_box["h"]
        front_cover = canvas.crop((fx, fy, fx + fw, fy + fh))
        front_cover = resize_front_cover_preview(front_cover)
        front_buffer = io.BytesIO()
        front_cover.save(front_buffer, format="JPEG", quality=95)
        front_buffer.seek(0)
        front_key = f"book-covers/{order_id}/{line_item_id}_front_cover.jpg"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=front_key,
            Body=front_buffer,
            ContentType="image/jpeg",
        )
        payload["hardcover_front_image_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{front_key}"

    return payload


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

    payload["page_count"] = int(page_count)
    payload.setdefault("language", language)
    payload.setdefault("birth_data", birth_data)

    try:
        return generate_cover_artifact(payload, upload_s3=True)
    except Exception as e:
        print(f"ERROR: {e}")
        raise
