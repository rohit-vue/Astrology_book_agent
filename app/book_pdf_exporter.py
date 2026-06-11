from weasyprint import HTML, CSS
from jinja2 import Template
import os
import pathlib
import subprocess
import json
import re
from datetime import datetime
import openai 

LABELS = {
    "English": {
        "chapter": "Chapter", "index": "Contents", "preface": "Preface", "prologue": "Prologue",
        "epilogue": "Epilogue", "subtitle": "A PERSONAL INTERPRETATION", "created": "Created on",
        "acknowledgments": "Acknowledgments", "foreword": "A Letter to the Reader"
    },
    "Spanish": {
        "chapter": "Capítulo", "index": "Índice", "preface": "Prefacio", "prologue": "Prólogo",
        "epilogue": "Epílogo", "subtitle": "UNA INTERPRETACIÓN PERSONAL", "created": "Creado el",
        "acknowledgments": "Agradecimientos", "foreword": "Una carta al lector"
    },
    "French": {
        "chapter": "Chapitre", "index": "Sommaire", "preface": "Préface", "prologue": "Prologue",
        "epilogue": "Épilogue", "subtitle": "UNE INTERPRÉTATION PERSONNELLE", "created": "Créé le",
        "acknowledgments": "Remerciements", "foreword": "Une lettre au lecteur"
    },
    "German": {
        "chapter": "Kapitel", "index": "Inhalt", "preface": "Vorwort", "prologue": "Prolog",
        "epilogue": "Epilog", "subtitle": "EINE PERSÖNLICHE INTERPRETATION", "created": "Erstellt am",
        "acknowledgments": "Danksagung", "foreword": "Ein Brief an den Leser"
    },
    "Italian": {
        "chapter": "Capitolo", "index": "Indice", "preface": "Prefazione", "prologue": "Prologo",
        "epilogue": "Epilogo", "subtitle": "UN'INTERPRETAZIONE PERSONALE", "created": "Creato il",
        "acknowledgments": "Ringraziamenti", "foreword": "Una lettera al lettore"
    },
    "Portuguese": {
        "chapter": "Capítulo", "index": "Índice", "preface": "Prefácio", "prologue": "Prólogo",
        "epilogue": "Epílogo", "subtitle": "UMA INTERPRETAÇÃO PESSOAL", "created": "Criado em",
        "acknowledgments": "Agradecimentos", "foreword": "Uma carta ao leitor"
    },
    "Japanese": {
        "chapter": "第", "index": "目次", "preface": "序文", "prologue": "プロローグ",
        "epilogue": "エピローグ", "subtitle": "個人的な解釈", "created": "作成日",
        "acknowledgments": "謝辞", "foreword": "読者への手紙"
    },
    "Hindi": {
        "chapter": "अध्याय", "index": "विषय सूची", "preface": "प्रस्तावना", "prologue": "उपसंहार",
        "epilogue": "उपसंहार", "subtitle": "एक व्यक्तिगत व्याख्या", "created": "को बनाया गया",
        "acknowledgments": "आभार", "foreword": "पाठक के लिए एक पत्र"
    },
    "Chinese": {
        "chapter": "第", "index": "目录", "preface": "前言", "prologue": "序幕",
        "epilogue": "结语", "subtitle": "个人解读", "created": "创建于",
        "acknowledgments": "致谢", "foreword": "致读者的一封信"
    },
    "Korean": {
        "chapter": "제", "index": "목차", "preface": "서문", "prologue": "프롤로그",
        "epilogue": "에필로그", "subtitle": "개인적인 해석", "created": "작성일",
        "acknowledgments": "감사의 말", "foreword": "독자에게 보내는 편지"
    },
    "Russian": {
        "chapter": "Глава", "index": "Содержание", "preface": "Предисловие", "prologue": "Пролог",
        "epilogue": "Эпилог", "subtitle": "ЛИЧНАЯ ИНТЕРПРЕТАЦИЯ", "created": "Создано",
        "acknowledgments": "Благодарности", "foreword": "Письмо читателю"
    }
}

PUBLISHED_BY_LABELS = {
    "english": "Published by",
    "spanish": "Publicado por",
    "french": "Publie par",
    "german": "Veroeffentlicht von",
    "italian": "Pubblicato da",
    "portuguese": "Publicado por",
    "japanese": "Hakkou moto",
    "hindi": "Dwara Prakashit",
    "chinese": "Chu ban",
    "korean": "Balhaeng",
    "russian": "Izdano"
}

def load_text_asset(filename):
    """Loads text from assets folder."""
    base_path = os.environ.get('LAMBDA_TASK_ROOT', os.path.dirname(__file__))
    path = os.path.join(base_path, 'assets', filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        path = os.path.join(os.path.dirname(__file__), 'assets', filename)
        try:
            with open(path, 'r', encoding='utf-8') as f: return f.read().strip()
        except: return ""

def load_json_asset(filename):
    """Loads JSON list from assets folder."""
    data = load_text_asset(filename)
    return json.loads(data) if data else []

def flatten_pdf_fonts(input_path, output_path):
    print(f"Flattening Fonts: {input_path} -> {output_path}")
    cmd = [ "gs", "-o", output_path, "-sDEVICE=pdfwrite", "-dNoOutputFonts", "-dCompatibilityLevel=1.4", input_path ]
    subprocess.run(cmd, check=True)

def normalize_apostrophe_spacing(text):
    if not isinstance(text, str):
        return text

    # Normalize common apostrophe-like characters to plain apostrophe.
    text = re.sub(r"[\u2018\u2019\u02BC\uFF07`´]", "'", text)

    # Remove regular and invisible spacing around apostrophes inside words.
    return re.sub(
        r"(?<=\w)[\s\u00A0\u2007\u202F\u200B\u2060\uFEFF]*'[\s\u00A0\u2007\u202F\u200B\u2060\uFEFF]*(?=\w)",
        "'",
        text,
    )

def save_book_as_pdf(
    title: str,
    book_data: dict,
    filename: str,
    output_dir: str = "/tmp",
    language: str = "English",
    openai_api_key: str = None
) -> tuple[str, int]:
    output_path = os.path.join(output_dir, filename)

    lang_key = language.title() 
    L = LABELS.get(lang_key, LABELS["English"])
    
    chapter_suffix = "章" if lang_key in ["Japanese", "Chinese"] else "장" if lang_key == "Korean" else ""
    
    bd = book_data.get('birth_data', {})
    
    year = bd.get('year', 2000)
    month = bd.get('month', 1)
    day = bd.get('day', 1)
    hour = bd.get('hour', 0)
    minute = bd.get('min', 0)
    lat = bd.get('lat', 0.0)
    lon = bd.get('lon', 0.0)

    birth_str = f"{year}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}"
    lat_str = str(lat)
    lon_str = str(lon)
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    footer_text = f"Written on {today_str}\nWritten for {birth_str} ({lat_str}, {lon_str})"
    footer_date = footer_text
    print(f"[DEBUG] FINAL FOOTER SENT TO PDF:\n{footer_text}")

    foreword_text = load_text_asset("foreword.txt")
    if language.lower() != "english" and openai_api_key:
        print(f"Translating Foreword to {language}...")
        try:
            client = openai.OpenAI(api_key=openai_api_key)
            trans_prompt = f"Translate the following text into {language}. Maintain the poetic, warm, and serious tone. Do not add commentary.\n\nTEXT:\n{foreword_text}"
            trans_resp = client.chat.completions.create(
                model="gpt-5.4-mini-2026-03-17",
                messages=[{"role": "user", "content": trans_prompt}],
                temperature=0.3
            )
            foreword_text = trans_resp.choices[0].message.content.strip()
            print("Translation successful.")
        except Exception as e:
            print(f"Foreword translation failed, falling back to English. Error: {e}")

    foreword_text = normalize_apostrophe_spacing(foreword_text)
    for section_key in ("preface_text", "prologue_text", "epilogue_text"):
        if section_key in book_data:
            book_data[section_key] = normalize_apostrophe_spacing(book_data.get(section_key))
    for chapter in book_data.get("chapters", []):
        chapter["heading"] = normalize_apostrophe_spacing(chapter.get("heading"))
        chapter["content"] = normalize_apostrophe_spacing(chapter.get("content"))

    ack_names = load_json_asset("acknowledgments.json")

    meta = book_data.get('metadata', {})
    dedication_title = meta.get('dedication_title', 'Career by Design')

    toc_base = []
    if book_data.get('prologue_text'): toc_base.append({"title": L['prologue'], "href": "#prologue"})
    for i, ch in enumerate(book_data.get("chapters", [])):
        toc_base.append({"title": ch["heading"], "href": f"#chapter-{i+1}"})
        ch['force_blank_before_image'] = False
        ch['force_blank_before_title'] = False
    if book_data.get('epilogue_text'): toc_base.append({"title": L['epilogue'], "href": "#epilogue"})
    
    book_data['force_blank_before_epilogue'] = False

    html_template = Template("""
    <!DOCTYPE html>
    <html lang="{{ lang }}">
    <head><meta charset="UTF-8"><title>{{ book_title }}</title></head>
    <body>
        <div class="page blank-page frontmatter-blank"></div>
        
        <!-- HALF TITLE -->
        <div class="page title-page">
            <div class="half-title">{{ published_by_label }} LUMINARY BLUEPRINT</div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>

        <!-- FULL TITLE -->
        <div class="page title-page">
            <div class="title-main-block">
                <div class="title-decoration">✧</div>
                <h1 class="book-title">{{ book_title }}</h1>
                <div class="title-decoration">✦</div>
            </div>
        </div>
        <div class="page print-date-page">
            <div style="white-space: pre-wrap; text-align: center; line-height: 2.0;">{{ footer_date }}</div>
        </div>
        <div class="page title-page">
            <div class="half-title">{{ dedication_title }}</div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>
        
        <!-- FOREWORD (Loaded from file) -->
        <div class="page content-page" id="foreword">
            <h2 style="margin-bottom: 0.5em;">{{ labels.get('foreword', 'A Letter to the Reader') }}</h2>
            <p style="text-align: center; margin: 0 0 2em 0; font-style: italic; font-size: 11pt;">Olamide Shokunbi</p>
            <div class="content-block">
                {% for p in foreword_text.split('\n') %}
                    {% if p.strip() %}<p>{{ p }}</p>{% endif %}
                {% endfor %}
            </div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>

        {% if preface_text %}
        <div class="page content-page" id="preface">
            <h2>{{ labels.preface }}</h2>
            <div class="content-block">{% for p in preface_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>
        {% endif %}
        
        <!-- ACKNOWLEDGMENTS (Loaded from file) -->
        <div class="page toc-page">
            <h1>{{ labels.acknowledgments }}</h1>
            <div class="ack-grid">
                {% for name in ack_names %}
                    <div class="ack-item">{{ name }}</div>
                {% endfor %}
            </div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>

        <!-- TOC -->
        <div class="page toc-page">
            <h1>{{ labels.index }}</h1>
            <div class="toc-list">
            {% for entry in toc_entries %}
                <div class="toc-entry">
                    <span class="entry-title"><a href="{{ entry.href }}">{{ entry.title }}</a></span>
                    <span class="leader"></span>
                    <span class="page-number">{% if page_map %}{{ page_map.get(entry.href) }}{% endif %}</span>
                </div>
            {% endfor %}
            </div>
        </div>
        <div class="page blank-page frontmatter-blank"></div>
        
        <!-- PROLOGUE -->
        {% if prologue_text %}
        <div class="page content-page" id="prologue">
            <h2>{{ labels.prologue }}</h2>
            <div class="content-block">{% for p in prologue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
        </div>
        {% endif %}

        <!-- CHAPTERS -->
        {% for chapter in chapters %}
            {% if chapter.force_blank_before_image %}<div class="page blank-page numbered-blank"><span style="visibility:hidden">.</span></div>{% endif %}
            
            {% if chapter.image_path %}
                <div class="page image-page">
                    <div class="image-container"><img src="{{ chapter.image_path }}" alt="Art"></div>
                </div>
            {% endif %}

            {% if chapter.force_blank_before_title %}<div class="page blank-page numbered-blank"><span style="visibility:hidden">.</span></div>{% endif %}

            <div class="page chapter-title-page" id="chapter-{{ loop.index }}">
                <div class="chapter-title-content">
                    <span class="chapter-number">{{ labels.chapter }} {{ loop.index }}{{ suffix }}</span>
                    <h1>{{ chapter.heading }}</h1>
                </div>
            </div>

            <div class="page content-page">
                <div class="content-block">{% for p in chapter.content.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
            </div>
        {% endfor %}  
        
        <!-- EPILOGUE -->
        {% if epilogue_text %}
            {% if force_blank_before_epilogue %}
                <div class="page blank-page numbered-blank"><span style="visibility:hidden">.</span></div>
            {% endif %}
            
            <div class="page content-page" id="epilogue">
                <h2>{{ labels.epilogue }}</h2>
                <div class="content-block">{% for p in epilogue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
            </div>
        {% endif %}
    </body>
    </html>
    """)
    
    lambda_root = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
    fonts_dir = os.path.join(lambda_root, 'fonts')
    if not os.path.exists(fonts_dir): fonts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fonts'))
    baskerville_regular_uri = pathlib.Path(os.path.join(fonts_dir, 'LibreBaskerville-Regular.ttf')).as_uri()
    baskerville_italic_uri = pathlib.Path(os.path.join(fonts_dir, 'LibreBaskerville-Italic.ttf')).as_uri()
    baskerville_bold_uri = pathlib.Path(os.path.join(fonts_dir, 'LibreBaskerville-Bold.ttf')).as_uri()
    font_config = f"""@font-face{{font-family:'Baskerville';src:url('{baskerville_regular_uri}');}}@font-face{{font-family:'Baskerville';font-style:italic;src:url('{baskerville_italic_uri}');}}@font-face{{font-family:'Baskerville';font-weight:bold;src:url('{baskerville_bold_uri}');}}"""

    main_css_string = """
    @page { size: 139.7mm 215.9mm; margin: 20mm; @bottom-center { content: none; } }
    @page numbered { counter-increment: page-num; @bottom-center { content: counter(page-num); font-family: 'Baskerville', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif; font-size: 9pt; } }
    @page numbered :blank { @bottom-center { content: counter(page-num); font-family: 'Baskerville', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif; font-size: 9pt; } }
    @page frontmatter { @bottom-center { content: none; } }
    body { font-family: 'Baskerville', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif; font-size: 8pt; line-height: 1.6; }
    .title-page, .print-date-page, .toc-page, .frontmatter-blank, #preface, #foreword { page: frontmatter; }
    #prologue, #epilogue, .chapter-title-page, .image-page, .content-page, .numbered-blank { page: numbered; }
    #prologue { counter-reset: page-num 0; }
    .page, .title-page, .print-date-page, .toc-page, .chapter-title-page, .image-page, .blank-page { page-break-after: always; position: relative; height: 100%; }
    .image-page { margin: 0; } .image-container img { max-width: 100%; max-height: 100%; object-fit: cover; }
    h1, h2, h3 { font-weight: bold; margin: 0; text-align: center; }
    .toc-page { padding: 2em 0; } .toc-page h1 { font-size: 24pt; margin-bottom: 1.2em; } .toc-list { width: 85%; margin: 0 auto; }
    .toc-entry { display: grid; grid-template-columns: auto 1fr auto; align-items: end; gap: 0 0.7em; font-size: 8pt; line-height: 1.25; margin-bottom: 0.7em; }
    .entry-title { grid-column: 1; text-align: left; } .leader { grid-column: 2; border-bottom: 1px dotted rgba(0,0,0,0.5); margin-bottom: 4px; } .page-number { grid-column: 3; text-align: right; }
    .entry-title a { text-decoration: none; color: black; }
    .title-page, .print-date-page, .chapter-title-content { text-align: center; width: 100%; margin: auto; box-sizing: border-box; }
    .title-main-block { margin: auto 0; text-align: center; }
    .half-title { font-size: 14pt; margin: auto 0; text-align: center; text-transform: uppercase; letter-spacing: 2px; }
    .book-title { font-size: 38pt; font-weight: bold; margin: 0.5em 0; line-height: 1.2; }
    .subtitle { font-size: 14pt; margin: 1em 0; letter-spacing: 0.2em; text-transform: uppercase; }
    .title-decoration { font-size: 24pt; margin: 1em 0; color: #555; }
    .print-date-page p { text-align: center; font-style: italic; font-size: 10pt; }
    .chapter-title-page { display: flex; width: 100%; align-items: center; justify-content: center; text-align: center; }
    .chapter-title-content h1 { font-size: 35pt }
    .chapter-number { display: block; font-size: 16pt; font-style: italic; color: #666; margin-bottom: 1.5em; text-transform: uppercase; }
    .content-page { padding: 0; }
    .content-page h2 { font-size: 20pt; text-transform: uppercase; margin-bottom: 2.5em; letter-spacing: 0.1em; }
    .content-block { margin: 0 auto; max-width: 100%; }
    .content-block p { text-align: justify; text-indent: 2em; margin-bottom: 0; line-height: 1.7; hyphens: auto; }
    .content-block p + p { margin-top: 1em; }
    .content-block p:first-child { text-indent: 0; }
    .content-block p:first-child::first-letter { font-size: 3.5em;font-weight: bold;}
    .ack-grid { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 1em; text-align: center; margin-top: 3em; font-size: 9pt; }
    .ack-item { margin-bottom: 0.5em; }
    """
    
    css = CSS(string=font_config + main_css_string)
    base_url = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    published_by_label = PUBLISHED_BY_LABELS.get(language.strip().lower(), PUBLISHED_BY_LABELS["english"])
    context = {"book_title": title, "labels": L, "suffix": chapter_suffix, "footer_date": footer_date, "toc_entries": toc_base, **book_data, "lang": language, "dedication_title": dedication_title, "ack_names": ack_names, "foreword_text": foreword_text, "published_by_label": published_by_label}
    
    print("PDF Engine: Pass 1")
    draft_html = html_template.render({**context, "page_map": None})
    doc = HTML(string=draft_html, base_url=base_url).render(stylesheets=[css])
    
    prologue_page_index = 0
    for i, page in enumerate(doc.pages):
        if 'prologue' in page.anchors:
            prologue_page_index = i
            break
            
    cumulative_shift = 0
    for i, ch in enumerate(book_data.get("chapters", [])):
        title_page_idx = -1
        target_anchor = f"chapter-{i+1}"
        for p_idx, page in enumerate(doc.pages):
            if target_anchor in page.anchors:
                title_page_idx = p_idx
                break
        
        if title_page_idx != -1:
            current_virtual_idx = title_page_idx + cumulative_shift
            current_page_num = (current_virtual_idx - prologue_page_index) + 1
            if ch.get('image_path'):
                image_page_num = current_page_num - 1
                if image_page_num % 2 != 0: 
                    ch['force_blank_before_image'] = True
                    cumulative_shift += 1
            else:
                if current_page_num % 2 == 0:
                    ch['force_blank_before_title'] = True
                    cumulative_shift += 1
    
    print("PDF Engine: Pass 2")
    final_context = {"book_title": title, "labels": L, "suffix": chapter_suffix, "footer_date": footer_date, "toc_entries": toc_base, **book_data, "lang": language, "dedication_title": dedication_title, "ack_names": ack_names, "foreword_text": foreword_text, "published_by_label": published_by_label}
    doc_2 = HTML(string=html_template.render({**final_context, "page_map": None}), base_url=base_url).render(stylesheets=[css])

    prologue_page_index_2 = 0
    for i, page in enumerate(doc_2.pages):
        if 'prologue' in page.anchors:
            prologue_page_index_2 = i
            break
            
    page_map = {}
    if book_data.get('prologue_text'): page_map['#prologue'] = 1

    for i, page in enumerate(doc_2.pages):
        if i >= prologue_page_index_2:
            real_page_number = (i - prologue_page_index_2) + 1
            for anchor_name in page.anchors:
                if anchor_name != 'prologue':
                    page_map[f'#{anchor_name}'] = real_page_number

    print("PDF Engine: Pass 3")
    doc_3 = HTML(string=html_template.render({**final_context, "page_map": page_map}), base_url=base_url).render(stylesheets=[css])
    
    prologue_page_index_3 = 0
    for i, page in enumerate(doc_3.pages):
        if 'prologue' in page.anchors:
            prologue_page_index_3 = i
            break
            
    final_page_map = {}
    if book_data.get('prologue_text'): final_page_map['#prologue'] = 1

    for i, page in enumerate(doc_3.pages):
        if i >= prologue_page_index_3:
            real_page_number = (i - prologue_page_index_3) + 1
            for anchor_name in page.anchors:
                if anchor_name != 'prologue':
                    if anchor_name == 'epilogue':
                        final_page_map[f'#{anchor_name}'] = real_page_number - 1
                    else:
                        final_page_map[f'#{anchor_name}'] = real_page_number

    print("PDF Engine: Pass 4")
    final_html = html_template.render({**final_context, "page_map": final_page_map})
    
    temp_pdf_path = output_path.replace(".pdf", "_temp.pdf")
    HTML(string=final_html, base_url=base_url).write_pdf(temp_pdf_path, stylesheets=[css])
    
    try:
        flatten_pdf_fonts(temp_pdf_path, output_path)
    except Exception as e:
        print(f"Flattening failed: {e}. Using unflattened.")
        os.rename(temp_pdf_path, output_path)
    
    return output_path, len(HTML(string=final_html, base_url=base_url).render(stylesheets=[css]).pages)
