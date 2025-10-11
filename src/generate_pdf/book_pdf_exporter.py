# app/book_pdf_exporter.py
from weasyprint import HTML, CSS
from jinja2 import Template
import os
from datetime import datetime
import pathlib

def save_book_as_pdf(title: str, book_data: dict, filename: str, output_dir: str = "/tmp") -> str:
    """
    Generates the final, professionally formatted PDF using a two-pass render
    to guarantee correct page numbers in the Table of Contents.
    """
    # output_dir = "generated_books"
    # os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)

    # --- Prepare all data for the template ---
    all_sections_for_toc = []
    if book_data.get('preface_text'):
        all_sections_for_toc.append({"title": "Preface", "href": "#preface"})
    if book_data.get('prologue_text'):
        prologue_title = book_data.get('prologue_title', "Prologue")
        all_sections_for_toc.append({"title": prologue_title, "href": "#prologue"})
    for i, ch in enumerate(book_data.get("chapters", [])):
        all_sections_for_toc.append({"title": ch["heading"], "href": f"#chapter-{i+1}"})
    if book_data.get('epilogue_text'):
        epilogue_title = book_data.get('epilogue_title', "Epilogue")
        all_sections_for_toc.append({"title": epilogue_title, "href": "#epilogue"})

    # This HTML template is correct and preserves the layout.
    html_template = Template("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8"><title>{{ book_title }}</title>
    </head>
    <body>
        # <div class="page swapi-call-page debug-page"><h1>Data Source</h1><pre class="swapi-text">{{ swapi_call_text }}</pre></div>
        # <div class="page swapi-json-page debug-page"><pre>{{ swapi_json_output }}</pre></div>
        <div class="page blank-page"></div><div class="page blank-page"></div>
        {% if image_path %}<div class="page image-page"><div class="image-container"><img src="{{ image_path }}" alt="AI Generated Book Image"></div></div>{% endif %}
        <div class="page title-page"><div class="title-main-block"><div class="title-decoration">✧</div><h1 class="book-title">{{ book_title }}</h1><div class="title-decoration">✦</div><h2 class="subtitle">A PERSONAL INTERPRETATION</h2></div></div>
        <div class="page print-date-page"><p>A personalized edition created on<br>{{ print_date }}</p></div>
        <div class="page blank-page"></div><div class="page blank-page"></div>
        <div class="page toc-page"><h1>Contents</h1><div class="toc-list">
            {% for entry in toc_entries %}
                <div class="toc-entry">
                    <span class="entry-title"><a href="{{ entry.href }}">{{ entry.title }}</a></span>
                    <span class="leader"></span>
                    <span class="page-number">
                        {% if page_map %}{{ page_map.get(entry.href) }}{% endif %}
                    </span>
                </div>
            {% endfor %}
        </div></div>
        <div class="page blank-page"></div>
        
        <div class="main-content-body">
            {% if preface_text %}<div class="page content-page" id="preface"><h2>Preface</h2><div class="content-block">{% for p in preface_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div></div><div class="page blank-page"></div>{% endif %}
            {% if prologue_text %}<div class="page content-page" id="prologue"><h2>{{ prologue_title | default('Prologue') }}</h2><div class="content-block">{% for p in prologue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div></div><div class="page blank-page"></div>{% endif %}
            {% for chapter in chapters %}
                <div class="page chapter-title-page">
                    <div class="chapter-title-content">
                        <span class="chapter-number">Chapter {{ loop.index }}</span>
                        <h2>{{ chapter.heading }}</h2>
                    </div>
                </div>
                {% if chapter.image_path %}
                    <div class="page image-page"><div class="image-container"><img src="{{ chapter.image_path }}" alt="Image for Chapter {{ loop.index }}"></div></div>
                {% endif %}
                <div class="page content-page" id="chapter-{{ loop.index }}">
                    <div class="content-block">
                        {% for p in chapter.content.split('\n\n') %}<p>{{ p }}</p>{% endfor %}
                    </div>
                </div>
            {% endfor %} 
            {% if epilogue_text %}<div class="page blank-page"></div><div class="page content-page" id="epilogue"><h2>{{ epilogue_title | default('Epilogue') }}</h2><div class="content-block">{% for p in epilogue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div></div>{% endif %}
        </div>
    </body>
    </html>
    """)
    
    fonts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fonts'))
    baskerville_regular_uri = pathlib.Path(os.path.abspath(os.path.join(fonts_dir, 'LibreBaskerville-Regular.ttf'))).as_uri()
    baskerville_italic_uri = pathlib.Path(os.path.abspath(os.path.join(fonts_dir, 'LibreBaskerville-Italic.ttf'))).as_uri()
    baskerville_bold_uri = pathlib.Path(os.path.abspath(os.path.join(fonts_dir, 'LibreBaskerville-Bold.ttf'))).as_uri()
    font_config = f"""@font-face{{font-family:'Baskerville';src:url('{baskerville_regular_uri}');}}@font-face{{font-family:'Baskerville';font-style:italic;src:url('{baskerville_italic_uri}');}}@font-face{{font-family:'Baskerville';font-weight:bold;src:url('{baskerville_bold_uri}');}}"""

    # <<< CSS IS MODIFIED HERE TO MAKE THE TOC MORE COMPACT >>>
    main_css_string = """
    @page { size: 140mm 216mm; margin: 25mm; }
    @page:blank { @bottom-center { content: ""; } }
    @page numbered {
        counter-increment: main-content-counter;
        @bottom-center { content: counter(main-content-counter); font-family: 'Baskerville', serif; font-size: 9pt; }
    }
    body { font-family: 'Baskerville', serif; font-size: 11pt; line-height: 1.6; counter-reset: main-content-counter; }
    .page { page-break-after: always; position: relative; height: 100%; }
    body > div:last-of-type { page-break-after: auto; }
    h1, h2, h3 { font-weight: bold; margin: 0; text-align: center; }
    .main-content-body > .page { page: numbered; }
    
    /* --- Table of Contents Styling --- */
    .toc-page { padding: 2em 0; }
    .toc-page h1 { font-size: 24pt; margin-bottom: 1.2em; }
    .toc-list { width: 85%; margin: 0 auto; }
    
    .toc-entry { 
        display: grid; 
        grid-template-columns: auto 1fr auto; 
        align-items: end; 
        gap: 0 0.7em;
        font-size: 8pt;
        line-height: 1.25;      /* TIGHTENED line height */
        margin-bottom: 0.7em;   /* REDUCED bottom margin */
    }
    
    .entry-title { grid-column: 1; text-align: left; }
    .leader { grid-column: 2; border-bottom: 1px dotted rgba(0,0,0,0.5); margin-bottom: 4px; }
    .page-number { grid-column: 3; text-align: right; }
    .entry-title a { text-decoration: none; color: black; }

    /* Other styles are unchanged */
    .debug-page pre { white-space: pre-wrap; word-wrap: break-word; font-size: 8pt; line-height: 1.1; }
    .swapi-text{ text-align: center }
    .image-page { margin: 0; } .image-container img { max-width: 100%; max-height: 100%; object-fit: contain; }
    .title-page, .print-date-page, .chapter-title-page { display: flex; align-items: center; justify-content: center; }
    .title-main-block { margin: auto 0; text-align: center; }
    .book-title { font-size: 38pt; font-weight: bold; margin: 0.5em 0; line-height: 1.2; }
    .subtitle { font-size: 14pt; margin: 1em 0; letter-spacing: 0.2em; text-transform: uppercase; }
    .title-decoration { font-size: 24pt; margin: 1em 0; color: #555; }
    .print-date-page p { text-align: center; font-style: italic; font-size: 10pt; }
    .chapter-title-content { text-align: center; padding: 2em; font-size: 30pt; }
    .chapter-number { display: block; font-size: 16pt; font-style: italic; color: #666; margin-bottom: 1.5em; text-transform: uppercase; }
    .content-page { padding: 0; }
    .content-page h2 { font-size: 20pt; text-transform: uppercase; margin-bottom: 2.5em; letter-spacing: 0.1em; }
    .content-block { margin: 0 auto; max-width: 100%; }
    .content-block p { text-align: justify; text-indent: 2em; margin-bottom: 0; line-height: 1.7; hyphens: auto; }
    .content-block p + p { margin-top: 1em; }
    .content-block p:first-child { text-indent: 0; }
    .content-block p:first-child::first-letter { font-size: 3.5em;font-weight: bold;}
    """
    css = CSS(string=font_config + main_css_string)
    base_url = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    # --- PASS 1: Render a draft to find the real page number of each anchor ---
    print("--- Starting Pass 1: Finding page numbers... ---")
    draft_context = {"page_map": None, "toc_entries": all_sections_for_toc, **book_data, "book_title": title, "print_date": datetime.now().strftime("%B %d, %Y")}
    draft_html = html_template.render(draft_context)
    doc = HTML(string=draft_html, base_url=base_url).render(stylesheets=[css])
    
    first_content_page_index = -1
    target_anchors = {entry['href'][1:] for entry in all_sections_for_toc}
    for p, page in enumerate(doc.pages):
        page_has_target_anchor = any(anchor in target_anchors for anchor in page.anchors)
        if page_has_target_anchor:
            first_content_page_index = p
            break
            
    if first_content_page_index == -1:
        raise RuntimeError("Could not find the start of the main content to calculate page numbers.")

    page_map = {}
    for p, page in enumerate(doc.pages):
        if p >= first_content_page_index:
            real_page_number = (p - first_content_page_index) + 1
            for anchor_name in page.anchors:
                href = f'#{anchor_name}'
                if anchor_name in target_anchors and href not in page_map:
                    page_map[href] = real_page_number
    
    print(f"--- Pass 1 Complete. Found page numbers: {page_map} ---")

    # --- PASS 2: Render the final PDF, injecting the correct page numbers into the TOC ---
    print("--- Starting Pass 2: Rendering final PDF... ---")
    final_context = {"page_map": page_map, "toc_entries": all_sections_for_toc, **book_data, "book_title": title, "print_date": datetime.now().strftime("%B %d, %Y")}
    final_html = html_template.render(final_context)
    HTML(string=final_html, base_url=base_url).write_pdf(output_path, stylesheets=[css])
    
    return output_path