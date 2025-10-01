# app/astrology_api_client.py
import httpx
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

API_BASE_URL = "https://json.astrologyapi.com/v1"
USER_ID = os.getenv("ASTROLOGY_API_USER_ID")
API_KEY = os.getenv("ASTROLOGY_API_KEY")

async def get_natal_chart_data(day: int, month: int, year: int, hour: int, minute: int, lat: float, lon: float, tzone: float) -> dict:
    """
    Fetches the detailed western horoscope (natal chart) data from AstrologyAPI.com.
    """
    if not USER_ID or not API_KEY:
        raise ValueError("Astrology API credentials (USER_ID, API_KEY) are not set in the .env file.")

    api_url = f"{API_BASE_URL}/western_horoscope"
    payload = {
        "day": day,
        "month": month,
        "year": year,
        "hour": hour,
        "min": minute,
        "lat": lat,
        "lon": lon,
        "tzone": tzone,
    }
    
    auth = (USER_ID, API_KEY)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print(f"Requesting chart data from AstrologyAPI for {month}/{day}/{year}...")
            response = await client.post(api_url, auth=auth, json=payload)
            response.raise_for_status()  # Raise exception for 4xx/5xx errors
            print("Successfully received chart data.")
            return response.json()
        except httpx.HTTPStatusError as e:
            error_message = f"Error fetching chart: {e.response.status_code} - {e.response.text}"
            print(error_message)
            raise Exception(error_message)
        except Exception as e:
            error_message = f"An unexpected error occurred while contacting AstrologyAPI: {e}"
            print(error_message)
            raise Exception(error_message)