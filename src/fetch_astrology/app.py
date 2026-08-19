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


REQUIRED_CHART_DATA_TYPES = {
    "WESTERN_HOROSCOPE": dict,
    "NATAL_TRANSITS": dict,
    "PLANETS": list,
    "SHADBALA": list,
    "BHAVABALA": dict,
    "VDASHA": dict,
}


def _nonempty_collection(value) -> bool:
    return isinstance(value, (dict, list)) and len(value) > 0


def validate_astrology_artifact(payload: dict) -> None:
    """Raise if META or a required CHARTS branch is missing/empty."""
    if not isinstance(payload, dict):
        raise ValueError("astrology artifact failed contract: payload is not an object")

    errors = []
    meta = payload.get("META")
    if not isinstance(meta, dict) or not meta:
        errors.append("META missing or empty")

    charts = payload.get("CHARTS")
    if not isinstance(charts, dict):
        errors.append("CHARTS missing")
        raise ValueError("astrology artifact failed contract: " + "; ".join(errors))

    for key, expected_type in REQUIRED_CHART_DATA_TYPES.items():
        block = charts.get(key)
        data = block.get("Data") if isinstance(block, dict) else None
        path = f"CHARTS.{key}.Data"
        if not isinstance(data, expected_type) or len(data) == 0:
            errors.append(f"{path} missing or empty")
            continue
        if key == "WESTERN_HOROSCOPE" and not _nonempty_collection(data.get("planets")):
            errors.append(f"{path}.planets missing or empty")
        elif key == "NATAL_TRANSITS" and not _nonempty_collection(data.get("transit_relation")):
            errors.append(f"{path}.transit_relation missing or empty")
        elif key == "BHAVABALA":
            if not (
                _nonempty_collection(data.get("summary"))
                or _nonempty_collection(data.get("houses"))
            ):
                errors.append(f"{path} missing summary and houses")

    if errors:
        raise ValueError("astrology artifact failed contract: " + "; ".join(errors))

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
                "Request_Date": today.isoformat(),
            },
            "CHARTS": {}
        }

        for key, (endpoint, auth, payload) in charts.items():
            comprehensive_data["CHARTS"][key] = {
                "Description": key,
                "Endpoint": endpoint,
                "Data": call_astrology_api(endpoint, auth, payload),
            }

        validate_astrology_artifact(comprehensive_data)

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
        raise