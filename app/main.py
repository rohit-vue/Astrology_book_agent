# app/main.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles 
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from app.book_writer import generate_astrology_book
from app.book_pdf_exporter import save_book_as_pdf
from app.astrology_api_client import get_natal_chart_data
from app.prompt_builder import build_data_extraction_prompt 
from dotenv import load_dotenv
import os
import re
import traceback
import json
from openai import AsyncOpenAI, RateLimitError
from datetime import datetime 

load_dotenv()

openai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_TEXT = "gpt-5.2-2025-12-11"

app = FastAPI(
    title="Personal Portrait Generator",
    description="An API to generate a personalized interpretation book based on a plain text birth prompt.",
    version="3.0.0"
)

app.mount("/generated_books", StaticFiles(directory="generated_books"), name="generated_books")

class BookRequest(BaseModel):
    birth_date: str = Field(..., description="The user's birth date, e.g., '1986-09-04'")
    birth_time: str = Field(..., description="The user's birth time, e.g., '15:30'")
    birth_location: str = Field(..., description="The user's birth location, e.g., 'West Palm Beach, Florida'")
    target_word_count: int = Field(15000, description="Desired book length: 15000, 30000, or 50000")

def sanitize_filename(text: str) -> str:
    """Removes invalid characters from a string to make it a valid filename."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", text)
    return sanitized[:50].strip().replace(' ', '_')

async def extract_birth_data_from_prompt(prompt: str) -> dict:
    """
    Uses an LLM to parse a natural language prompt into structured birth data,
    including geocoded location and the correct timezone offset.
    """
    print(f"Parsing prompt with AI: '{prompt}'")
    extraction_prompt = build_data_extraction_prompt(prompt)
    
    try:
        response = await openai.chat.completions.create(
            model=MODEL_TEXT,
            messages=[{"role": "user", "content": extraction_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0 
        )
        
        structured_data = json.loads(response.choices[0].message.content)
        print("Successfully parsed prompt into structured data:", structured_data)
        
        structured_data['tzone'] = structured_data.pop('timezone_offset')
        structured_data['lon'] = structured_data.pop('longitude')
        structured_data['lat'] = structured_data.pop('latitude')
        structured_data['minute'] = structured_data.pop('min')

        return structured_data
    except RateLimitError:
        print("Failed to parse prompt due to OpenAI quota issue.")
        raise ValueError("OpenAI API quota exceeded. Please check your billing details and plan.")
    except Exception as e:
        print(f"Failed to parse prompt with AI: {e}")
        raise ValueError("Could not understand the provided birth information. Please try again with a clear format like 'Month Day, Year, Time, City, State'.")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse('index.html')


@app.post("/generate-book/", summary="Generate a Personal Portrait Book")
async def generate_book(request: BookRequest):
    """
    Generates a complete PDF book from a simple text prompt containing birth info.
    """
    if not all([request.birth_date, request.birth_time, request.birth_location]):
        raise HTTPException(status_code=400, detail="Date, time, and location fields cannot be empty.")
    
    user_prompt = f"{request.birth_date} at {request.birth_time} in {request.birth_location}"
    
    if request.target_word_count not in [15000, 30000, 50000]:
        raise HTTPException(status_code=400, detail="Word count must be one of: 15000, 30000, 50000.")

    print(f"--- Starting Book Generation for prompt: '{user_prompt}' ---")

    try:
        birth_data = await extract_birth_data_from_prompt(user_prompt)
        natal_chart_data = await get_natal_chart_data(**birth_data)

        book_title = "The Architecture of You" 
        print(f"Generating book components for: '{book_title}'...")

        book_data = await generate_astrology_book(
            natal_chart_json=natal_chart_data,
            target_word_count=request.target_word_count
        )
        print("Book components generated successfully.")

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        books_dir = os.path.join(repo_root, "generated_books")
        os.makedirs(books_dir, exist_ok=True)

        # Align with src/generate_pdf/book_pdf_exporter: same assets/fonts via LAMBDA_TASK_ROOT,
        # birth_data shape uses "min" for footer; extract_birth_data uses "minute".
        pdf_book_data = {
            **book_data,
            "birth_data": {
                **birth_data,
                "min": birth_data.get("minute", birth_data.get("min", 0)),
            },
            "metadata": {
                "title": book_title,
                "dedication_title": "Career by Design",
            },
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{sanitize_filename(book_title)}_{timestamp}.pdf"
        print(f"Generating unique PDF: {filename}...")

        def _save_pdf():
            os.environ["LAMBDA_TASK_ROOT"] = os.path.join(repo_root, "src", "generate_pdf")
            return save_book_as_pdf(
                title=book_title,
                book_data=pdf_book_data,
                filename=filename,
                output_dir=books_dir,
                language="English",
                openai_api_key=os.getenv("OPENAI_API_KEY"),
            )

        output_pdf_path, page_count = await run_in_threadpool(_save_pdf)
        print("\n--- SUCCESS ---")
        print(f"Personalized book saved to: {output_pdf_path} ({page_count} pages)")
        
        pdf_url = f"/generated_books/{filename}"

        return {
            "title": book_title,
            "pdf_file": pdf_url,
            "preview": book_data.get('prologue_text', '') + "\n\n" + book_data.get('chapters', [{}])[0].get('content', '')[:1500] + "..."
        }

    except Exception as e:
        print(f"\n--- AN ERROR OCCURRED ---")
        print(f"An error occurred during book generation: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))