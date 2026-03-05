import boto3
import json
import os
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io

s3_client = boto3.client('s3')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')

PROTECTIVE_COVER_TEXT = {
    "english": "Remove Protective Cover Before Reading",
    "spanish": "Retire la cubierta protectora antes de leer",
    "french": "Retirez la couverture de protection avant de lire",
    "german": "Schutzumschlag vor dem Lesen entfernen",
    "italian": "Rimuovere la copertina protettiva prima di leggere",
    "portuguese": "Remova a capa protetora antes de ler",
    "japanese": "読む前に保護カバーを外してください",
    "hindi": "पढ़ने से पहले सुरक्षात्मक कवर हटाएं",
    "chinese": "阅读前请取下保护封套",
    "korean": "읽기 전에 보호 커버를 제거하세요",
    "russian": "Снимите защитную обложку перед чтением"
}

def resolve_cover_text(language: str) -> str:
    return PROTECTIVE_COVER_TEXT.get((language or "English").strip().lower(), PROTECTIVE_COVER_TEXT["english"])

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int):
    words = text.split()
    if not words:
        return [text]

    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

def draw_centered_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, box: tuple[int, int, int, int]):
    x, y, w, h = box
    lines = wrap_text(draw, text, font, max(1, int(w * 0.9)))
    bbox_sample = font.getbbox("Aj")
    line_height = (bbox_sample[3] - bbox_sample[1]) + 18
    total_height = len(lines) * line_height
    current_y = y + (h - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x + (w - text_w) // 2, current_y), line, font=font, fill=(255, 255, 255))
        current_y += line_height

def lambda_handler(event, context):
    print(f"GenerateCover received event: {json.dumps(event, indent=2)}")
    payload = event.get('Payload', event)

    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    birth_data = payload.get('birth_data')
    page_count = payload.get('page_count')
    language = payload.get('language', 'English')

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

        TOTAL_W_INCH = (BLEED*2) + (FLAP_WIDTH*2) + (COVER_WIDTH*2) + spine_width
        TOTAL_H_INCH = (BLEED*2) + COVER_HEIGHT
        
        w_px = int(TOTAL_W_INCH * DPI)
        h_px = int(TOTAL_H_INCH * DPI)
        
        canvas = Image.new('RGB', (w_px, h_px), 'black')
        
        draw = ImageDraw.Draw(canvas)
        try:
            font_path = os.path.join(os.environ.get('LAMBDA_TASK_ROOT', ''), 'fonts', 'LibreBaskerville-Regular.ttf')
            if not os.path.exists(font_path): font_path = "fonts/LibreBaskerville-Regular.ttf"
            font = ImageFont.truetype(font_path, 64)
        except:
            font = ImageFont.load_default()

        front_x = int((BLEED + FLAP_WIDTH + COVER_WIDTH + spine_width) * DPI)
        front_y = int(BLEED * DPI)
        front_w = int(COVER_WIDTH * DPI)
        front_h = int(COVER_HEIGHT * DPI)

        cover_text = resolve_cover_text(language)
        draw_centered_text(draw, cover_text, font, (front_x, front_y, front_w, front_h))
        
        final_buffer = io.BytesIO()
        canvas.save(final_buffer, format='PDF', resolution=DPI)
        final_buffer.seek(0)
        
        key = f"book-covers/{order_id}/{line_item_id}_dust_jacket_final.pdf"
        s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=final_buffer, ContentType='application/pdf')

        front_cover = canvas.crop((front_x, front_y, front_x + front_w, front_y + front_h))
        front_cover = ImageOps.fit(front_cover, (1024, 1792), Image.Resampling.LANCZOS)
        front_cover_buffer = io.BytesIO()
        front_cover.save(front_cover_buffer, format='JPEG', quality=95)
        front_cover_buffer.seek(0)

        front_key = f"book-covers/{order_id}/{line_item_id}_front_cover.jpg"
        s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=front_key, Body=front_cover_buffer, ContentType='image/jpeg')
        
        payload["cover_image_s3_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{key}"
        payload["hardcover_front_image_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{front_key}"
        return payload

    except Exception as e:
        print(f"ERROR: {e}")
        raise
