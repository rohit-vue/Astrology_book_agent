# FILE: src/fetch_qanda_data/app.py

import boto3
import os

s3_client = boto3.client('s3')

QANDA_S3_URI = os.environ['QANDA_S3_URI']

def parse_s3_uri(s3_uri):
    path_parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = path_parts[0]
    key = path_parts[1]
    return bucket, key

def lambda_handler(event, context):
    """
    Securely fetches the {Q&A} plain text file content from a private S3 bucket.
    """
    print(f"Fetching proprietary {{Q&A}} data from S3 URI: {QANDA_S3_URI}...")

    try:
        bucket_name, key_name = parse_s3_uri(QANDA_S3_URI)

        s3_object = s3_client.get_object(Bucket=bucket_name, Key=key_name)
        
        qanda_content_string = s3_object['Body'].read().decode('utf-8')
        
        print(f"Successfully downloaded {len(qanda_content_string)} characters of {{Q&A}} content.")

        event['qanda_content'] = qanda_content_string
        
        return event

    except Exception as e:
        print(f"ERROR: Failed to fetch or process proprietary data from S3. Error: {e}")
        raise e