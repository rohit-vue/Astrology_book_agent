# FILE: src/fetch_astrology/app.py (FINAL CORRECTED VERSION)

import boto3
import json
import os
import requests

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ['API_KEYS_SECRET_ARN']
ARTIFACTS_BUCKET = os.environ['ARTIFACTS_BUCKET']

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    order_id = event.get('order_id')
    line_item_id = event.get('line_item_id')
    birth_data = event.get('birth_data')

    if not all([order_id, line_item_id, birth_data]):
        raise ValueError("Input event is missing order_id, line_item_id, or birth_data")

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        api_keys = json.loads(secret_payload['SecretString'])
        astrology_api_user_id = api_keys.get('AstrologyAPIUserID')
        astrology_api_key = api_keys.get('AstrologyAPIKey')
        
        if not astrology_api_user_id or not astrology_api_key:
            raise ValueError("Astrology API credentials not found in Secrets Manager")

        print(f"Calling AstrologyAPI for order {order_id}, line item {line_item_id}...")
        response = requests.post(
            "https://json.astrologyapi.com/v1/western_horoscope",
            auth=(astrology_api_user_id, astrology_api_key),
            json=birth_data,
            timeout=15 
        )
        response.raise_for_status()
        astrology_data = response.json()
        print("Successfully received data from AstrologyAPI.")

        output_key = f"astrology-json/{order_id}/{line_item_id}.json"
        
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=output_key,
            Body=json.dumps(astrology_data),
            ContentType='application/json'
        )
        print(f"Successfully saved astrology data to s3://{ARTIFACTS_BUCKET}/{output_key}")
        
        # THIS IS THE FIX: Add the new data to the event and return the whole object
        event['astrology_json_s3_path'] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return event

    except Exception as e:
        print(f"ERROR on order {order_id}, line_item {line_item_id}: {e}")
        raise e