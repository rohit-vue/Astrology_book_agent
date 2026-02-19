# import boto3

# BUCKET = "astrology-artifacts-astrology-initials-123" 
# KEY = "final-epubs/live-order-001-ebook/personA-ebook.epub" 

# session = boto3.Session(
#     aws_access_key_id=AWS_ACCESS_KEY_ID,
#     aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
#     aws_session_token=AWS_SESSION_TOKEN if AWS_SESSION_TOKEN else None,
#     region_name='us-east-1'
# )

# s3 = session.client('s3')

# try:
#     url = s3.generate_presigned_url(
#         'get_object',
#         Params={'Bucket': BUCKET, 'Key': KEY},
#         ExpiresIn=604800  
#     )
#     print("\nSUCCESS! Here is the download link for your client:\n")
#     print(url)
#     print("\n")
# except Exception as e:
#     print(f"Error: {e}")