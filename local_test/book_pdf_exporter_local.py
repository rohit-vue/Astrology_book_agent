"""
Local Docker PDF layout override: foreword + acknowledgments moved after epilogue.
Mounted over generate_pdf/book_pdf_exporter.py in local_test/docker-compose.yml only.
"""
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
        "acknowledgments": "Acknowledgments"
    },
    "Spanish": {
        "chapter": "Capítulo", "index": "Índice", "preface": "Prefacio", "prologue": "Prólogo",
        "epilogue": "Epílogo", "subtitle": "UNA INTERPRETACIÓN PERSONAL", "created": "Creado el",
        "acknowledgments": "Agradecimientos"
    },
    "French": {
        "chapter": "Chapitre", "index": "Sommaire", "preface": "Préface", "prologue": "Prologue",
        "epilogue": "Épilogue", "subtitle": "UNE INTERPRÉTATION PERSONNELLE", "created": "Créé le",
        "acknowledgments": "Remerciements"
    },
    "German": {
        "chapter": "Kapitel", "index": "Inhalt", "preface": "Vorwort", "prologue": "Prolog",
        "epilogue": "Epilog", "subtitle": "EINE PERSÖNLICHE INTERPRETATION", "created": "Erstellt am",
        "acknowledgments": "Danksagung"
    },
    "Italian": {
        "chapter": "Capitolo", "index": "Indice", "preface": "Prefazione", "prologue": "Prologo",
        "epilogue": "Epilogo", "subtitle": "UN'INTERPRETAZIONE PERSONALE", "created": "Creato il",
        "acknowledgments": "Ringraziamenti"
    },
    "Portuguese": {
        "chapter": "Capítulo", "index": "Índice", "preface": "Prefácio", "prologue": "Prólogo",
        "epilogue": "Epílogo", "subtitle": "UMA INTERPRETAÇÃO PESSOAL", "created": "Criado em",
        "acknowledgments": "Agradecimentos"
    },
    "Japanese": {
        "chapter": "第", "index": "目次", "preface": "序文", "prologue": "プロローグ",
        "epilogue": "エピローグ", "subtitle": "個人的な解釈", "created": "作成日",
        "acknowledgments": "謝辞"
    },
    "Hindi": {
        "chapter": "अध्याय", "index": "विषय सूची", "preface": "प्रस्तावना", "prologue": "उपसंहार",
        "epilogue": "उपसंहार", "subtitle": "एक व्यक्तिगत व्याख्या", "created": "को बनाया गया",
        "acknowledgments": "आभार"
    },
    "Chinese": {
        "chapter": "第", "index": "目录", "preface": "前言", "prologue": "序幕",
        "epilogue": "结语", "subtitle": "个人解读", "created": "创建于",
        "acknowledgments": "致谢"
    },
    "Korean": {
        "chapter": "제", "index": "목차", "preface": "서문", "prologue": "프롤로그",
        "epilogue": "에필로그", "subtitle": "개인적인 해석", "created": "작성일",
        "acknowledgments": "감사의 말"
    },
    "Russian": {
        "chapter": "Глава", "index": "Содержание", "preface": "Предисловие", "prologue": "Пролог",
        "epilogue": "Эпилог", "subtitle": "ЛИЧНАЯ ИНТЕРПРЕТАЦИЯ", "created": "Создано",
        "acknowledgments": "Благодарности"
    }
}

LANG_CODES = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Japanese": "ja",
    "Hindi": "hi",
    "Chinese": "zh",
    "Korean": "ko",
    "Russian": "ru",
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

FOOTER_DATE_LABELS = {
    "english": {"written_on": "Written on", "written_for": "Written for"},
    "spanish": {"written_on": "Escrito el", "written_for": "Escrito para"},
    "french": {"written_on": "Ecrit le", "written_for": "Ecrit pour"},
    "german": {"written_on": "Geschrieben am", "written_for": "Geschrieben fuer"},
    "italian": {"written_on": "Scritto il", "written_for": "Scritto per"},
    "portuguese": {"written_on": "Escrito em", "written_for": "Escrito para"},
    "japanese": {"written_on": "作成日", "written_for": "作成対象"},
    "hindi": {"written_on": "लिखा गया", "written_for": "के लिए लिखा गया"},
    "chinese": {"written_on": "写于", "written_for": "写给"},
    "korean": {"written_on": "작성일", "written_for": "대상"},
    "russian": {"written_on": "Написано", "written_for": "Написано для"},
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

    # --- Quotation marks: trim space touching quotes, then collapse doubled horizontal space ---
    # Horizontal only (no \n) so \n\n paragraph breaks stay intact.
    _h = r"[ \t\u00A0\u1680\u2000-\u200A\u2007\u202F\u205F\u3000]"
    _qo = "[\u201c\"]"
    _qc = "[\u201d\"]"
    # Inside the pair: no gap between quote and word.
    text = re.sub("(" + _qo + r")" + _h + r"+(?=\S)", r"\1", text)
    text = re.sub(r"(?<=\S)" + _h + r"+(" + _qc + r")", r"\1", text)
    # Any 2+ horizontal spaces → one space (fixes as␠␠“honesty and ”,␠␠and — the latter
    # was missed when we only matched spaces directly after ”, not after comma).
    text = re.sub(_h + r"{2,}", " ", text)

    # Normalize common apostrophe-like characters to plain apostrophe.
    text = re.sub(r"[\u2018\u2019\u02BC\uFF07`´]", "'", text)

    # Remove regular and invisible spacing around apostrophes inside words.
    text = re.sub(
        r"(?<=\w)[\s\u00A0\u2007\u202F\u200B\u2060\uFEFF]*'[\s\u00A0\u2007\u202F\u200B\u2060\uFEFF]*(?=\w)",
        "'",
        text,
    )
    return text


def _folio_start_page_index(doc, book_data) -> int:
    """Index of the first PDF page where body folio numbering begins (folio 1)."""
    if book_data.get("prologue_text"):
        for i, page in enumerate(doc.pages):
            if "prologue" in page.anchors:
                return i
    for i, page in enumerate(doc.pages):
        if "chapter-1" in page.anchors:
            return i
    return 0


def _build_page_map_from_doc(doc, book_data) -> dict:
    """Map anchor ids to body folio using a rendered document."""
    folio_start = _folio_start_page_index(doc, book_data)
    page_map = {}
    if book_data.get("prologue_text"):
        page_map["#prologue"] = 1

    for i, page in enumerate(doc.pages):
        if i >= folio_start:
            folio_num = (i - folio_start) + 1
            for anchor_name in page.anchors:
                if anchor_name != "prologue":
                    page_map[f"#{anchor_name}"] = folio_num
    return page_map


def _is_english_book_language(language: str) -> bool:
    """True only for English books (storefront may send 'English' or 'english')."""
    key = str(language or "").strip().lower()
    return key == "english" or key.startswith("english,") or key.startswith("english ")


def save_book_as_pdf(
    title: str,
    book_data: dict,
    filename: str,
    output_dir: str = "/tmp",
    language: str = "English",
    openai_api_key: str = None
) -> tuple[str, int]:
    include_letter = _is_english_book_language(language)
    print(
        "[local PDF layout] Foreword + acknowledgments render after epilogue "
        "(blank, letter, blank, acknowledgments)."
    )
    if include_letter:
        print("[local PDF layout] Letter to the Reader included (book language is English).")
    else:
        print(
            f"[local PDF layout] Letter to the Reader omitted "
            f"(book language is {language!r}, not English)."
        )
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
    
    footer_labels = FOOTER_DATE_LABELS.get(lang_key.strip().lower(), FOOTER_DATE_LABELS["english"])
    today_str = datetime.now().strftime('%Y-%m-%d')
    footer_text = (
        f"{footer_labels['written_on']} {today_str}\n"
        f"{footer_labels['written_for']} {birth_str} ({lat_str}, {lon_str})"
    )
    footer_date = footer_text
    print(f"[DEBUG] FINAL FOOTER SENT TO PDF:\n{footer_text}")

    # Local rule: include the English letter only when the book language is English.
    foreword_text = ""
    if include_letter:
        foreword_text = normalize_apostrophe_spacing(load_text_asset("foreword.txt"))
    for section_key in ("preface_text", "prologue_text", "epilogue_text"):
        if section_key in book_data:
            book_data[section_key] = normalize_apostrophe_spacing(book_data.get(section_key))
    for chapter in book_data.get("chapters", []):
        chapter["heading"] = normalize_apostrophe_spacing(chapter.get("heading"))
        chapter["content"] = normalize_apostrophe_spacing(chapter.get("content"))

    ack_names = load_json_asset("acknowledgments.json")

    meta = book_data.get('metadata', {})
    focus_raw = (book_data.get("focus") or "").strip()
    if focus_raw:
        dedication_title = f"{focus_raw} BY DESIGN".upper()
    else:
        dedication_title = meta.get('dedication_title', 'Career by Design')

    toc_base = []
    if book_data.get('prologue_text'): toc_base.append({"title": L['prologue'], "href": "#prologue"})
    for i, ch in enumerate(book_data.get("chapters", [])):
        toc_base.append({"title": ch["heading"], "href": f"#chapter-{i+1}"})
    if book_data.get('epilogue_text'): toc_base.append({"title": L['epilogue'], "href": "#epilogue-heading"})

    html_template = Template("""
    <!DOCTYPE html>
    <html lang="{{ html_lang }}">
    <head><meta charset="UTF-8"><title>{{ book_title }}</title></head>
    <body>
        <!-- HALF TITLE -->
        <div class="page title-page">
            <div class="half-title">{{ published_by_label }} LUMINARY BLUEPRINT</div>
        </div>

        <div class="page blank-page frontmatter-blank"></div>

        <!-- PRINT DATE -->
        <div class="fm-break fm-break-recto">
            <div class="page print-date-page">
                <div style="white-space: pre-wrap; text-align: center; line-height: 2.0;">{{ footer_date }}</div>
            </div>
        </div>

        <!-- DEDICATION -->
        <div class="fm-break fm-break-verso">
            <div class="page title-page">
                <div class="half-title">{{ dedication_title }}</div>
            </div>
        </div>

        <!-- FULL TITLE -->
        <div class="fm-break fm-break-recto">
            <div class="page title-page">
                <div class="title-main-block">
                    <div class="title-decoration">✧</div>
                    <h1 class="book-title" style="text-transform: uppercase;">{{ book_title }}</h1>
                    <div class="title-decoration">✦</div>
                </div>
            </div>
        </div>

        <div class="page blank-page frontmatter-blank"></div>

        <!-- PREFACE -->
        {% if preface_text %}
        <div class="fm-break fm-break-recto">
            <div class="page content-page" id="preface">
                <h2>{{ labels.preface }}</h2>
                <div class="content-block">{% for p in preface_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
            </div>
        </div>
        {% endif %}

        <!-- TOC -->
        <div class="fm-break fm-break-recto">
            <div class="page toc-page">
                <h1 style="text-transform: uppercase;">{{ labels.index }}</h1>
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
        </div>

        <div class="page blank-page frontmatter-blank"></div>

        <!-- PROLOGUE -->
        <main class="book-numbered-body">
            {% if prologue_text %}
            <div class="fm-break fm-break-recto">
                <div class="page content-page" id="prologue">
                    <h2>{{ labels.prologue }}</h2>
                    <div class="content-block">{% for p in prologue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
                </div>
            </div>
            {% endif %}

            <!-- CHAPTERS -->
            {% for chapter in chapters %}
                {% if chapter.image_path %}
                <div class="chapter-spread chapter-spread--image-recto">
                    <div class="page image-page">
                        <div class="image-container"><img src="{{ chapter.image_path }}" alt="Art"></div>
                    </div>
                </div>
                {% endif %}

                {% if not chapter.image_path %}<div class="chapter-spread chapter-spread--title-verso">{% endif %}
                <div class="page chapter-title-page" id="chapter-{{ loop.index }}">
                    <div class="chapter-title-content">
                        <span class="chapter-number">{{ labels.chapter }} {{ loop.index }}{{ suffix }}</span>
                        <h1 lang="{{ html_lang }}" style="text-transform: uppercase;">{{ chapter.heading }}</h1>
                    </div>
                </div>
                {% if not chapter.image_path %}</div>{% endif %}

                <div class="page content-page chapter-body-start-recto">
                    <div class="content-block">{% for p in chapter.content.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
                </div>
            {% endfor %}  
            
            <!-- EPILOGUE -->
            {% if epilogue_text %}
                <div class="fm-break fm-break-recto">
                    <div class="page content-page" id="epilogue">
                        <h2 id="epilogue-heading">{{ labels.epilogue }}</h2>
                        <div class="content-block">{% for p in epilogue_text.split('\n\n') %}<p>{{ p }}</p>{% endfor %}</div>
                    </div>
                </div>
            {% endif %}
        </main>

        <div class="back-matter-no-folio">
            {% if include_letter and foreword_text %}
            <div class="fm-break fm-break-recto">
                <div class="page content-page" id="foreword">
                    <h2 style="margin-bottom: 0.5em;">A Letter to the Reader</h2>
                    <div class="content-block">
                        {% for p in foreword_text.split('\n') %}
                            {% if p.strip() %}<p>{{ p }}</p>{% endif %}
                        {% endfor %}
                    </div>
                </div>
            </div>

            <div class="page blank-page"><span style="visibility:hidden">.</span></div>
            {% endif %}

            <div class="fm-break fm-break-recto">
                <div class="page toc-page" id="acknowledgments">
                    <h1 style="text-transform: uppercase; margin-bottom: 2.5em;">{{ labels.acknowledgments }}</h1>
                    <div class="ack-grid">
                        {% for name in ack_names %}
                            <div class="ack-item">{{ name }}</div>
                        {% endfor %}
                    </div>
                </div>
            </div>
            <div class="page blank-page"><span style="visibility:hidden">.</span></div>
        </div>
    </body>
    </html>
    """)
    
    lambda_root = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
    fonts_dir = os.path.join(lambda_root, 'fonts')
    if not os.path.exists(fonts_dir): fonts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fonts'))
    noto_serif_regular_uri = pathlib.Path(os.path.join(fonts_dir, 'NotoSerif-Regular.ttf')).as_uri()
    noto_serif_italic_uri = pathlib.Path(os.path.join(fonts_dir, 'NotoSerif-Italic.ttf')).as_uri()
    noto_serif_bold_uri = pathlib.Path(os.path.join(fonts_dir, 'NotoSerif-Bold.ttf')).as_uri()
    font_config = f"""@font-face{{font-family:'NotoSerif';src:url('{noto_serif_regular_uri}');}}@font-face{{font-family:'NotoSerif';font-style:italic;src:url('{noto_serif_italic_uri}');}}@font-face{{font-family:'NotoSerif';font-weight:bold;src:url('{noto_serif_bold_uri}');}}"""

    main_css_string = """
    @page { size: 139.7mm 215.9mm; margin: 20mm; @bottom-center { content: none; } }
    /*
      Numbered body: one named page (main-flow) so folio increments on EVERY sheet,
      including blanks WeasyPrint inserts for recto/verso (those pages stay on main-flow).
    */
    main.book-numbered-body { page: main-flow; counter-reset: page-num 0; }
    main.book-numbered-body .fm-break,
    main.book-numbered-body .chapter-spread,
    main.book-numbered-body .page { page: main-flow; }
    @page main-flow {
        counter-increment: page-num;
        @bottom-center {
            content: counter(page-num);
            font-family: 'NotoSerif', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif;
            font-size: 9pt;
        }
    }
    @page main-flow :blank {
        @bottom-center {
            content: counter(page-num);
            font-family: 'NotoSerif', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif;
            font-size: 9pt;
        }
    }
    @page frontmatter { @bottom-center { content: none; } }
    @page back-unnumbered { @bottom-center { content: none; } }
    body { font-family: 'NotoSerif', 'Noto Serif CJK SC', 'Noto Sans Devanagari', 'Noto Sans', serif; font-size: 8pt; line-height: 1.6; }
    .title-page, .print-date-page, .toc-page, .frontmatter-blank, #preface { page: frontmatter; }
    .back-matter-no-folio, .back-matter-no-folio .page, .back-matter-no-folio .content-page, .back-matter-no-folio .toc-page { page: back-unnumbered; }
    /* Front/back matter recto-verso (WeasyPrint: page-break-before left = verso, right = recto — see WeasyPrint#241) */
    .fm-break { display: block; margin: 0; padding: 0; }
    .fm-break-recto { page-break-before: right; }
    .fm-break-verso { page-break-before: left; }
    /*
     Chapter spread: image recto → title verso → body recto.
     Wrappers carry breaks; .chapter-title-page is flex so breaks stay on wrappers.
    */
    .chapter-spread { display: block; margin: 0; padding: 0; }
    .chapter-spread--image-recto { page-break-before: right; }
    .chapter-spread--title-verso { page-break-before: left; }
    .chapter-body-start-recto { page-break-before: right; }
    .page, .title-page, .print-date-page, .toc-page, .chapter-title-page, .image-page, .blank-page { page-break-after: always; position: relative; height: 100%; }
    .image-page { margin: 0; } .image-container img { max-width: 100%; max-height: 100%; object-fit: cover; margin-top: 5em; }
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
    .chapter-title-content h1 { font-size: 35pt; hyphens: auto; }
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
    
    # TOC probe CSS: disable recto/verso parity padding so TOC numbers match visible folios
    # when WeasyPrint inserts unnumbered engine blank sheets.
    toc_probe_css_string = (
        main_css_string
        + """
    .fm-break-recto,
    .fm-break-verso,
    .chapter-spread--image-recto,
    .chapter-spread--title-verso,
    .chapter-body-start-recto { page-break-before: always !important; }
    """
    )

    css = CSS(string=font_config + main_css_string)
    toc_probe_css = CSS(string=font_config + toc_probe_css_string)
    base_url = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    published_by_label = PUBLISHED_BY_LABELS.get(language.strip().lower(), PUBLISHED_BY_LABELS["english"])
    html_lang = LANG_CODES.get(language, "en")
    final_context = {"book_title": title, "labels": L, "suffix": chapter_suffix, "footer_date": footer_date, "toc_entries": toc_base, **book_data, "lang": language, "html_lang": html_lang, "dedication_title": dedication_title, "ack_names": ack_names, "foreword_text": foreword_text, "include_letter": include_letter, "published_by_label": published_by_label}

    print("PDF Engine: Pass 1 (TOC probe without parity padding)")
    toc_probe_doc = HTML(
        string=html_template.render({**final_context, "page_map": None}),
        base_url=base_url,
    ).render(stylesheets=[toc_probe_css])
    page_map = _build_page_map_from_doc(toc_probe_doc, book_data)

    print("PDF Engine: Pass 2 (TOC stabilization without parity padding)")
    toc_probe_doc_2 = HTML(
        string=html_template.render({**final_context, "page_map": page_map}),
        base_url=base_url,
    ).render(stylesheets=[toc_probe_css])
    final_page_map = _build_page_map_from_doc(toc_probe_doc_2, book_data)

    print("PDF Engine: Pass 3 (final PDF)")
    final_html = html_template.render({**final_context, "page_map": final_page_map})
    final_doc = HTML(string=final_html, base_url=base_url).render(stylesheets=[css])

    temp_pdf_path = output_path.replace(".pdf", "_temp.pdf")
    final_doc.write_pdf(temp_pdf_path)

    try:
        flatten_pdf_fonts(temp_pdf_path, output_path)
    except Exception as e:
        print(f"Flattening failed: {e}. Using unflattened.")
        os.rename(temp_pdf_path, output_path)

    return output_path, len(final_doc.pages)
