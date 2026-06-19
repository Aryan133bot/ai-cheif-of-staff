"""AI reply draft generation for the Reply Engine."""

import logging
import os

logger = logging.getLogger(__name__)

REPLY_SYSTEM_PROMPT = """You are an AI assistant drafting a professional email reply
on behalf of a busy executive. You will be given:
1. The original email (subject, sender, AND the full body)
2. The user's intent (follow_up, acknowledge, request_info, decline, custom)

CRITICAL INSTRUCTION: First, meticulously read and analyze the FULL email body. Do not just blindly reply based on the subject line. Your reply must contextually address the specific contents, questions, or context provided in the email body.

Write a concise, professional reply that:
- Matches the tone of the original email
- Addresses the key points from the body
- Is ready to send with minimal edits
- Signs off naturally (do NOT add a name — the user will add their own)

Return ONLY the reply text. No subject line, no "RE:", no extra formatting."""


def _template_reply(subject: str, sender: str, intent: str) -> str:
    sender_name = sender.split("<")[0].strip().split("@")[0].strip()
    if not sender_name or sender_name == sender:
        sender_name = "there"

    templates = {
        "follow_up": (
            f"Hi {sender_name},\n\n"
            f"Following up on your recent email. "
            f"Could you share an update on this when you get a chance?\n\n"
            f"Thanks,"
        ),
        "acknowledge": (
            f"Hi {sender_name},\n\n"
            f"Thank you for your recent email. "
            f"I've noted the details and will get back to you shortly.\n\n"
            f"Best,"
        ),
        "request_info": (
            f"Hi {sender_name},\n\n"
            f"Thanks for reaching out. "
            f"Could you provide some additional details regarding your email so I can review this properly?\n\n"
            f"Thanks,"
        ),
        "decline": (
            f"Hi {sender_name},\n\n"
            f"Thank you for your recent email. "
            f"Unfortunately, I won't be able to proceed with this at the moment. "
            f"I'll reach out if anything changes.\n\n"
            f"Best regards,"
        ),
    }
    return templates.get(intent, templates["follow_up"])


def generate_reply_text(
    original_subject: str,
    original_sender: str,
    original_body: str,
    reply_intent: str = "follow_up",
) -> tuple[str, str, float]:
    """
    Generate a reply draft using the best available LLM.
    Returns (draft_text, model_used, confidence).
    """
    user_message = (
        f"ORIGINAL EMAIL:\n"
        f"From: {original_sender}\n"
        f"Subject: {original_subject}\n\n"
        f"{original_body[:2000]}\n\n"
        f"---\n"
        f"REPLY INTENT: {reply_intent}\n\n"
        f"Draft a professional reply."
    )

    last_error = ""

    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if gemini_key and gemini_key.lower() != "your-gemini-key-here":
        try:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
            
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=f"{REPLY_SYSTEM_PROMPT}\n\n---\n\n{user_message}",
                    )
                    return response.text.strip(), model_name, 0.85
                except Exception as e:
                    if "503" in str(e) or "UNAVAILABLE" in str(e):
                        logger.warning("Model %s is overloaded, trying next model...", model_name)
                        continue
                    raise e
                    
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
            return response.content[0].text.strip(), "claude-haiku-4-5-20251001", 0.85
        except Exception as e:
            logger.error("Claude reply generation failed: %s — using template fallback", e)
            last_error = f"Claude Error: {str(e)}"

    logger.info("No valid AI API key — using template-based reply fallback")
    
    return _template_reply(original_subject, original_sender, reply_intent), "template", 0.5
