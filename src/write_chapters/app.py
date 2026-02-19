# src/write_chapters/app.py

import boto3
import json
import os
import asyncio
from openai import AsyncOpenAI
from urllib.parse import urlparse

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
ssm_client = boto3.client('ssm') 
openai_client = AsyncOpenAI(api_key="dummy")

API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
MODEL_TEXT = "gpt-4-1106-preview"
MODEL_IMAGE = "dall-e-3"
MODEL_STABLE = "gpt-4o"

def parse_s3_path(s3_path):
    return s3_path.replace("s3://", "").split("/", 1)

async def generate_section(name, description, style, language, chart):
    if not description: return ""
    print(f"Generating {name}...")
    prompt = f"""
    Write the {name} for a personal astrology book.
    Language: {language}
    Style: {style}
    Context: {description}
    Directive: Write in second person ("You"). Start directly. Plain text only.
    """
    try:
        resp = await openai_client.chat.completions.create(
            model=MODEL_STABLE, 
            messages=[{"role": "user", "content": prompt}], 
            temperature=0.7, 
            max_tokens=800 
        )
        text = resp.choices[0].message.content.strip()
        print(f"✅ {name} SUCCESS: {len(text)} chars")
        return text
    except Exception as e:
        print(f"❌ Error generating {name}: {e}")
        return ""

async def write_chapter(idx, details, chart, target, focus, style, language, order_id, line_item_id, image_prompt_template):
    title = details['title']
    print(f"Starting Chapter {idx}: {title}")
    
    prompt = f"""
    Write Chapter {idx}: "{title}".
    **Language:** {language}
    **Style:** {style}
    **Focus:** {focus}
    **Summary:** {details['description']}
    **Word Target:** {target}
    **Formatting:** Plain paragraphs. No bold. No headers.
    **Data:** {json.dumps(chart)}
    """
    
    text_resp = await openai_client.chat.completions.create(model=MODEL_TEXT, messages=[{"role": "user", "content": prompt}], temperature=0.5)
    text = text_resp.choices[0].message.content.strip()
    
    img_sum_prompt = f"Summarize text for image: {text[:1000]}"
    try:
        sum_resp = await openai_client.chat.completions.create(model=MODEL_TEXT, messages=[{"role": "user", "content": img_sum_prompt}], max_tokens=100)
        summary = sum_resp.choices[0].message.content.strip()
        img_prompt = image_prompt_template.replace("__CHAPTER_TITLE__", title).replace("__SUMMARY__", summary)
        img_resp = await openai_client.images.generate(model=MODEL_IMAGE, prompt=img_prompt, size="1024x1792")
        img_url = img_resp.data[0].url
    except Exception as e:
        print(f"Image gen failed: {e}")
        img_url = None

    key = f"chapters-json/{order_id}/{line_item_id}/chapter_{idx}.json"
    s3_client.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=json.dumps({"chapter_title": title, "chapter_text": text}), ContentType="application/json")
    
    return {"chapter_index": idx, "chapter_title": title, "chapter_text_s3_path": f"s3://{ARTIFACTS_BUCKET}/{key}", "image_url": img_url}

async def async_lambda_handler(event, context):
    print(f"WriteChapters received event: {json.dumps(event, indent=2)}")
    payload = event.get('Payload', event)
    
    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    focus = payload.get('focus', 'Personality')
    language = payload.get('language', 'English')
    
    secret = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
    openai_client.api_key = json.loads(secret['SecretString']).get('OpenAIKey')
    
    b_astro, k_astro = parse_s3_path(payload['astrology_json_s3_path'])
    chart = json.loads(s3_client.get_object(Bucket=b_astro, Key=k_astro)['Body'].read().decode('utf-8'))
    
    b_struct, k_struct = parse_s3_path(payload['book_structure_s3_path'])
    structure = json.loads(s3_client.get_object(Bucket=b_struct, Key=k_struct)['Body'].read().decode('utf-8'))

    try:
        p_image = ssm_client.get_parameter(Name="/AstrologyBookFactory/prompts/writer/image",WithDecryption=True)['Parameter']['Value']
    except:
        p_image = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic. NO text."

    style_prompt = f"Describe the ideal writing tone for a book about {focus} in {language} based on this chart."
    try:
        style_res = await openai_client.chat.completions.create(model=MODEL_TEXT, messages=[{"role": "user", "content": style_prompt}], max_tokens=100)
        style = style_res.choices[0].message.content.strip()
    except:
        style = "Warm"

    final_output = payload
    final_output['full_book_structure'] = structure
    
    preface_desc = structure.get('preface_description') or structure.get('structure', {}).get('preface_description')
    if not preface_desc: preface_desc = "Write a warm, welcoming preface setting the stage for a journey of self-discovery based on the user's astrology."

    prologue_desc = structure.get('prologue_description') or structure.get('structure', {}).get('prologue_description')
    if not prologue_desc: prologue_desc = "Write an introduction that explains the core themes of the book and invites the reader to explore their inner world."

    epilogue_desc = structure.get('epilogue_description') or structure.get('structure', {}).get('epilogue_description')
    if not epilogue_desc: epilogue_desc = "Write a concluding chapter that synthesizes the journey, offering encouragement and a call to action for the future."

    preface_text = await generate_section("Preface", preface_desc, style, language, chart)
    prologue_text = await generate_section("Prologue", prologue_desc, style, language, chart)
    
    final_output['generated_sections'] = { 'preface': preface_text, 'prologue': prologue_text }
    final_output['full_book_structure']['preface_text'] = preface_text
    final_output['full_book_structure']['prologue_text'] = prologue_text
    
    tasks = []
    chapters_list = structure.get('chapters') or structure.get('structure', {}).get('chapters', [])
    word_count_per_chapter = 10000
    
    for idx, ch in enumerate(chapters_list):
        tasks.append(write_chapter(idx+1, ch, chart, word_count_per_chapter, focus, style, language, order_id, line_item_id, p_image))
    
    results = await asyncio.gather(*tasks)
    final_output['chapters_data'] = sorted([r for r in results if r], key=lambda x: x['chapter_index'])

    if not final_output['chapters_data']:
        raise ValueError("All chapter writing tasks failed.")

    epilogue_text = await generate_section("Epilogue", epilogue_desc, style, language, chart)
    
    final_output['generated_sections']['epilogue'] = epilogue_text
    final_output['full_book_structure']['epilogue_text'] = epilogue_text

    if 'metadata' not in final_output['full_book_structure']:
        final_output['full_book_structure']['metadata'] = structure.get('metadata', structure.get('book_metadata', {}))

    return final_output

def lambda_handler(event, context):
    return asyncio.run(async_lambda_handler(event, context))