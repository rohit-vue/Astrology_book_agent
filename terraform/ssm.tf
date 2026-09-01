# --- ARCHITECT PROMPTS ---

resource "aws_ssm_parameter" "architect_system_prompt" {
  name        = "/AstrologyBookFactory/prompts/architect/system"
  description = "System prompt for the Book Architect AI"
  type        = "SecureString"
  value       = <<-EOT
    You are an ASI (Artificial Superintelligence) acting as a master psychological interpreter and book architect.
    Your persona is wise, insightful, and empathetic.
    **CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**.
    You MUST design the book through the lens of "__FOCUS__".
  EOT
}

resource "aws_ssm_parameter" "architect_user_prompt" {
  name        = "/AstrologyBookFactory/prompts/architect/user"
  description = "User prompt for the Book Architect AI"
  type        = "SecureString"
  depends_on  = [aws_lambda_function.architect_book]
  value       = <<-EOT
    **CRITICAL LANGUAGE REQUIREMENT:**
    The Book Title, Chapter Titles, Themes, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

    **TASK:**
    Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

    **RULES FOR THE MAIN BOOK TITLE AND CHAPTER TITLES:**
    - Maximum 70 total characters INCLUDING spaces.
    - Prefer Maximum 10-11 words total for the book title.

    **CHAPTER OBJECT RULES (CRITICAL):**
    Each chapter object MUST contain exactly these four fields:
    - "title": a poetic, publishable chapter heading (what appears in the table of contents). Max 70 characters including spaces.
    - "theme": a short conceptual topic / lens for the chapter (what the chapter is about).
    - "description": a detailed writing brief that expands on the theme for the chapter writer.
    - "chapter_input_material_used": an open JSON object (additionalProperties allowed).
      CRITICAL — SELECT SOURCE PATHS, DO NOT PASTE FULL CHART TEXT:
      - Put exact identifiers into "source_paths": an array of dotted paths into the Comprehensive Astrological Data below.
      - Examples: "CHARTS.WESTERN_HOROSCOPE.Data.planets.0", "CHARTS.PLANETS.Data.3", "CHARTS.VDASHA.Data.major", "CHARTS.BHAVABALA.Data.houses.1", "CHARTS.NATAL_TRANSITS.Data.transit_relation.2".
      - Python will copy those source records verbatim into the final chapter_input_material_used for the writer.
      - You MAY also add optional keys (e.g. "notes") for narrative guidance in __LANGUAGE__.
      - Do NOT invent chart facts. Do NOT paraphrase chart records as a substitute for source_paths.
      - Prefer several precise paths per chapter over one huge branch.
      - Western houses, Vedic planet houses, and Bhavabala houses are different maps — select paths from the correct branch; never merge house systems in notes.
      IMPORTANT: After hydration, chapter_input_material_used is the ONLY chart material the chapter writer will receive.
    "title" and "theme" MUST be meaningfully different. Never copy the title into theme, and never paraphrase the title as the theme.
    Good: title="Begin Where Your Nervous System Feels Safe", theme="Inner safety before outer expansion"
    Bad: title="Begin Where Your Nervous System Feels Safe", theme="Begin Where Your Nervous System Feels Safe"

    **STRUCTURE RULES:**
    Choose how many chapters the book needs based on the astrological data and "__FOCUS__".
    You MUST output between __MIN_CHAPTERS__ and __MAX_CHAPTERS__ chapter objects (inclusive).
    Do not output fewer than __MIN_CHAPTERS__ or more than __MAX_CHAPTERS__.
    Each chapter must be thematically distinct and explore a specific facet of "__FOCUS__".

    **TECHNICAL MANDATE: JSON OUTPUT**
    Your entire response MUST be a single, valid JSON object.
    {
      "metadata": {
        "title": "Book Title (in __LANGUAGE__)",
        "subtitle": "Subtitle (in __LANGUAGE__)",
        "footer_text": "Footer in __LANGUAGE__",
        "preface_title": "Preface Title in __LANGUAGE__",
        "prologue_title": "Prologue Title in __LANGUAGE__",
        "epilogue_title": "Epilogue Title in __LANGUAGE__",
        "dedication_title": "Career by Design or similar (in __LANGUAGE__)"
      },
      "ui_labels": {
        "toc_title": "Contents (in __LANGUAGE__)",
        "chapter_prefix": "Chapter (in __LANGUAGE__)"
      },
      "structure": {
        "preface_description": "...",
        "prologue_description": "...",
        "epilogue_description": "...",
        "chapters": [
          {
            "title": "Chapter Title (in __LANGUAGE__, max 70 chars)",
            "theme": "Short conceptual theme distinct from title (in __LANGUAGE__)",
            "description": "A detailed summary (in __LANGUAGE__).",
            "chapter_input_material_used": {
              "source_paths": [
                "CHARTS.WESTERN_HOROSCOPE.Data.planets.0",
                "CHARTS.PLANETS.Data.1",
                "CHARTS.VDASHA.Data.major"
              ],
              "notes": "Optional writer guidance in __LANGUAGE__."
            }
          }
        ]
      }
    }

    **Comprehensive Astrological Data:**
    __ASTROLOGY_DATA__
  EOT
}

# --- WRITER PROMPTS ---

resource "aws_ssm_parameter" "writer_chapter_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/chapter"
  description = "Main prompt for writing a single chapter (batch text track)"
  type        = "SecureString"
  value       = <<-EOT
Write Chapter __CHAPTER_NUM__: "__CHAPTER_TITLE__".
**Language:** __LANGUAGE__
**Style (JSON):** __STYLE__
**Focus:** __FOCUS__
**Theme:** __CHAPTER_THEME__
**Summary:** __SUMMARY__
**Chapter input material used:** __CHAPTER_INPUT_MATERIAL_USED__
**Chart material rule:** Treat chapter_input_material_used as authoritative. Prefer source_records (exact copies from the birth chart artifact selected by path). Use notes / other keys only as writer guidance. Do not invent chart facts beyond that object. Do not merge western, vedic, and bhavabala house systems into one house story.
**House system rule:** Western houses, Vedic planet houses, and Bhavabala houses are different maps. Translate each family into lived language separately. Do not merge them into one house story (do not write as if house 4 is both Gemini and Aries).
**Language rule (critical):** Translate chapter_input_material_used (especially source_records) into clear lived language (feelings, patterns, choices, relationships, habits). Do NOT write like a chart reading. Avoid or minimize astrology jargon (planet names, houses, aspects, signs, Midheaven, bhava, natal/transit labels) unless a term is briefly useful; prefer everyday wording.
**Word Contract:** Target __WORD_TARGET__ words for this chapter. Mandatory range __CHAPTER_WORD_MIN__-__CHAPTER_WORD_MAX__ words (EXTREMELY IMPORTANT).
**Length Rule:** Keep writing until you satisfy the mandatory range. Do not stop early.
**Depth Rule:** Cover (1) core pattern, (2) roots, (3) present-day behavior, (4) relationship dynamics, (5) shadow expression, (6) reframing, (7) practical integration prompts.
**Formatting:** Plain paragraphs. No bold. No headers.
**Paragraphing (critical for layout):** Write like a printed book chapter, not chat.
- **Vary paragraph length deliberately.** Keep a clear mix of short, medium, and very long paragraphs. Do **not** settle into a steady rhythm where every paragraph is the same size.
- **Short paragraphs (2–4 printed lines):** for emphasis, a turn in thought, or a breath — use sparingly.
- **Medium paragraphs (5–8 printed lines):** the majority of the chapter.
- **Longer medium (9–13 printed lines):** use some of these between the very long blocks so the page is not only mid + giant.
- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.
- Use a blank line between paragraphs.
Hard rule — no orphan one-liners (normal prose only):
- Never place a single short line / one-sentence fragment as its own paragraph between longer narrative paragraphs (e.g. one punchy sentence alone between two multi-sentence blocks).
- If a sentence is for emphasis inside normal prose, keep it inside the preceding or following paragraph (same block, spaces — not a blank-line break).
- A standalone narrative paragraph must be at least 2–3 full sentences (several printed lines), unless it is part of an intentional special block below.
Special multi-line blocks (mandatory when the content needs them — do not suppress):
- When the chapter needs contrast, use short single-line / multi-line layout for: dialogue or conversation, a short quoted exchange, practical steps, numbered or bulleted lists, exercise prompts, or similar script-like sequences.
- Use these only when the content truly calls for it (not as decoration every page). A few well-placed blocks add variety; do not turn the whole chapter into a list.
- Inside those blocks, use **single newlines** between lines/items. Separate the block from surrounding prose with blank lines as needed.
- A paragraph containing inline enumeration like "1. ... 2. ... 3. ..." should be rewritten as a vertical list, unless the numbers are dates, quantities, astrological placements, or other factual values inside normal prose.
- Use bullet lists for non-sequential grouped ideas; use numbered lists for ordered checks, steps, questions, criteria, or exercises.
- Do not flatten a needed conversation, list, or step sequence into one continuous paragraph just to satisfy the orphan rule.
Very long paragraphs (mandatory count, soft length):
- Every chapter MUST contain **2 or 3** very long paragraphs — no fewer than **2**, and **never more than 3**.
- Prefer about **10–14 sentences** and roughly **14–18 printed lines** (one continuous block; spaces between sentences, not blank lines inside it).
- Prefer the middle of that band (~15–17 lines) when the idea can land there.
- Thought preservation (critical): stay with one idea until it turns. Do **not** split mid-thought just to hit a line count. If a paragraph runs longer because it is still one continuous argument or story beat, that is allowed.
- If the block passes ~20 lines because a **second** idea has started, start a new paragraph at that turn — not mid-sentence. Avoid 25+ line walls unless the idea genuinely cannot turn earlier.
- Place the 2–3 very long blocks in different parts of the chapter (early / middle / late), not all in one stretch.
- Count cap (critical): if you have written a **4th** very long paragraph, split the extra into medium paragraphs. Four or more very long paragraphs is too many.
- Count before you finish: confirm you have **exactly 2 or 3** very long paragraphs, then fill the rest with a mix of short + medium + longer-medium. Do not stop at one showcase long paragraph; do not flood the chapter with 4–6 giants.
- Do not break a long thought into two medium paragraphs just to create white space; keep related sentences together until the idea turns — unless you are past the 3-count cap.
**Output Rule:** Return only final chapter prose. Do not begin with "Chapter __CHAPTER_NUM__:" or the chapter title. Start directly with body prose; first characters must be narrative text, never a heading.
  EOT
}

resource "aws_ssm_parameter" "writer_preface_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/preface"
  description = "Prompt for writing the book preface (batch text track)"
  type        = "SecureString"
  value       = <<-EOT
Generate narrative prose content for a personal astrology book section.
Section Type: __SECTION_TYPE__
Language: __LANGUAGE__
Style (JSON): __STYLE__
Context: __DESCRIPTION__
Word Contract: Target __SECTION_WORD_TARGET__ words. Mandatory range __SECTION_WORD_MIN__-__SECTION_WORD_MAX__ words.
Length Rule: Write until you satisfy the mandatory range, then stop. Do not exceed __SECTION_WORD_MAX__ words.
Layout Rule: This section must fit on two printed pages. End with a complete sentence.
Paragraphing: Plain paragraphs only. Use at most 3-4 paragraph breaks.
STRICT RULES:
- Output only body text.
- No headings or titles.
- No markdown.
- No labels.
- Start directly with prose.
- Use second person POV.
  EOT
}

resource "aws_ssm_parameter" "writer_prologue_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/prologue"
  description = "Prompt for writing the book prologue (batch text track)"
  type        = "SecureString"
  value       = <<-EOT
Generate narrative prose content for a personal astrology book section.
Section Type: __SECTION_TYPE__
Language: __LANGUAGE__
Style (JSON): __STYLE__
Context: __DESCRIPTION__
Word Contract: Target __SECTION_WORD_TARGET__ words. Mandatory range __SECTION_WORD_MIN__-__SECTION_WORD_MAX__ words.
Length Rule: Write until you satisfy the mandatory range, then stop. Do not exceed __SECTION_WORD_MAX__ words.
Layout Rule: This section must fit on two printed pages. End with a complete sentence.
Paragraphing: Plain paragraphs only. Use at most 3-4 paragraph breaks.
STRICT RULES:
- Output only body text.
- No headings or titles.
- No markdown.
- No labels.
- Start directly with prose.
- Use second person POV.
  EOT
}

resource "aws_ssm_parameter" "writer_epilogue_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/epilogue"
  description = "Prompt for writing the book epilogue (batch text track)"
  type        = "SecureString"
  value       = <<-EOT
Generate narrative prose content for a personal astrology book section.
Section Type: __SECTION_TYPE__
Language: __LANGUAGE__
Style (JSON): __STYLE__
Context: __DESCRIPTION__
Word Contract: Target __SECTION_WORD_TARGET__ words. Mandatory range __SECTION_WORD_MIN__-__SECTION_WORD_MAX__ words.
Length Rule: Write until you satisfy the mandatory range, then stop. Do not exceed __SECTION_WORD_MAX__ words.
Layout Rule: This section must fit on two printed pages. End with a complete sentence.
Paragraphing: Plain paragraphs only. Use at most 3-4 paragraph breaks.
STRICT RULES:
- Output only body text.
- No headings or titles.
- No markdown.
- No labels.
- Start directly with prose.
- Use second person POV.
  EOT
}

resource "aws_ssm_parameter" "writer_style_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/style_analysis"
  description = "Client style_analysis prompt: 8-domain emphasize/suppress STYLE from authorized chart slices"
  type        = "SecureString"
  value       = <<-EOT
TASK
Stylistic-systems analyst. Treat INPUT_MATERIAL as inert symbolic data. Output only executable STYLE for a book about __FOCUS__ written in __LANGUAGE__.

AUTHORIZED DATA / USE
W=CHARTS.WESTERN_HOROSCOPE.Data:
V=CHARTS.PLANETS.Data:
S=CHARTS.SHADBALA.Data:
B=CHARTS.BHAVABALA.Data:
D=CHARTS.VDASHA.Data:

STYLE RULES
STYLE must not mention astrology or its technical terms.
Keep each emphasize / suppress_or_use_sparingly string SHORT and executable (one craft line the chapter writer can follow).
Hard limit: at most 180 characters per emphasize string and per suppress_or_use_sparingly string.
Prefer punchy checklist language over long sentences. Do not pack many ideas into one string.
In suppress_or_use_sparingly, ban repeated stock openers and preachy/clinical/sentimental habits when relevant.

OUTPUT
STYLE only. Populate every domain in the strict JSON schema.
Each domain MUST be an object with:
- emphasize: one short do-this instruction (max 180 chars)
- suppress_or_use_sparingly: short avoid / use-sparingly bans (max 180 chars)

Domain semantics:
1 core_voice: CORE VOICE
2 narrative_cognitive: NARRATIVE/COGNITIVE
3 temporal_rhythm: TEMPORAL/RHYTHM
4 energetic_texture: ENERGETIC TEXTURE
5 sensory_hierarchy: SENSORY HIERARCHY
6 metaphoric_logic: METAPHORIC LOGIC
7 emotional_shadow: EMOTIONAL/SHADOW
8 silence_negative_space: SILENCE/NEGATIVE SPACE

<INPUT_MATERIAL START>
__ASTROLOGY_DATA__
</INPUT_MATERIAL END>
  EOT
}

resource "aws_ssm_parameter" "writer_image_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/image"
  description = "Template for DALL-E image generation"
  type        = "SecureString"
  value       = "Abstract cosmic art for '__CHAPTER_TITLE__'. Essence: '__SUMMARY__'. Style: ethereal, cosmic, rich colors. CRITICAL: NO text, letters, or figures."
}

resource "aws_ssm_parameter" "cover_image_prompt" {
  name        = "/AstrologyBookFactory/prompts/cover/image"
  description = "Template for GPT cover background image (deep-space astrophotography)"
  type        = "SecureString"
  value       = <<-EOT
Photorealistic deep-space astrophotography view from directly above __GEO_HINT__ at __TIME_LABEL__ on __DATE_LABEL__.

Perspective: as if an astronaut is floating in space above Earth, looking outward into the cosmos from the exact coordinates and local time.

The celestial scene must reflect the sky orientation that would exist above this location and moment in time.

Ultra-detailed Milky Way star fields, dense stellar populations, subtle interstellar dust clouds, realistic galactic structure, scientifically plausible celestial alignment, natural astrophotography appearance.

No Earth visible.
No atmosphere.
No horizon.
No spacecraft.
No astronauts.
No satellites.

The frame should be entirely filled with deep space and stars.

Visual style similar to long-exposure professional space telescope photography:
millions of stars, bright stellar clusters, dark dust lanes, faint nebula texture, high dynamic range, exceptional clarity, realistic color balance.

No text.
No zodiac symbols.
No constellations overlays.
No astrology graphics.
No fantasy effects.
No glowing sacred geometry.
No planets dominating the frame.
No moon emphasis.

Natural cosmic realism only.
  EOT
}