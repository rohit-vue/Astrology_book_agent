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
MODEL_TEXT = "gpt-5.2-2025-12-11" 
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
        sum_resp = await openai_client.chat.completions.create(model=MODEL_TEXT, messages=[{"role": "user", "content": img_sum_prompt}], max_completion_tokens=100)
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
        print("SSM Image Prompt not found, using default.")
        p_image = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic. NO text."

    style_prompt = f"Describe the ideal writing tone for a book about {focus} in {language} based on this chart."
    try:
        style_res = await openai_client.chat.completions.create(model=MODEL_TEXT, messages=[{"role": "user", "content": style_prompt}], max_completion_tokens=100)
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

    master_foreword = """Thank you. Thank you for taking the step. Thank you for opening a book that is not quite like any other book you have held in your hands. A self help book created with the help of artificial intelligence can feel strange. It can feel like a strange kind of mirror. It can feel like a strange kind of promise. It can feel like something between a tool and a prophecy. It may make you wonder if the human heart can truly be guided by something made of code. It may make you wonder if the answers you need can be generated. It may make you wonder if your pain can be understood by something that does not feel pain.

    And that is a valid concern.

    We are living in an age of profound disconnect. We are living in a time when loneliness has become an epidemic, when people feel isolated even in crowded rooms, when social media gives us the illusion of connection while quietly starving us of the real thing. We are living in a time when we perform our lives more than we live them. And in this noise, it is easy to lose the signal of who we actually are.

    But here is the truth about this book: The intelligence may be artificial, but the data is yours. The patterns are yours. The story is yours.

    Astrology is an ancient language, a way of mapping the invisible currents that shape a life. For centuries, it has been used to help people understand their nature, their cycles, and their potential. What we have done here is use modern technology to translate that ancient language into a narrative that speaks directly to you.

    Think of this AI not as an author, but as a translator. It is taking the complex, mathematical snapshot of the sky at the moment you were born and translating it into words, sentences, and chapters. It is synthesizing vast amounts of astrological wisdom to find the specific threads that weave together to form the tapestry of your personality.

    It is not magic. It is a reflection.

    As you read these pages, take what resonates and leave what does not. Use this book as a starting point for your own inquiry. Let it provoke you, comfort you, challenge you, and validate you. Let it be a conversation starter between you and your own soul.

    You are the only expert on your own life. This book is simply a guide, a map generated from the stars to help you navigate the terrain of your own becoming.

    Welcome to your Blueprint."""

    final_foreword_text = master_foreword
    if language.lower() != "english":
        print(f"Translating Foreword to {language}...")
        trans_prompt = f"Translate the following text into {language}. Maintain the poetic, warm, and serious tone. Do not add commentary.\n\nTEXT:\n{master_foreword}"
        try:
            trans_resp = await openai_client.chat.completions.create(
                model=MODEL_STABLE,
                messages=[{"role": "user", "content": trans_prompt}],
                temperature=0.3
            )
            final_foreword_text = trans_resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"Translation failed: {e}")
            final_foreword_text = master_foreword

    print(f"Generating Preface with desc: {preface_desc[:50]}...")
    preface_text = await generate_section("Preface", preface_desc, style, language, chart)
    
    print(f"Generating Prologue with desc: {prologue_desc[:50]}...")
    prologue_text = await generate_section("Prologue", prologue_desc, style, language, chart)
    
    final_output['generated_sections'] = { 'preface': preface_text, 'prologue': prologue_text,'foreword': final_foreword_text }
    final_output['full_book_structure']['preface_text'] = preface_text
    final_output['full_book_structure']['prologue_text'] = prologue_text
    
    tasks = []
    chapters_list = structure.get('chapters') or structure.get('structure', {}).get('chapters', [])
    word_count_per_chapter = 10000 
    
    for idx, ch in enumerate(chapters_list):
        tasks.append(write_chapter(idx+1, ch, chart, word_count_per_chapter, focus, style, language, order_id, line_item_id, p_image))
    
    results = await asyncio.gather(*tasks)
    
    successful = [r for r in results if r is not None]
    final_output['chapters_data'] = sorted(successful, key=lambda x: x['chapter_index'])

    if not final_output['chapters_data']:
        raise ValueError("All chapter writing tasks failed.")

    print(f"Generating Epilogue with desc: {epilogue_desc[:50]}...")
    epilogue_text = await generate_section("Epilogue", epilogue_desc, style, language, chart)
    
    final_output['generated_sections']['epilogue'] = epilogue_text
    final_output['full_book_structure']['epilogue_text'] = epilogue_text

    if 'metadata' not in final_output['full_book_structure']:
        final_output['full_book_structure']['metadata'] = structure.get('metadata', structure.get('book_metadata', {}))

    return final_output

def lambda_handler(event, context):
    return asyncio.run(async_lambda_handler(event, context))