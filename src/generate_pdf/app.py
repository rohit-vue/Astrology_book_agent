import boto3
import os
import json
from book_pdf_exporter import save_book_as_pdf
from urllib.parse import urlparse
import requests

s3_client = boto3.client('s3')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')

def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def lambda_handler(event, context):
    print(f"Received raw event from Step Functions: {json.dumps(event, indent=2)}")

    # --- START OF THE FINAL, CORRECT FIX ---
    # The previous step's output is a dictionary wrapped in a "Payload" key.
    if 'Payload' in event and isinstance(event['Payload'], dict):
        print("Detected nested 'Payload'. Unwrapping...")
        payload = event['Payload']
    else:
        # This handles cases where the event is passed directly (e.g., testing)
        payload = event
    
    print(f"Using final processed payload: {json.dumps(payload, indent=2)}")
    # --- END OF THE FINAL, CORRECT FIX ---

    order_id = payload.get('order_id')
    chapters_data = payload.get('chapters_data')
    line_item_id = payload.get('line_item_id')

    if not all([order_id, line_item_id, chapters_data]):
        raise ValueError(f"Missing 'order_id', 'line_item_id', or 'chapters_data' in the processed payload. Original Event: {json.dumps(event)}")

    local_tmp_dir = f"/tmp/{order_id}/{line_item_id}"
    os.makedirs(local_tmp_dir, exist_ok=True)

    book_data = {"chapters": []}

    try:
        print(f"--- Downloading assets for order: {order_id}, line item: {line_item_id} ---")
        for idx, chapter in enumerate(chapters_data, start=1):
            text_s3_path = chapter.get('chapter_text_s3_path')
            if not text_s3_path:
                print(f"Warning: chapter_text_s3_path missing for chapter index {idx}, skipping.")
                continue

            bucket, key = parse_s3_path(text_s3_path)
            s3_object = s3_client.get_object(Bucket=bucket, Key=key)
            chapter_json_content = json.loads(s3_object['Body'].read().decode('utf-8'))
            full_chapter_text = chapter_json_content.get('chapter_text', '')

            image_url = chapter.get('image_url')
            local_image_path = None
            if image_url:
                try:
                    image_filename = f"chapter_{idx}_image.png"
                    local_image_path = os.path.join(local_tmp_dir, image_filename)
                    response = requests.get(image_url, stream=True)
                    response.raise_for_status()
                    with open(local_image_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"Successfully downloaded image for chapter {idx}")
                except Exception as e:
                    print(f"Warning: failed to download image for chapter {idx}. Error: {e}")
                    local_image_path = None

            book_data["chapters"].append({
                "heading": chapter.get("chapter_title", f"Chapter {idx}"),
                "content": full_chapter_text,
                "image_path": local_image_path
            })

        print("--- All assets prepared, generating PDF ---")
        book_title = "The Architecture of You"

        # --- START OF THE FIX ---
        # Your exporter is designed to work from the current directory.
        # We need to change into our temporary directory before calling it.
        original_working_dir = os.getcwd()
        os.chdir(local_tmp_dir)

        # Call the exporter without the `output_dir` argument.
        # It will save the file inside the current directory (`local_tmp_dir`).
        local_pdf_filename = f"{line_item_id}.pdf"
        save_book_as_pdf(
            title=book_title, 
            book_data=book_data,
            filename=local_pdf_filename
        )
        
        # The full path to the generated PDF
        output_pdf_path = os.path.join(local_tmp_dir, "generated_books", local_pdf_filename)

        # Change back to the original directory
        os.chdir(original_working_dir)
        # --- END OF THE FIX ---

        print(f"--- PDF generated locally at: {output_pdf_path} ---")

        final_pdf_s3_key = f"final-pdfs/{order_id}/{line_item_id}.pdf"
        s3_client.upload_file(
            output_pdf_path, ARTIFACTS_BUCKET, final_pdf_s3_key,
            ExtraArgs={"ContentType": "application/pdf"}
        )
        final_s3_path = f"s3://{ARTIFACTS_BUCKET}/{final_pdf_s3_key}"
        print(f"--- Successfully uploaded final PDF to {final_s3_path} ---")

        payload["final_pdf_s3_path"] = final_s3_path
        return payload

    except Exception as e:
        print(f"ERROR: PDF generation failed for order {order_id}: {e}")
        raise e