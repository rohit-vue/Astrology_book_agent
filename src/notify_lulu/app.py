# FILE: src/notify_lulu/app.py

import boto3
import json
import os
import requests

# AWS clients
secrets_manager = boto3.client('secretsmanager')

# Environment variables
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

# Lulu API details
LULU_API_URL = "https://api.lulu.com"

def get_lulu_token(client_key, client_secret):
    """Authenticates with Lulu and returns an access token."""
    token_url = f"{LULU_API_URL}/auth/realms/glasstax/protocol/openid-connect/token"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    payload = {'grant_type': 'client_credentials'}
    
    print("Requesting Lulu API access token...")
    response = requests.post(token_url, headers=headers, auth=(client_key, client_secret), data=payload)
    response.raise_for_status() # Will raise an error for non-200 responses
    
    access_token = response.json()['access_token']
    print("Successfully received Lulu access token.")
    return access_token

def lambda_handler(event, context):
    """
    Receives final book data, authenticates with Lulu, and creates a print job.
    """
    print(f"Received event to notify Lulu: {json.dumps(event, indent=2)}")
    
    # Extract necessary info from the Step Function event
    order_id = event['order_id']
    shipping_address = event['shipping_address']
    processed_books = event['processed_books_results']

    try:
        # 1. Fetch secrets from Secrets Manager
        print("Fetching secrets...")
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        client_key = secrets.get('LuluApiClientKey')
        client_secret = secrets.get('LuluApiClientSecret')
        
        if not client_key or not client_secret:
            raise ValueError("Lulu API credentials not found in Secrets Manager")

        # 2. Get Lulu authentication token
        token = get_lulu_token(client_key, client_secret)

        # 3. Construct the line items for the Lulu print job
        line_items = []
        for book in processed_books:
            # Assumes the GeneratePDF lambda returns the S3 path of the final PDF
            pdf_s3_path = book.get('pdf_generation_output', {}).get('final_pdf_s3_path')
            cover_title = book.get('cover_title', 'A Personal Portrait')

            if not pdf_s3_path:
                print(f"WARNING: Skipping a book in order {order_id} because its PDF path was not found.")
                continue

            line_items.append({
                "external_id": book.get('line_item_id', order_id),
                "printable_normalization": {
                    "cover": { "source_url": "URL_TO_YOUR_COVER_PDF_OR_IMAGE" }, # Placeholder
                    "interior": { "source_url": pdf_s3_path },
                },
                "pod_package_id": "0550X0850BWSTDLW060UC444MNG", # Example: 5.5x8.5, B&W, paperback
                "quantity": 1,
                "title": cover_title
            })

        if not line_items:
            raise ValueError(f"Order {order_id} had no valid books to print.")

        # 4. Construct the full payload for the Lulu Print Job API
        lulu_payload = {
            "external_id": order_id,
            "line_items": line_items,
            "production_delay": 1440, # In minutes, e.g., 24 hours
            "shipping_level": "MAIL", # Cheapest shipping option
            "shipping_address": {
                "name": f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}",
                "street1": shipping_address.get('address1'),
                "street2": shipping_address.get('address2'),
                "city": shipping_address.get('city'),
                "state_code": shipping_address.get('province_code'),
                "country_code": shipping_address.get('country_code'),
                "postcode": shipping_address.get('zip')
            }
        }
        
        # 5. Call the Lulu API to create the print job
        print(f"Sending print job to Lulu for order {order_id}...")
        print_job_url = f"{LULU_API_URL}/print-jobs/"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.post(print_job_url, headers=headers, json=lulu_payload)
        response.raise_for_status()
        
        lulu_response = response.json()
        print("Successfully created Lulu print job!")
        print(f"Lulu Job ID: {lulu_response.get('id')}")

        return {
            "status": "SUCCESS",
            "order_id": order_id,
            "lulu_job_id": lulu_response.get('id')
        }

    except Exception as e:
        print(f"ERROR: Failed to create Lulu print job for order {order_id}. Error: {e}")
        if 'response' in locals():
            print(f"Lulu API Response Body: {response.text}")
        raise e