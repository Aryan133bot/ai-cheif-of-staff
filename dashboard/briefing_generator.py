"""AI morning briefing generator for the dashboard."""

import logging
import os
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """You are the AI Chief of Staff. You are providing a morning executive briefing to your principal.
You will be provided with a JSON string containing the most recent unread/priority emails and the most urgent pending tasks.
Write a concise, engaging, and professional 2-3 paragraph summary. 
Start with a pleasant greeting (e.g., "Good morning. Here is your briefing for today.").
Highlight the most critical items that need their attention. 
Do not output raw JSON, just a beautifully formatted markdown response."""

def generate_daily_briefing(emails: list[dict], tasks: list[dict]) -> str:
    """Generate a markdown briefing using Gemini based on recent emails and tasks."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("No GEMINI_API_KEY set. Falling back to default briefing.")
        return "Good morning! Please set your Gemini API key in the environment to receive a personalized briefing."

    import json
    data_payload = {
        "recent_priority_emails": [
            {
                "subject": e.get("subject"),
                "sender": e.get("sender"),
                "category": e.get("category"),
            } for e in emails[:5]
        ],
        "urgent_tasks": [
            {
                "title": t.get("title"),
                "priority": t.get("priority"),
                "due_date": t.get("deadline_date"),
            } for t in tasks[:5]
        ]
    }
    
    user_message = json.dumps(data_payload, indent=2)

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{BRIEFING_SYSTEM_PROMPT}\n\n---\n\n{user_message}",
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Failed to generate briefing: %s", e)
        return "Good morning! I was unable to generate your briefing at this time due to an error."
