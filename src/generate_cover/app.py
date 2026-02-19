import boto3
import json
import os
import requests
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
import textwrap

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
openai_client = OpenAI(api_key="dummy")

def build_cover_prompt(birth_data: dict) -> str:
    lat = birth_data.get('lat', '0.0'); lon = birth_data.get('lon', '0.0')
    year = birth_data.get('year', 2000); month = birth_data.get('month', 1); day = birth_data.get('day', 1)
    return f"A beautiful, abstract cosmic vista representing the night sky over {lat}, {lon} on {year}-{month}-{day}. Style: ethereal, swirling cosmic dust, intricate fractal patterns, glowing filaments. Rich, vibrant color palette. CRITICAL: NO text, borders, or figures."

def lambda_handler(event, context):
    print(f"GenerateCover received event: {json.dumps(event, indent=2)}")
    payload = event.get('Payload', event)

    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    birth_data = payload.get('birth_data')
    page_count = payload.get('page_count')
    
    full_structure = payload.get('full_book_structure', {})
    metadata = full_structure.get('metadata', {})
    if not metadata: metadata = full_structure.get('book_metadata', {})
    
    book_title = metadata.get('title')
    if not book_title:
        book_title = payload.get('cover_title', 'A Personal Journey')

    if not all([order_id, line_item_id, birth_data, page_count]):
        raise ValueError("Missing required fields.")

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        openai_client.api_key = json.loads(secret_payload['SecretString']).get('OpenAIKey')
        
        prompt = build_cover_prompt(birth_data)
        image_response = openai_client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1792", quality="hd", n=1)
        image_data = requests.get(image_response.data[0].url, timeout=60).content
        dalle_art = Image.open(io.BytesIO(image_data)).convert('RGB')

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

        ART_TARGET_W_INCH = COVER_WIDTH + BLEED
        ART_TARGET_H_INCH = COVER_HEIGHT + (BLEED * 2)
        
        art_w_px = int(ART_TARGET_W_INCH * DPI)
        art_h_px = int(ART_TARGET_H_INCH * DPI)
        
        front_art = ImageOps.fit(dalle_art, (art_w_px, art_h_px), Image.Resampling.LANCZOS)
        
        paste_x = int((BLEED + FLAP_WIDTH + COVER_WIDTH + spine_width) * DPI)
        paste_y = 0 
        
        canvas.paste(front_art, (paste_x, paste_y))
        
        draw = ImageDraw.Draw(canvas)
        try:
            font_path = os.path.join(os.environ.get('LAMBDA_TASK_ROOT', ''), 'fonts', 'LibreBaskerville-Regular.ttf')
            if not os.path.exists(font_path): font_path = "fonts/LibreBaskerville-Regular.ttf"
            font = ImageFont.truetype(font_path, 80)
        except:
            font = ImageFont.load_default()

        visual_center_x = paste_x + int((COVER_WIDTH * DPI) / 2)
        visual_center_y = int(h_px / 2)

        lines = textwrap.wrap(book_title, width=18) 
        
        bbox_sample = font.getbbox("Aj")
        line_height = (bbox_sample[3] - bbox_sample[1]) + 20 
        total_block_height = len(lines) * line_height
        
        current_y = visual_center_y - (total_block_height / 2)

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            
            draw.text(
                (visual_center_x - text_w/2, current_y), 
                line, 
                font=font, 
                fill=(255, 255, 255)
            )
            current_y += line_height
        
        final_buffer = io.BytesIO()
        canvas.save(final_buffer, format='PDF', resolution=DPI)
        final_buffer.seek(0)
        
        key = f"book-covers/{order_id}/{line_item_id}_dust_jacket_final.pdf"
        s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=final_buffer, ContentType='application/pdf')
        
        payload["cover_image_s3_url"] = f"https://{ARTIFACTS_BUCKET}.s3.amazonaws.com/{key}"
        return payload

    except Exception as e:
        print(f"ERROR: {e}")
        raise