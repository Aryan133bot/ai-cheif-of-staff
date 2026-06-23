import os
import requests
from bs4 import BeautifulSoup
import pymupdf  # PyMuPDF
from google import genai
from pydantic import BaseModel, Field

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class KnowledgeFact(BaseModel):
    title: str = Field(description="A short, descriptive title for the fact (max 5 words).")
    content: str = Field(description="The detailed fact, rule, preference, or piece of knowledge.")

class ExtractedKnowledge(BaseModel):
    facts: list[KnowledgeFact]

def _extract_with_gemini(text: str, context: str) -> list[dict]:
    prompt = f"""
    You are an AI Chief of Staff helping to build a Knowledge Base.
    Extract the most important, reusable facts, rules, or preferences from the provided text.
    Ignore conversational filler, greetings, or highly specific temporary details (like "I am free next Tuesday").
    Focus on evergreen knowledge (e.g., standard pricing, company policies, communication preferences, standard procedures).
    
    Context/Source: {context}
    
    Text:
    {text}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': list[KnowledgeFact],
        },
    )
    
    try:
        # Pydantic validation handles parsing
        facts = response.parsed
        return [{"title": f.title, "content": f.content} for f in facts]
    except Exception as e:
        print(f"Error extracting knowledge: {e}")
        return []

def extract_facts_from_email(email_body: str) -> list[dict]:
    """Extracts facts from an email thread."""
    return _extract_with_gemini(email_body, "Email communication")

def ingest_url(url: str) -> list[dict]:
    """Scrapes a URL and extracts facts."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove scripts, styles
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
            
        text = soup.get_text(separator=' ', strip=True)
        # Limit text length just in case it's huge
        text = text[:15000]
        return _extract_with_gemini(text, f"Website URL: {url}")
    except Exception as e:
        print(f"Failed to scrape URL {url}: {e}")
        return []

def ingest_document(file_content: bytes, filename: str) -> list[dict]:
    """Parses a document (PDF) and extracts facts."""
    try:
        text = ""
        if filename.lower().endswith(".pdf"):
            doc = pymupdf.Document(stream=file_content, filetype="pdf")
            for page in doc:
                text += page.get_text()
        else:
            # Assume text/markdown
            text = file_content.decode('utf-8', errors='ignore')
            
        text = text[:15000] # Limit for now
        return _extract_with_gemini(text, f"Document: {filename}")
    except Exception as e:
        print(f"Failed to parse document {filename}: {e}")
        return []
