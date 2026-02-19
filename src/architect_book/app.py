import boto3
import json
import os
from openai import OpenAI
from urllib.parse import urlparse

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
ssm_client = boto3.client('ssm') 

API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')
openai_client = OpenAI(api_key="dummy") 

MODEL_ID = "gpt-5.2"

def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def get_prompts_from_ssm(natal_chart_json: dict, focus: str, language: str, qanda: str) -> (str, str):
    """
    Fetches prompts from SSM and injects variables (Focus, Language, Data, Q&A).
    """
    try:
        sys_param = ssm_client.get_parameter(Name="/AstrologyBookFactory/prompts/architect/system",WithDecryption=True)
        user_param = ssm_client.get_parameter(Name="/AstrologyBookFactory/prompts/architect/user",WithDecryption=True)
        
        system_template = sys_param['Parameter']['Value']
        user_template = user_param['Parameter']['Value']
    except Exception as e:
        print(f"Error fetching prompts from SSM: {e}")
        raise e

    system_prompt = system_template.replace("__LANGUAGE__", language)
    
    user_prompt = user_template.replace("__FOCUS__", focus)
    user_prompt = user_prompt.replace("__LANGUAGE__", language)
    user_prompt = user_prompt.replace("__ASTROLOGY_DATA__", json.dumps(natal_chart_json, indent=2))
    safe_qanda = qanda[:15000] if qanda else "No Q&A provided."
    user_prompt = user_prompt.replace("__QANDA__", safe_qanda)

    return system_prompt, user_prompt

def lambda_handler(event, context):
    print(f"ArchitectBook received event: {json.dumps(event)}")
    payload = event.get('Payload', event)

    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    astrology_s3_path = payload.get('astrology_json_s3_path')
    focus = payload.get('focus', 'Personality')
    language = payload.get('language', 'English') 
    
    qanda_content = payload.get('qanda_content', {}).get('qanda_content', '')

    if not all([order_id, line_item_id, astrology_s3_path]):
        raise ValueError("Missing required fields.")

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        openai_client.api_key = json.loads(secret_payload['SecretString']).get('OpenAIKey')

        bucket, key = parse_s3_path(astrology_s3_path)
        s3_object = s3_client.get_object(Bucket=bucket, Key=key)
        astrology_data = json.loads(s3_object['Body'].read().decode('utf-8'))

        system_prompt, user_prompt = get_prompts_from_ssm(astrology_data, focus, language, qanda_content)
        
        print("Calling OpenAI to architect the book structure...")
        response = openai_client.chat.completions.create(
            model=MODEL_ID, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )


        full_structure = json.loads(response.choices[0].message.content)
        print(f"Generated structure with {len(full_structure.get('structure', {}).get('chapters', []))} chapters.")

        output_key = f"book-structures/{order_id}/{line_item_id}.json"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET, Key=output_key,
            Body=json.dumps(full_structure, indent=2), ContentType='application/json'
        )
        
        payload['book_structure_s3_path'] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return payload

    except Exception as e:
        print(f"ERROR: {e}")
        raise e