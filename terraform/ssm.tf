# --- ARCHITECT PROMPTS ---

resource "aws_ssm_parameter" "architect_system_prompt" {
  name        = "/AstrologyBookFactory/prompts/architect/system"
  description = "System prompt for the Book Architect AI"
  type        = "SecureString"
  value       = <<-EOT
    You are an ASI (Artificial Superintelligence) acting as a master psychological interpreter and book architect.
    Your persona is wise, insightful, and empathetic.
    **CRITICAL INSTRUCTION:** You MUST output your response in **__LANGUAGE__**.
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
    - "chapter_input_material_used": a JSON object with "chapter_focus" selected from the provided chart data.
      Do NOT invent chart facts. Copy/condense real factors from the Comprehensive Astrological Data.
      IMPORTANT: chapter_focus is the ONLY chart material the chapter writer will receive (no chart_snapshot, no full chart dump). Make cues dense and self-sufficient.
      chapter_focus MUST include:
      - "rationale": why these cues are primary for this chapter
      - "western_cues": dense strings from WESTERN_HOROSCOPE. Prefix EVERY cue with "western". Use WESTERN_HOROSCOPE signs/houses only. For planets include name, sign, house, norm_degree, full_degree, is_retro. For aspects include type and orb. For angles include degree when present.
      - "planets_cues": dense strings from PLANETS. Prefix EVERY cue with "vedic". Include sign, nakshatra, house, awastha, isRetro, normDegree, fullDegree when present. Vedic houses/signs will not match western houses.
      - "shadbala_cues": array of compact strings from SHADBALA strengths
      - "bhavabala_cues": array of compact strings from BHAVABALA / house strengths. Prefix EVERY cue with "bhavabala" and the Sanskrit name (Sukha, Putra, Dhana, etc.). Never treat these as western houses.
      - "vdasha_cues": copy planet + period dates only from VDASHA. Do not add psychological meanings.
      - "transit_cues": dense strings from NATAL_TRANSITS for today (transit planet, aspect, natal point, signs/houses, retro when available)
      HOUSE SYSTEM RULE (CRITICAL):
      Western houses, Vedic PLANETS houses, and Bhavabala houses are different maps. Never merge them (e.g. do not imply western house 4 and bhavabala house 4 Sukha are the same sign).
      BOOK COVERAGE + UNIQUENESS (CRITICAL):
      - Coverage: across ALL chapters combined, significant chart factors must appear at least once: every WESTERN planet (including Node, Chiron, Part of Fortune, Lilith if present), Ascendant, Midheaven, every natal aspect with orb <= 2.0, every PLANETS body (including Rahu, Ketu, Ascendant), SHADBALA strongest planet and any not-strong planet, BHAVABALA strongest and weakest houses (use Sanskrit names), the full current VDASHA stack, every outer-planet transit (Jupiter/Saturn/Uranus/Neptune/Pluto) that is present, and any transit to ASC/MC/IC/DC.
      - Primary home: assign each significant factor to ONE primary chapter (the theme it actually serves).
      - Reuse: overlap is allowed only for book-level anchors (the current dasha stack, and at most 1-2 signature transits for the day). Other cues must not be copied into more than 2-3 chapters.
      - Soft caps per family when that family matters: western_cues 6-10, planets_cues 4-8, transit_cues 4-8, shadbala_cues 2-6, bhavabala_cues 2-6, vdasha_cues 2-5.
      - Empty arrays only when that family truly has nothing relevant. Prefer at least one cue in western_cues.
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
              "chapter_focus": {
                "rationale": "Why these chart factors matter for this chapter (in __LANGUAGE__).",
                "western_cues": ["western Sun Aquarius house 10 norm_degree 6.12 full_degree 306.12 is_retro=false", "western Sun Conjunction Midheaven orb 0.8"],
                "planets_cues": ["vedic Sun Capricorn nakshatra Shravan house 10 awastha Yuva normDegree 6.12 fullDegree 306.12 isRetro=false"],
                "shadbala_cues": ["Sun strong 118% of minimum"],
                "bhavabala_cues": ["bhavabala house 4 Sukha Aries 46% of baseline", "bhavabala house 5 Putra Taurus weakest"],
                "vdasha_cues": ["Current major Jupiter 22-8-2024 to 23-8-2031"],
                "transit_cues": ["Transit Uranus Gemini Conjunction natal IC house 4 retro=false"]
              }
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
**Style:** __STYLE__
**Focus:** __FOCUS__
**Theme:** __CHAPTER_THEME__
**Summary:** __SUMMARY__
**Chapter input material used:** __CHAPTER_INPUT_MATERIAL_USED__
**Chart focus rule:** Ground this chapter ONLY in chapter_input_material_used.chapter_focus. Those dense chart cues are the sole chart material. Do not invent extra chart factors beyond that material.
**House system rule:** Western houses, Vedic planet houses, and Bhavabala houses are different maps. Translate each family into lived language separately. Do not merge them into one house story (do not write as if house 4 is both Gemini and Aries).
**Language rule (critical):** Translate chapter_input_material_used.chapter_focus into clear lived language (feelings, patterns, choices, relationships, habits). Do NOT write like a chart reading. Avoid or minimize astrology jargon (planet names, houses, aspects, signs, Midheaven, bhava, natal/transit labels) unless a term is briefly useful; prefer everyday wording.
**Word Contract:** Target __WORD_TARGET__ words for this chapter. Mandatory range __CHAPTER_WORD_MIN__-__CHAPTER_WORD_MAX__ words (EXTREMELY IMPORTANT).
**Length Rule:** Keep writing until you satisfy the mandatory range. Do not stop early.
**Depth Rule:** Cover (1) core pattern, (2) roots, (3) present-day behavior, (4) relationship dynamics, (5) shadow expression, (6) reframing, (7) practical integration prompts.
**Formatting:** Plain paragraphs. No bold. No headers.
**Paragraphing (critical for layout):** Write like a printed book chapter, not chat.
- **Vary paragraph length deliberately.** Mix shorter paragraphs (often **3-5 sentences**, about **2–3 printed lines**) with medium and longer ones. Do **not** settle into a steady rhythm where every paragraph is the same size.
- **Short paragraphs are allowed** for emphasis, a turn in thought, or a breath between ideas—use them **sometimes**, not after every sentence.
- Longer paragraphs are fine when the idea needs room; neighbor paragraphs may be much shorter so the page does not look like uniform blocks.
- Use **single newlines** only when you must break a long paragraph; prefer joining sentences in the same paragraph with spaces.
- Use **double newlines (blank line)** ONLY between **major sections**. **At most 8–10 double-newlines in the whole chapter.**
**Output Rule:** Return only final chapter prose. Do not begin with "Chapter __CHAPTER_NUM__:" or the chapter title. Start directly with body prose; first characters must be narrative text, never a heading.
  EOT
}

resource "aws_ssm_parameter" "writer_style_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/style_analysis"
  description = "Client style_analysis prompt: 8-field STYLE from authorized chart slices"
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

OUTPUT
STYLE only. Populate every field in the strict JSON schema. Domain semantics:
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