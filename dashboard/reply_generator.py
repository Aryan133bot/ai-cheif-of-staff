"""AI reply draft generation for the Reply Engine."""

import logging
import os

logger = logging.getLogger(__name__)

import json

REPLY_SYSTEM_PROMPT = """You are an AI assistant drafting a professional email reply
on behalf of a busy executive. You will be given:
1. The original email (subject, sender, AND the full body)
2. The user's intent (follow_up, acknowledge, request_info, decline, custom)

CRITICAL INSTRUCTION: First, meticulously read and analyze the FULL email body.
Write a concise, professional reply that matches the tone of the original email, addresses key points, and signs off naturally (without adding a name).

RISK GATING: You must evaluate the risk of automatically sending this reply without human review.
Set "auto_send_eligible" to true ONLY IF this is a routine administrative item (e.g., meeting confirmation, receipt acknowledgment, polite decline of cold outreach).
Set "auto_send_eligible" to false for VIP clients, complex negotiations, financial decisions, ambiguous requests, or anything high-stakes.

You MUST return your response as a valid JSON object with EXACTLY two keys:
{
  "draft_text": "The actual email text to send",
  "auto_send_eligible": true/false
}
Do not output any markdown formatting around the JSON, just the raw JSON object."""


def _template_reply(subject: str, sender: str, intent: str, last_error: str = "") -> tuple[str, bool]:
    sender_name = sender.split("<")[0].strip().split("@")[0].strip()
    if not sender_name or sender_name == sender:
        sender_name = "there"

    templates = {
        "follow_up": (
            f"Hi {sender_name},\n\n"
            f"Following up on your recent email. Could you share an update on this when you get a chance?\n\n"
            f"Thanks,"
        ),
        "acknowledge": (
            f"Hi {sender_name},\n\n"
            f"Thank you for your recent email. I've noted the details and will get back to you shortly.\n\n"
            f"Best,"
        ),
        "request_info": (
            f"Hi {sender_name},\n\n"
            f"Thanks for reaching out. Could you provide some additional details regarding your email so I can review this properly?\n\n"
            f"Thanks,"
        ),
        "decline": (
            f"Hi {sender_name},\n\n"
            f"Thank you for your recent email. Unfortunately, I won't be able to proceed with this at the moment. I'll reach out if anything changes.\n\n"
            f"Best regards,"
        ),
    }
    
    text = templates.get(intent, templates["follow_up"])
    if last_error:
        text += f"\n\n[DEBUG ERROR: {last_error}]"
        
    return text, False


def generate_reply_text(
    original_subject: str,
    original_sender: str,
    original_body: str,
    reply_intent: str = "follow_up",
    contact_role: str | None = None,
    tone_preference: str | None = None,
    knowledge_context: str | None = None,
) -> tuple[str, str, float, bool]:
    """
    Generate a reply draft using the best available LLM.
    Returns (draft_text, model_used, confidence, auto_send_eligible).
    """
    relationship_context = ""
    if contact_role and tone_preference:
        relationship_context = (
            f"RELATIONSHIP CONTEXT:\n"
            f"The sender of this email is the user's {contact_role}.\n"
            f"Ensure the tone of your reply is {tone_preference}.\n\n"
        )

    knowledge_block = ""
    if knowledge_context:
        knowledge_block = (
            f"{knowledge_context}\n\n"
            f"INSTRUCTION: Use the knowledge base facts above to accurately answer any questions in the email.\n\n"
        )

    user_message = (
        f"ORIGINAL EMAIL:\n"
        f"From: {original_sender}\n"
        f"Subject: {original_subject}\n\n"
        f"{original_body[:2000]}\n\n"
        f"---\n"
        f"{knowledge_block}"
        f"{relationship_context}"
        f"REPLY INTENT: {reply_intent}\n\n"
        f"Draft a professional reply."
    )

    last_error = ""

    # 1. Try JSON ADC / Service Account if GOOGLE_CREDENTIALS_JSON is provided
    # 2. Else try gemini_key
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    google_creds_raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    # Only use google_creds for genai if it's actually a Service Account (not an OAuth Client ID)
    google_creds = None
    if google_creds_raw and '"type": "service_account"' in google_creds_raw:
        google_creds = google_creds_raw

    if (gemini_key and gemini_key.lower() != "your-gemini-key-here") or google_creds:
        try:
            from google import genai
            from google.genai import types

            import tempfile
            if google_creds:
                cred_path = os.path.join(tempfile.gettempdir(), "google_credentials.json")
                if not os.path.exists(cred_path):
                    with open(cred_path, "w") as f:
                        f.write(google_creds)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path
                client = genai.Client()
            else:
                client = genai.Client(api_key=gemini_key)

            models_to_try = ["gemini-2.5-flash", "gemini-1.5-pro", "gemini-1.5-flash"]
            
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=f"{REPLY_SYSTEM_PROMPT}\n\n---\n\n{user_message}",
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json"
                        )
                    )
                    raw_text = response.text.strip()
                    if raw_text.startswith("```json"):
                        raw_text = raw_text[7:]
                    if raw_text.startswith("```"):
                        raw_text = raw_text[3:]
                    if raw_text.endswith("```"):
                        raw_text = raw_text[:-3]
                    raw_text = raw_text.strip()
                    data = json.loads(raw_text)
                    return data.get("draft_text", "").strip(), model_name, 0.85, data.get("auto_send_eligible", False)
                except Exception as e:
                    logger.warning("Model %s failed: %s", model_name, e)
                    last_error = str(e)
                    continue
            
            if last_error:
                raise Exception(last_error)
        except Exception as e:
            logger.error("Gemini reply generation failed: %s — trying next provider", e)
            last_error = f"Gemini Error: {str(e)}"

    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if anthropic_key and anthropic_key.lower() != "your-anthropic-key-here" and anthropic_key.startswith("sk-ant-"):
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=REPLY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            try:
                data = json.loads(response.content[0].text.strip())
                return data.get("draft_text", "").strip(), "claude-haiku-4-5-20251001", 0.85, data.get("auto_send_eligible", False)
            except json.JSONDecodeError:
                return response.content[0].text.strip(), "claude-haiku-4-5-20251001", 0.85, False
        except Exception as e:
            logger.error("Claude reply generation failed: %s — using template fallback", e)
            last_error = f"Claude Error: {str(e)}"

    logger.info("No valid AI API key — using template-based reply fallback")
    
    text, auto = _template_reply(original_subject, original_sender, reply_intent, last_error)
    return text, "template", 0.5, auto
