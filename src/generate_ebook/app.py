import boto3
import json
import os
from ebooklib import epub
from urllib.parse import urlparse
import requests

s3_client = boto3.client('s3')
ARTIFACTS_BUCKET = os.environ.get('ARTIFACTS_BUCKET')

def parse_s3_path(s3_path):
    parsed = urlparse(s3_path, allow_fragments=False)
    return parsed.netloc, parsed.path.lstrip('/')

def download_image(url):
    try:
        resp = requests.get(url, timeout=10)
        return resp.content if resp.status_code == 200 else None
    except: return None

def lambda_handler(event, context):
    print(f"GenerateEbook event: {json.dumps(event)}")
    payload = event.get('Payload', event)
    
    order_id = payload.get('order_id')
    line_item_id = payload.get('line_item_id')
    chapters_data = payload.get('chapters_data', [])
    full_book_structure = payload.get('full_book_structure', {})
    
    book = epub.EpubBook()
    title = full_book_structure.get("metadata", {}).get("title", "The Luminary Blueprint")
    book.set_identifier(line_item_id)
    book.set_title(title)
    book.set_language('en')

    hardcover_front_cover_url = payload.get('hardcover_front_image_url')
    if hardcover_front_cover_url:
        cover_data = download_image(hardcover_front_cover_url)
        if cover_data:
            book.set_cover("cover.jpg", cover_data)
            print("EPUB cover set from hardcover front image.")
        else:
            print("Could not download hardcover front image for EPUB cover.")

    style = '''
        @namespace epub "http://www.idpf.org/2007/ops";
        body { font-family: serif; line-height: 1.5; }
        h1 { text-align: center; margin-top: 2em; margin-bottom: 1em; }
        
        /* House Style: Indent, No Margin */
        p { 
            text-indent: 1.2em; 
            margin: 0; 
            text-align: justify;
        }
        
        /* First paragraph after header: No Indent */
        p.noindent { text-indent: 0; }
        
        .chapter-img { display: block; margin: 1em auto; max-width: 100%; }
        .center { text-align: center; }
    '''
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)

    spine = ['nav']
    

    for i, chap in enumerate(chapters_data):
        chapter_title = chap.get('chapter_title', f'Chapter {i+1}')
        file_name = f'chapter_{i+1}.xhtml'
        
        text_s3 = chap.get('chapter_text_s3_path')
        content_text = ""
        if text_s3:
            b, k = parse_s3_path(text_s3)
            obj = s3_client.get_object(Bucket=b, Key=k)
            c_data = json.loads(obj['Body'].read().decode('utf-8'))
            content_text = c_data.get('chapter_text', '')
        
        paras = content_text.split('\n\n')
        html_paras = []
        for idx, p in enumerate(paras):
            cls = 'class="noindent"' if idx == 0 else ''
            html_paras.append(f'<p {cls}>{p}</p>')
        formatted_html = "".join(html_paras)
        
        img_html = ""
        img_url = chap.get('image_url')
        if img_url:
            img_data = download_image(img_url)
            if img_data:
                img_name = f"image_{i+1}.jpg"
                book.add_item(epub.EpubItem(uid=f"img_{i+1}", file_name=f"images/{img_name}", media_type="image/jpeg", content=img_data))
                img_html = f'<div class="chapter-img"><img src="images/{img_name}" alt="Art" /></div>'

        c_item = epub.EpubHtml(title=chapter_title, file_name=file_name)
        c_item.content = f"""
            <html><body>
                <h1>{chapter_title}</h1>
                {img_html}
                {formatted_html}
            </body></html>
        """
        c_item.add_item(nav_css)
        book.add_item(c_item)
        spine.append(c_item)

    if full_book_structure.get('epilogue'):
        epilogue = epub.EpubHtml(title='Epilogue', file_name='epilogue.xhtml')
        formatted_text = "".join([f"<p>{p}</p>" for p in full_book_structure['epilogue'].split('\n\n')])
        epilogue.content = f"<html><body><h1>Epilogue</h1>{formatted_text}</body></html>"
        book.add_item(epilogue)
        spine.append(epilogue)

    book.toc = tuple(spine[1:]) 
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    output_filename = f"{line_item_id}.epub"
    output_path = f"/tmp/{output_filename}"
    epub.write_epub(output_path, book, {})
    
    print(f"EPUB generated at {output_path}")

    s3_key = f"final-epubs/{order_id}/{output_filename}"
    s3_client.upload_file(
        output_path, 
        ARTIFACTS_BUCKET, 
        s3_key,
        ExtraArgs={"ContentType": "application/epub+zip"}
    )
    
    s3_uri = f"s3://{ARTIFACTS_BUCKET}/{s3_key}"
    print(f"EPUB uploaded to {s3_uri}")

    payload['ebook_s3_path'] = s3_uri
    return payload
