import boto3
import json
import os
import requests
from datetime import datetime

s3_client = boto3.client('s3')
secrets_manager_client = boto3.client('secretsmanager')

API_KEYS_SECRET_ARN = os.environ['API_KEYS_SECRET_ARN']
ARTIFACTS_BUCKET = os.environ['ARTIFACTS_BUCKET']

def call_astrology_api(endpoint, auth, payload, timeout=15):
    full_url = f"https://json.astrologyapi.com/v1/{endpoint}"
    print(f"Calling {endpoint}...")
    try:
        response = requests.post(full_url, auth=auth, json=payload, timeout=timeout)
        response.raise_for_status() 
        return response.json()
    except Exception as e:
        print(f"Error calling {endpoint}: {e}")
        return None

def lambda_handler(event, context):
    print(f"FetchAstrology received: {json.dumps(event)}")
    
    order_id = event.get('order_id')
    line_item_id = event.get('line_item_id')
    birth_data = event.get('birth_data')

    if not all([order_id, line_item_id, birth_data]):
        raise ValueError("Missing required data")

    try:
        secret_payload = secrets_manager_client.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        api_keys = json.loads(secret_payload['SecretString'])

        required_keys = ['AstrologyWesternUserID', 'AstrologyWesternAPIKey', 'AstrologyVedicUserID', 'AstrologyVedicAPIKey']
        if not all(key in api_keys for key in required_keys):
            raise KeyError("One or more required API keys are missing from Secrets Manager.")

        western_auth = (api_keys['AstrologyWesternUserID'], api_keys['AstrologyWesternAPIKey'])
        vedic_auth = (api_keys['AstrologyVedicUserID'], api_keys['AstrologyVedicAPIKey'])

        today = datetime.now()
        transit_payload = {**birth_data, "trans_date": today.strftime('%d-%m-%Y')}
        charts = {
            "WESTERN_HOROSCOPE": ("western_horoscope", western_auth, birth_data),
            "NATAL_TRANSITS": ("natal_transits/daily", western_auth, transit_payload),
            "PLANETS": ("planets", western_auth, birth_data),
            "SHADBALA": ("shadbala", vedic_auth, birth_data),
            "BHAVABALA": ("bhavabala", vedic_auth, birth_data),
            "VDASHA": ("current_vdasha", vedic_auth, birth_data),
        }

        comprehensive_data = {
            "META": {
                "Order_ID": order_id,
                "Request_Date": today.isoformat(),
                "Input_Parameters": birth_data
            },
            "CHARTS": {}
        }

        for key, (endpoint, auth, payload) in charts.items():
            comprehensive_data["CHARTS"][key] = {
                "Description": key,
                "Endpoint": endpoint,
                "Data": call_astrology_api(endpoint, auth, payload),
            }

        output_key = f"astrology-json/{order_id}/{line_item_id}.json"
        s3_client.put_object(
            Bucket=ARTIFACTS_BUCKET, Key=output_key,
            Body=json.dumps(comprehensive_data, indent=2), ContentType='application/json'
        )
        
        event['astrology_json_s3_path'] = f"s3://{ARTIFACTS_BUCKET}/{output_key}"
        return event

    except Exception as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
             error_msg += f" | Body: {e.response.text}"
        print(f"FATAL ERROR fetching astrology data: {error_msg}")
        return None