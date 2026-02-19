# FILE: src/debug_lulu_files/app.py

import boto3
import json
import os
import requests

secrets_manager = boto3.client('secretsmanager')
API_KEYS_SECRET_ARN = os.environ.get('API_KEYS_SECRET_ARN')

def get_lulu_token(client_key, client_secret):
    LULU_AUTH_URL = "https://api.sandbox.lulu.com/auth/realms/glasstree/protocol/openid-connect/token"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    payload = {'grant_type': 'client_credentials'}
    response = requests.post(LULU_AUTH_URL, headers=headers, auth=(client_key, client_secret), data=payload)
    response.raise_for_status()
    return response.json()['access_token']

def lambda_handler(event, context):
    print("--- Starting Lulu File Validation Test ---")
    
    try:
        secret_payload = secrets_manager.get_secret_value(SecretId=API_KEYS_SECRET_ARN)
        secrets = json.loads(secret_payload['SecretString'])
        client_key = secrets.get('LuluApiClientKey')
        client_secret = secrets.get('LuluApiClientSecret')
        
        token = get_lulu_token(client_key, client_secret)
        
        
        interior_file_url = "https://astrology-artifacts-astrology-initials-123.s3.amazonaws.com/book-covers/shpfy_GOLDEN_TEST_001/golden_test_line_item_001_dust_jacket_final.png?AWSAccessKeyId=ASIAWVO2ZGGYZWH7YRWX&Signature=KnF0%2BMktAcygC9laDArLy%2FdLFaQ%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEBcaCXVzLWVhc3QtMSJIMEYCIQCZGVc9VtCQB352aJvWHXBiPDHWBpQH4NqpMLg8oSI0bQIhAKw5hBnmFoq24X5z2oi2F65FltThn5xRP8gg6CrDdJ6KKpwDCND%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNDU4NDA5NzE4MTkzIgwoMaicbphTSWrzZB0q8AKq43Kb4Wmg2oSFJsGM0WW11nfDqN3eOx9kaWfUNUj1udk3a0nxFSH1NED%2FEN%2FTdyrI16h2xkcWwVrrm%2BG%2Bs9fSHjvljUV%2B2b88MoEyrWVRQceRFpIYlWI5IV2IighxnMtJeKaDOZa%2B8LSuAQ1XDNBDz8xNinJg9Fsm6byzMV8VhtCBk3moBhszOyLyt8c09ofoVVLmjhGwbmVmtQ%2Bx%2Bv7T36gxJc4fRg2xRg1cucBp%2BTqcexEBfIKENdYO5ufqHt%2BtYPCUDD1hLtsgmLSqtUDfSv%2FgTkeIE5oaUjcV%2FH2k6JAYJYAspsDUfXCFC6KJDnEytRVQvv2TNjXOc3nfH%2BzqT5QciUWBfRRg34zMmqYjy8nMb7Vsbola69JJCfu1Y9pBKG%2FNNLTxvHWZ9BwbECP7XxFxWPtRPr80gbIPILXTN2YhmxSkKVSlQ0QQFnmbVtfBBmvvQsNnRodoOUYnKRpvwwQ5OBRKMWVoduYvckBbRjDJ%2BIbIBjqcAYDLRYTIDqucFS3wjtSl6FnSgXyshzwjokqnNo3fi6VZ6NPechKWQhX6lw323kIIAq%2B4QdCXi0S0WtGR%2Flic2JDRyXhBaBpNLMSZYDlssNLC0P4E%2Bim9jcLeImweHNIrLTee3J3ForzkWQY4E%2B%2Bv0oFnCSV%2FgYQ5v6m5sI6BBh9twInqDBHfunu%2BMRL%2BoLx2zr%2F0up4anvNp9bepow%3D%3D&Expires=1761725018"
        cover_file_url = "https://astrology-artifacts-astrology-initials-123.s3.amazonaws.com/final-pdfs/shpfy_GOLDEN_TEST_001/golden_test_line_item_001.pdf?AWSAccessKeyId=ASIAWVO2ZGGYZWH7YRWX&Signature=UwH4OJi%2BsS9FAWKxD4lYGsB0bvM%3D&x-amz-security-token=IQoJb3JpZ2luX2VjEBcaCXVzLWVhc3QtMSJIMEYCIQCZGVc9VtCQB352aJvWHXBiPDHWBpQH4NqpMLg8oSI0bQIhAKw5hBnmFoq24X5z2oi2F65FltThn5xRP8gg6CrDdJ6KKpwDCND%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNDU4NDA5NzE4MTkzIgwoMaicbphTSWrzZB0q8AKq43Kb4Wmg2oSFJsGM0WW11nfDqN3eOx9kaWfUNUj1udk3a0nxFSH1NED%2FEN%2FTdyrI16h2xkcWwVrrm%2BG%2Bs9fSHjvljUV%2B2b88MoEyrWVRQceRFpIYlWI5IV2IighxnMtJeKaDOZa%2B8LSuAQ1XDNBDz8xNinJg9Fsm6byzMV8VhtCBk3moBhszOyLyt8c09ofoVVLmjhGwbmVmtQ%2Bx%2Bv7T36gxJc4fRg2xRg1cucBp%2BTqcexEBfIKENdYO5ufqHt%2BtYPCUDD1hLtsgmLSqtUDfSv%2FgTkeIE5oaUjcV%2FH2k6JAYJYAspsDUfXCFC6KJDnEytRVQvv2TNjXOc3nfH%2BzqT5QciUWBfRRg34zMmqYjy8nMb7Vsbola69JJCfu1Y9pBKG%2FNNLTxvHWZ9BwbECP7XxFxWPtRPr80gbIPILXTN2YhmxSkKVSlQ0QQFnmbVtfBBmvvQsNnRodoOUYnKRpvwwQ5OBRKMWVoduYvckBbRjDJ%2BIbIBjqcAYDLRYTIDqucFS3wjtSl6FnSgXyshzwjokqnNo3fi6VZ6NPechKWQhX6lw323kIIAq%2B4QdCXi0S0WtGR%2Flic2JDRyXhBaBpNLMSZYDlssNLC0P4E%2Bim9jcLeImweHNIrLTee3J3ForzkWQY4E%2B%2Bv0oFnCSV%2FgYQ5v6m5sI6BBh9twInqDBHfunu%2BMRL%2BoLx2zr%2F0up4anvNp9bepow%3D%3D&Expires=1761725018"
        pod_package_id = "0600X0900BWSTDPB060UW444MXX" 
        

        validation_payload = {
            "pod_package_id": pod_package_id,
            "cover": { "source_url": cover_file_url },
            "interior": { "source_url": interior_file_url }
        }

        print("Sending payload to Lulu File Validation API:")
        print(json.dumps(validation_payload, indent=2))
        
        validation_url = "https://api.sandbox.lulu.com/files/validation/"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        response = requests.post(validation_url, headers=headers, json=validation_payload)
        
        print("--- LULU VALIDATION API RESPONSE ---")
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(response.text) 
        print("--- END OF RESPONSE ---")

        response.raise_for_status()
        
        return {
            'statusCode': 200,
            'body': response.json()
        }

    except Exception as e:
        print(f"ERROR during validation test: {e}")
        if 'response' in locals():
            print(f"Error Response Body: {response.text}")
        raise e