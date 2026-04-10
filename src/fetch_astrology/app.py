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

        main_payload = birth_data
        today = datetime.now()
        sr_payload = {**birth_data, "sr_year": today.year}
        transit_payload = {**birth_data, "trans_date": today.strftime('%d-%m-%Y')}

        
        comprehensive_data = {
            "META": {
                "Order_ID": order_id,
                "Request_Date": today.isoformat(),
                "Input_Parameters": birth_data
            },
            "CHARTS": {}
        }

        comprehensive_data["CHARTS"]["AYANAMSHA"] = {
            "Description": "Ayanamsha Calculation",
            "Endpoint": "ayanamsha",
            "Data": call_astrology_api("ayanamsha", vedic_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["PLANETS_EXTENDED"] = {
            "Description": "Extended Planetary Positions",
            "Endpoint": "planets/extended",
            "Data": call_astrology_api("planets/extended", western_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["BHAV_MADHYA"] = {
            "Description": "Vedic Basic Details (Ascendant & Nakshatra)",
            "Endpoint": "astro_details",
            "Data": call_astrology_api("astro_details", vedic_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["WESTERN_HOROSCOPE"] = {
            "Description": "Standard Western Natal Chart",
            "Endpoint": "western_horoscope",
            "Data": call_astrology_api("western_horoscope", western_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["VDASHA"] = {
            "Description": "Vedic Astro Details (Reliable Endpoint)",
            "Endpoint": "current_vdasha",
            "Data": call_astrology_api("current_vdasha", vedic_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["CHARDASHA"] = {
            "Description": "Chardasha (Current)",
            "Endpoint": "current_chardasha",
            "Data": call_astrology_api("current_chardasha", vedic_auth, main_payload)
        }

        comprehensive_data["CHARTS"]["SOLAR_RETURN_HOUSES"] = {
            "Description": "Solar Return House Cusps for Current Year",
            "Endpoint": "solar_return_house_cusps",
            "Data": call_astrology_api("solar_return_house_cusps", western_auth, sr_payload)
        }

        comprehensive_data["CHARTS"]["SOLAR_RETURN_PLANETS"] = {
            "Description": "Solar Return Planetary Positions",
            "Endpoint": "solar_return_planets",
            "Data": call_astrology_api("solar_return_planets", western_auth, sr_payload)
        }

        comprehensive_data["CHARTS"]["SOLAR_RETURN_ASPECTS"] = {
            "Description": "Solar Return Planet Aspects",
            "Endpoint": "solar_return_planet_aspects",
            "Data": call_astrology_api("solar_return_planet_aspects", western_auth, sr_payload)
        }

        comprehensive_data["CHARTS"]["TRANSITS"] = {
            "Description": "Daily Tropical Transits",
            "Endpoint": "tropical_transits/daily",
            "Data": call_astrology_api("tropical_transits/daily", western_auth, transit_payload)
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
        print(f"FATAL ERROR calling {endpoint}: {error_msg}")
        return None