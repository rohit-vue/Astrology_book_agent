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
    The Book Title, Chapter Titles, and Descriptions MUST be written in **__LANGUAGE__**. Do not write in English unless the language is English.

    **TASK:**
    Analyze the provided astrological data. Your primary creative goal is to design a book structure that explores what this person needs to hear today, specifically through the lens of **"__FOCUS__"**.

    **RULES FOR THE MAIN BOOK TITLE AND CHAPTER TITLES:**
    - Maximum 70 total characters INCLUDING spaces.
    - Prefer Maximum 10-11 words total for the book title.
    
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
          { "title": "Chapter Title (in __LANGUAGE__)", "description": "A detailed summary (in __LANGUAGE__)." }
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
**Summary:** __SUMMARY__
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
**Output Rule:** Return only final chapter prose.
**Data:** __ASTROLOGY_DATA__
  EOT
}

resource "aws_ssm_parameter" "writer_style_prompt" {
  name        = "/AstrologyBookFactory/prompts/writer/style_analysis"
  description = "Prompt to analyze tone"
  type        = "SecureString"
  value       = "Analyze the following astrological data. Based on its core energies, describe the ideal writing tone and style for a personal book about '__FOCUS__' in **__LANGUAGE__**. Keep it concise.\n\nDATA:\n__ASTROLOGY_DATA__"
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