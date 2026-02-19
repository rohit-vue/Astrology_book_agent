# src/generate_pdf/app.py

import boto3
import json
import os
from book_pdf_exporter import save_book_as_pdf
from urllib.parse import urlparse

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def lambda_handler(event, context):
    print(f"Received raw event: {json.dumps(event, indent=2)}")
    payload = event.get('Payload', event)
    
    openai_api_key = None
    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        openai_api_key = json.loads(secret_payload['SecretString']).get('OpenAIKey')
    except Exception as e:
        print(f"Could not retrieve OpenAI key, translation will be skipped. Error: {e}")

    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    chapters_data = payload.get('chapters_data')
    full_book_structure = payload.get('full_book_structure')
    language = payload.get('language', 'English')
    birth_data = payload.get('birth_data', {})
    
    sections = payload.get('generated_sections', {})
    preface_text = sections.get("preface")
    prologue_text = sections.get("prologue")
    epilogue_text = sections.get("epilogue")
    if not preface_text: preface_text = full_book_structure.get("preface_text")
    if not prologue_text: prologue_text = full_book_structure.get("prologue_text")
    if not epilogue_text: epilogue_text = full_book_structure.get("epilogue_text")

    if not all([order_id, line_item_id, chapters_data, full_book_structure]):
        raise ValueError("Missing critical data for PDF generation.")

    local_tmp_dir = f"/tmp/{order_id}/{line_item_id}"
    os.makedirs(local_tmp_dir, exist_ok=True)

    try:
        metadata = full_book_structure.get("metadata", {})
        if not metadata: metadata = full_book_structure.get("book_metadata", {})
        book_title = metadata.get("title", "The Architecture of You")

        book_data = {
            "metadata": metadata,
            "birth_data": birth_data,
            "preface_text": preface_text,
            "prologue_text": prologue_text,
            "epilogue_text": epilogue_text,
            "chapters": []
        }
        
        for idx, chapter in enumerate(chapters_data, start=1):
            text_s3_path = chapter.get('chapter_text_s3_path')
            if not text_s3_path: continue

            bucket, key = parse_s3_path(text_s3_path)
            s3_object = s3_client.get_object(Bucket=bucket, Key=key)
            chapter_json_content = json.loads(s3_object['Body'].read().decode('utf-8'))
            
            book_data["chapters"].append({
                "heading": chapter.get("chapter_title", f"Chapter {idx}"),
                "content": chapter_json_content.get('chapter_text', ''),
                "image_path": chapter.get('image_url')
            })

        local_pdf_filename = f"{line_item_id}.pdf"
        
        output_pdf_path, page_count = save_book_as_pdf(
            title=book_title, 
            book_data=book_data,
            filename=local_pdf_filename,
            output_dir=local_tmp_dir,
            language=language,
            openai_api_key=openai_api_key
        )

        final_pdf_s3_key = f"final-pdfs/{order_id}/{line_item_id}.pdf"
        s3_client.upload_file(output_pdf_path, ARTIFACTS_BUCKET, final_pdf_s3_key, ExtraArgs={"ContentType": "application/pdf"})
        final_s3_path = f"s3://{ARTIFACTS_BUCKET}/{final_pdf_s3_key}"
        
        payload["final_pdf_s3_path"] = final_s3_path
        payload["page_count"] = page_count
        return payload

    except Exception as e:
        print(f"ERROR: PDF generation failed: {e}")
        raise e