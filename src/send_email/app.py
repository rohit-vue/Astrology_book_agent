import boto3
import json
import os

s3_client = boto3.client('s3')
ses_client = boto3.client('ses')

SENDER_EMAIL = "orders@luminaryblueprint.com" 

def parse_s3_path(s3_path):
    path_parts = s3_path.replace("s3://", "").split("/", 1)
    return path_parts[0], path_parts[1]

def generate_presigned_url(s3_path, expiration=604800): # 7 Days
    bucket, key = parse_s3_path(s3_path)
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=expiration
    )

def lambda_handler(event, context):
    print(f"SendEmail received event: {json.dumps(event)}")
    
    payload = event.get('Payload', event)
    processed_books = payload.get('processed_books_results', [])
    customer_details = payload.get('customer_details', {})
    recipient_email = customer_details.get('email')

    if not recipient_email:
        print("No customer email found. Skipping email delivery.")
        return payload

    download_links = []
    physical_formats = []
    for book in processed_books:
        title = book.get('cover_title', 'Your Astrology Book')
        ebook_path = book.get('ebook_s3_path')
        book_format = (book.get("book_format") or "").strip().lower()
        if not book_format:
            book_format = "digital" if not book.get("requires_shipping", True) else "hardcover"
        if book_format in {"hardcover", "paperback"}:
            physical_formats.append(book_format)
        
        if ebook_path:
            url = generate_presigned_url(ebook_path)
            download_links.append(f'<li><strong>{title}</strong>: <a href="{url}">Download eBook (EPUB)</a></li>')

    if not download_links:
        print("No eBooks found to send.")
        return payload

    if physical_formats:
        if all(fmt == "hardcover" for fmt in physical_formats):
            physical_note = (
                "<p>Your physical hardcover book is currently being printed "
                "and will ship separately.</p>"
            )
        elif all(fmt == "paperback" for fmt in physical_formats):
            physical_note = (
                "<p>Your physical paperback book is currently being printed "
                "and will ship separately.</p>"
            )
        else:
            physical_note = (
                "<p>Your physical book(s) are currently being printed "
                "and will ship separately.</p>"
            )
    else:
        physical_note = ""

    html_body = f"""
    <html>
    <body>
        <h1>Your Astrology Order is Ready!</h1>
        <p>Hello {customer_details.get('first_name', 'Stargazer')},</p>
        <p>Thank you for your order. Your personalized digital books are ready for download.</p>
        <p><strong>Your Downloads (Valid for 7 Days):</strong></p>
        <ul>
            {''.join(download_links)}
        </ul>
        {physical_note}
        <p>Enjoy your journey!</p>
    </body>
    </html>
    """

    try:
        print(f"Sending email to {recipient_email}...")
        response = ses_client.send_email(
            Source=SENDER_EMAIL,
            Destination={'ToAddresses': [recipient_email]},
            Message={
                'Subject': {'Data': "Your Digital Astrology Books are Ready ✧", 'Charset': 'UTF-8'},
                'Body': {'Html': {'Data': html_body, 'Charset': 'UTF-8'}}
            }
        )
        print(f"Email sent! Message ID: {response['MessageId']}")
    except Exception as e:
        print(f"Failed to send email: {e}")
    
    return payload