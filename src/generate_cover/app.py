import boto3
import json
import os
import io
from PIL import Image, ImageDraw, ImageFont, ImageOps

s3_client = boto3.client("s3")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET")

PROTECTIVE_COVER_TEXT = {
    "english": "Remove Protective Cover Before Reading",
    "spanish": "Retire la cubierta protectora antes de leer",
    "french": "Retirez la couverture de protection avant de lire",
    "german": "Schutzumschlag vor dem Lesen entfernen",
    "italian": "Rimuovere la copertina protettiva prima di leggere",
    "portuguese": "Remova a capa protetora antes de ler",
    "japanese": "\u8aad\u3080\u524d\u306b\u4fdd\u8b77\u30ab\u30d0\u30fc\u3092\u53d6\u308a\u5916\u3057\u3066\u304f\u3060\u3055\u3044",
    "hindi": "\u092a\u095d\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 \u0938\u0941\u0930\u0915\u094d\u0937\u093e \u0915\u0935\u0930 \u0939\u091f\u093e\u090f\u0901",
    "chinese": "\u9605\u8bfb\u524d\u8bf7\u53d6\u4e0b\u4fdd\u62a4\u5c01\u5957",
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


def load_font_for_language(language: str, size: int):
    lambda_fonts_dir = os.path.join(os.environ.get("LAMBDA_TASK_ROOT", ""), "fonts")
    local_fonts_dir = "fonts"
    fallback_dirs = [
        lambda_fonts_dir,
        local_fonts_dir,
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


def lambda_handler(event, context):
    print(f"GenerateCover received event: {json.dumps(event, indent=2)}")
    payload = event.get("Payload", event)

    order_id = payload.get("order_id")
    line_item_id = payload.get("line_item_id")
    birth_data = payload.get("birth_data")
    page_count = payload.get("page_count")
    language = payload.get("language", "English")

    if not all([order_id, line_item_id, birth_data, page_count]):
        raise ValueError("Missing required fields.")

    try:
        DPI = 300
        PAGE_MULTIPLIER = 0.002252
        COVER_WIDTH = 5.895
        COVER_HEIGHT = 9.0
        FLAP_WIDTH = 3.5
        BLEED = 0.125

        spine_width = page_count * PAGE_MULTIPLIER
        print(f"Spine Width: {spine_width:.3f} inches")

        total_w_inch = (BLEED * 2) + (FLAP_WIDTH * 2) + (COVER_WIDTH * 2) + spine_width
        total_h_inch = (BLEED * 2) + COVER_HEIGHT

        w_px = int(total_w_inch * DPI)
        h_px = int(total_h_inch * DPI)

        canvas = Image.new("RGB", (w_px, h_px), "black")
        draw = ImageDraw.Draw(canvas)
        font = load_font_for_language(language, 64)

        front_x = int((BLEED + FLAP_WIDTH + COVER_WIDTH + spine_width) * DPI)
        front_y = int(BLEED * DPI)
        front_w = int(COVER_WIDTH * DPI)
        front_h = int(COVER_HEIGHT * DPI)

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
