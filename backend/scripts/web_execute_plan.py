#!/usr/bin/env python3
"""
web_execute_plan.py — Bridge between Next.js control plane and Python skills.

Usage:
    python3 scripts/web_execute_plan.py --json '<json_payload>'

Payload fields:
    plan_id       str   — WebPlan DB id
    channel       str   — linkedin | gmail | whatsapp | twitter
    action_type   str   — post_text | post_image | draft_email | send_email | etc.
    payload       dict  — normalized action payload
    title         str   — human-readable plan title
    scheduled_at  str?  — ISO datetime or null

Exit codes:
    0 — success
    1 — execution error
    2 — unsupported channel/action
"""

import sys
import os
import json
import argparse
import traceback
from datetime import datetime, timezone

# Ensure repo root is on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, REPO_ROOT)
# Also add src so imports work
sys.path.insert(0, os.path.join(REPO_ROOT, 'src'))


def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).isoformat()
    print(json.dumps({"timestamp": ts, "level": level, "source": "web_execute_plan", "message": msg}), flush=True)


def generate_content_with_openai(topic: str, tone: str = "Professional", length: str = "Medium",
                                  channel: str = "linkedin", intent: str = "",
                                  hashtags=None, cta: str = "") -> str:
    """Generate post content using OpenAI Chat API. Returns generated text or falls back to topic."""
    import os
    import json
    import urllib.request
    import urllib.error

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log("OPENAI_API_KEY not set — using topic as content", "WARNING")
        return topic

    word_count = 50 if "50" in str(length) else 300 if "300" in str(length) else 150
    if hashtags is None:
        hashtags = []
    hashtag_text = f"\nHashtags to include at the end: {', '.join(hashtags)}" if hashtags else ""
    cta_text = f"\nCall to action: {cta}" if cta else ""

    system_prompt = (
        "You are an expert LinkedIn content writer. Write engaging, authentic posts that perform well. "
        "Use short paragraphs and line breaks for readability. Never use hashtags mid-post — add them at the end only."
        if channel == "linkedin" else
        f"You are an expert {channel} content writer."
    )
    user_prompt = (
        f"Write a {tone.lower()} {channel} post about: {topic}\n"
        f"Intent: {intent or 'general'}\n"
        f"Target length: approximately {word_count} words"
        f"{hashtag_text}{cta_text}\n\n"
        "Write ONLY the post content — no preamble, no quotes, just the raw post text."
    )

    data = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 900,
        "temperature": 0.75,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            content = result["choices"][0]["message"]["content"].strip()
            log(f"OpenAI generated {len(content)} chars of content")
            return content
    except Exception as e:
        log(f"OpenAI content generation failed: {e}", "WARNING")
        return topic  # Fall back to raw topic


def generate_image_with_dalle(prompt: str) -> tuple:
    """Generate image with DALL-E 3. Returns (image_bytes, mime_type) or (None, None)."""
    import os
    import json
    import urllib.request

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log("OPENAI_API_KEY not set — cannot generate image", "WARNING")
        return None, None

    data = {
        "model": "dall-e-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "response_format": "url",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read())
            image_url = result["data"][0]["url"]
            log(f"DALL-E image generated: {image_url[:80]}…")
        # Download generated image
        with urllib.request.urlopen(image_url, timeout=30) as img_resp:
            img_bytes = img_resp.read()
            return img_bytes, "image/png"
    except Exception as e:
        log(f"DALL-E image generation failed: {e}", "WARNING")
        return None, None


def execute_linkedin_post(payload: dict) -> dict:
    """Post to LinkedIn using the existing API helper."""
    try:
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper  # type: ignore

        helper = LinkedInAPIHelper()

        topic = payload.get("topic", "")
        body = payload.get("body") or payload.get("content") or ""
        hashtags = payload.get("hashtags", [])
        cta = payload.get("cta", "")
        tone = payload.get("tone", "Professional")
        length = payload.get("length", "Medium (~150 words)")
        intent = payload.get("intent", "")
        action_type = payload.get("action_type", "post_text")

        # Generate full content with OpenAI if only a topic was provided (no body)
        if topic and not body.strip():
            log(f"No body provided — generating content from topic: '{topic}'")
            body = generate_content_with_openai(
                topic=topic,
                tone=tone,
                length=length,
                channel="linkedin",
                intent=intent,
                hashtags=hashtags if isinstance(hashtags, list) else [],
                cta=cta,
            )

        content = body or topic

        # Append hashtags if not already in body (for cases where OpenAI didn't include them)
        if hashtags and isinstance(hashtags, list):
            tags_str = " ".join(f"#{str(h).lstrip('#')}" for h in hashtags)
            if tags_str and not any(f"#{str(h).lstrip('#')}" in content for h in hashtags[:1]):
                content = f"{content}\n\n{tags_str}"

        if cta and cta not in content:
            content = f"{content}\n\n{cta}"

        if action_type == "post_image":
            image_url = payload.get("imageUrl") or payload.get("image_url") or payload.get("imageFile") or ""
            image_mode = payload.get("imageMode", "none")

            img_bytes = None
            mime_type = "image/jpeg"

            if image_mode == "generate" or (not image_url.startswith("http") and image_mode != "none"):
                # Generate image with DALL-E 3
                image_subject = payload.get("imageSubject", "")
                image_style = payload.get("imageStyle", "")
                image_vibe = payload.get("imageVibe", "")
                dalle_prompt = " ".join(filter(None, [
                    image_subject or topic,
                    f"Style: {image_style}" if image_style else "",
                    f"Mood: {image_vibe}" if image_vibe else "",
                    "Professional LinkedIn post visual, high quality",
                ]))
                log(f"Generating DALL-E image: {dalle_prompt[:100]}")
                img_bytes, mime_type = generate_image_with_dalle(dalle_prompt)

            elif image_url.startswith("http"):
                # Download provided image URL
                import urllib.request
                try:
                    with urllib.request.urlopen(image_url, timeout=30) as img_resp:
                        img_bytes = img_resp.read()
                        mime_type = img_resp.headers.get("content-type", "image/jpeg")
                except Exception as e:
                    log(f"Failed to download image from URL: {e}", "WARNING")

            if img_bytes:
                asset_urn = helper.upload_image(img_bytes, mime_type)
                result = helper.create_post_with_image(text=content, image_urn=asset_urn)
            else:
                # No image available — fall back to text post
                log("No image available, falling back to text post", "WARNING")
                result = helper.create_post(text=content)
        else:
            # post_text
            result = helper.create_post(text=content)

        log(f"LinkedIn post successful: {result}")
        return {"success": True, "result": result}

    except ImportError as e:
        log(f"LinkedIn helper import error: {e}", "WARNING")
        log(f"[DRY-RUN] Would post to LinkedIn: {payload.get('body', '')[:200]}", "INFO")
        return {
            "success": True,
            "result": {
                "dry_run": True,
                "message": f"LinkedIn helper not importable ({e}); logged as dry-run"
            }
        }
    except Exception as e:
        tb = traceback.format_exc()
        log(f"LinkedIn execution error: {e}", "ERROR")
        log(f"Traceback: {tb}", "ERROR")
        return {"success": False, "error": str(e), "traceback": tb}


def _make_gmail_helper():
    """Instantiate GmailAPIHelper with correct credential paths from repo root."""
    from ai_operator.core import gmail_api_helper  # type: ignore
    secrets_dir = os.path.join(REPO_ROOT, ".secrets")
    return gmail_api_helper.GmailAPIHelper(
        credentials_path=os.path.join(secrets_dir, "gmail_credentials.json"),
        token_path=os.path.join(secrets_dir, "gmail_token.json"),
    )


def execute_gmail_draft(payload: dict) -> dict:
    """Create a real Gmail Draft saved to the Drafts folder (not sent)."""
    import base64 as _b64
    from email.mime.text import MIMEText as _MIMEText

    to = payload.get("recipient") or payload.get("to", "")
    subject = payload.get("subject") or payload.get("topic", "Draft Email")
    body = payload.get("body") or payload.get("content", "")

    # Auto-generate body if only a topic was provided
    if not body.strip() and payload.get("topic"):
        body = generate_content_with_openai(
            topic=payload["topic"],
            tone=payload.get("tone", "Professional"),
            length=payload.get("length", "Standard (~150 words)"),
            channel="gmail",
            intent=payload.get("intent", ""),
        )

    try:
        helper = _make_gmail_helper()
        if not helper.authenticate():
            return {"success": False, "error": "Gmail authentication failed — check .secrets/gmail_token.json"}

        msg = _MIMEText(body, 'plain', 'utf-8')
        msg['to'] = to
        msg['subject'] = subject

        raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = helper.service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw}}
        ).execute()

        draft_id = draft.get('id', '')
        log(f"Gmail draft created: id={draft_id} to={to} subject={subject}")
        return {
            "success": True,
            "result": {
                "mode": "draft",
                "draft_id": draft_id,
                "to": to,
                "subject": subject,
                "message": f"Draft saved to Gmail Drafts folder for {to or '(no recipient)'} — open Gmail to review and send",
            }
        }
    except ImportError as e:
        log(f"Gmail API library not available: {e}", "WARNING")
        return {"success": False, "error": "Gmail API libraries not installed. Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"}
    except Exception as e:
        log(f"Gmail draft error: {e}", "ERROR")
        return {"success": False, "error": str(e)}


def execute_gmail_send(payload: dict) -> dict:
    """Send an email via Gmail API (OAuth2 with auto-refresh)."""
    to = payload.get("recipient") or payload.get("to", "")
    subject = payload.get("subject") or payload.get("topic", "Email from AI Employee")
    body = payload.get("body") or payload.get("content", "")

    if not to:
        return {"success": False, "error": "Recipient email address is required"}

    # Auto-generate body from topic if empty
    if not body.strip() and payload.get("topic"):
        body = generate_content_with_openai(
            topic=payload["topic"],
            tone=payload.get("tone", "Professional"),
            length=payload.get("length", "Standard (~150 words)"),
            channel="gmail",
            intent=payload.get("intent", ""),
        )

    try:
        helper = _make_gmail_helper()
        result = helper.send_email(to=to, subject=subject, body=body, dry_run=False)
        log(f"Gmail send result: {result}")
        return {"success": result.get("success", False), "result": result}
    except ImportError as e:
        log(f"Gmail API library not available: {e}", "WARNING")
        return {"success": False, "error": f"Gmail API libraries not installed. Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"}
    except Exception as e:
        log(f"Gmail send error: {e}", "ERROR")
        return {"success": False, "error": str(e)}


def execute_whatsapp_message(payload: dict) -> dict:
    """Send a WhatsApp message via WhatsApp Web (Playwright). Requires paired session."""
    # "to" comes from web plan payload as "recipient" (ActionWizard field name) or "to"
    to = payload.get("to") or payload.get("recipient", "")
    message = payload.get("message") or payload.get("body") or payload.get("content", "")

    if not to:
        return {"success": False, "error": "Recipient phone number or chat_id ('to') is required"}
    if not message:
        return {"success": False, "error": "Message text is required"}

    # Auto-generate message body from topic if needed
    if not message.strip() and payload.get("topic"):
        message = generate_content_with_openai(
            topic=payload["topic"],
            tone=payload.get("tone", "Casual"),
            length=payload.get("length", "Short"),
            channel="whatsapp",
            intent=payload.get("intent", ""),
        )

    try:
        from ai_operator.core.whatsapp_web_helper import WhatsAppWebClient  # type: ignore

        # slow_mo=50 — just enough for WA JS to keep up, no unnecessary delay
        client = WhatsAppWebClient(headless=True, slow_mo_ms=50)
        client.start()

        if not client.is_logged_in():
            client.stop()
            return {
                "success": False,
                "error": "WhatsApp Web not logged in. Run: python3 scripts/wa_setup.py"
            }

        success = client.send_message(to=to, text=message)
        client.stop()

        if success:
            log(f"WhatsApp message sent to {to[:5]}**** ({len(message)} chars)")
            return {"success": True, "result": {"to_partial": to[:5] + "****", "chars": len(message)}}
        else:
            return {"success": False, "error": "send_message returned False — check WhatsApp Web session"}

    except ImportError as e:
        log(f"WhatsAppWebClient import error: {e}", "WARNING")
        return {"success": False, "error": f"WhatsApp helper not importable ({e}). Run: pip install playwright && playwright install chromium"}
    except Exception as e:
        tb = traceback.format_exc()
        log(f"WhatsApp execution error: {e}", "ERROR")
        log(f"Traceback: {tb}", "ERROR")
        return {"success": False, "error": str(e), "traceback": tb}


def execute_instagram_post(payload: dict) -> dict:
    """Post an image to Instagram via the Graph API. Requires a publicly accessible image URL."""
    try:
        from ai_operator.core.instagram_api_helper import InstagramAPIHelper  # type: ignore

        helper = InstagramAPIHelper()

        topic = payload.get("topic", "")
        body = payload.get("body") or payload.get("content") or payload.get("caption") or ""
        hashtags = payload.get("hashtags", [])
        tone = payload.get("tone", "Creative")
        length = payload.get("length", "Medium (~100 words)")
        intent = payload.get("intent", "")

        # Generate caption from topic if body is empty
        if topic and not body.strip():
            log(f"No caption provided — generating from topic: '{topic}'")
            body = generate_content_with_openai(
                topic=topic,
                tone=tone,
                length=length,
                channel="instagram",
                intent=intent,
                hashtags=hashtags if isinstance(hashtags, list) else [],
            )

        caption = body or topic

        # Append hashtags if not already in caption
        if hashtags and isinstance(hashtags, list):
            tags_str = " ".join(f"#{str(h).lstrip('#')}" for h in hashtags)
            if tags_str and tags_str not in caption:
                caption = f"{caption}\n\n{tags_str}"

        # Resolve image URL: prefer previewImageUrl (AI-generated), then imageFile (upload)
        image_url = (
            payload.get("previewImageUrl") or
            payload.get("imageFile") or
            payload.get("image_url") or
            ""
        )
        image_mode = payload.get("imageMode", "none")

        if not image_url or not image_url.startswith("http"):
            if image_mode == "generate":
                # DALL-E generated a previewImageUrl in the wizard — it must be in payload
                return {
                    "success": False,
                    "error": (
                        "Instagram requires a publicly accessible image URL. "
                        "The DALL-E preview URL was not found in the plan payload. "
                        "Go back to the wizard, generate the preview image, then resubmit."
                    ),
                }
            return {
                "success": False,
                "error": (
                    "Instagram requires a public image URL (https://…). "
                    "Use 'Upload' mode and paste a public URL, or use 'Generate' mode "
                    "to create an AI image preview first."
                ),
            }

        log(f"Posting Instagram image: url={image_url[:80]}… caption={caption[:80]}…")
        result = helper.create_post_with_image(image_url=image_url, caption=caption)
        log(f"Instagram post successful: {result}")
        return {"success": True, "result": result}

    except ImportError as e:
        log(f"Instagram helper import error: {e}", "WARNING")
        return {"success": False, "error": f"Instagram helper not importable: {e}"}
    except Exception as e:
        tb = traceback.format_exc()
        log(f"Instagram execution error: {e}", "ERROR")
        log(f"Traceback: {tb}", "ERROR")
        return {"success": False, "error": str(e), "traceback": tb}


def execute_odoo_query(payload: dict) -> dict:
    """Execute a read-only Odoo query (no approval needed)."""
    try:
        from ai_operator.core.odoo_api_helper import OdooAPIHelper  # type: ignore

        # Determine mock vs real mode
        secrets_path = os.path.join(REPO_ROOT, ".secrets", "odoo_credentials.json")
        use_mock = not os.path.exists(secrets_path)

        helper = OdooAPIHelper(
            credentials_path=secrets_path,
            mock=use_mock,
        )

        operation = payload.get("action_type", "query_invoices")
        log(f"Odoo query: operation={operation}, mock={use_mock}")

        if operation in ("query_invoices", "list_unpaid_invoices"):
            result = helper.list_invoices(status_filter="unpaid")
        elif operation == "revenue_summary":
            result = helper.revenue_summary()
        elif operation in ("ar_aging", "ar_aging_summary"):
            result = helper.ar_aging_summary()
        elif operation == "list_customers":
            result = helper.list_customers()
        else:
            result = helper.list_invoices(status_filter="unpaid")

        if result.get("success"):
            log(f"Odoo query successful: {operation}")
            return {"success": True, "result": result}
        else:
            return {"success": False, "error": result.get("error", "Odoo query failed")}

    except Exception as e:
        tb = traceback.format_exc()
        log(f"Odoo query error: {e}", "ERROR")
        return {"success": False, "error": str(e), "traceback": tb}


def _odoo_audit_log(action_type: str, parameters: dict, plan_id: str, result_ok: bool, error: str = None):
    """Write hackathon-compliant audit entry to Logs/YYYY-MM-DD.json."""
    try:
        from ai_operator.core.audit_logger import AuditLogger  # type: ignore
        audit = AuditLogger(os.path.join(REPO_ROOT, "Logs"))
        audit.log(
            action_type=action_type,
            actor="claude_code",
            parameters=parameters,
            approval_status="approved",
            approval_ref=plan_id,
            approved_by="human",
            result="success" if result_ok else "failure",
            error=error,
        )
    except Exception as ae:
        log(f"Audit log write failed (non-fatal): {ae}", "WARNING")


def execute_odoo_action(payload: dict) -> dict:
    """Execute an approved Odoo write action (approval required, dry-run safe)."""
    try:
        from ai_operator.core.odoo_api_helper import OdooAPIHelper  # type: ignore

        secrets_path = os.path.join(REPO_ROOT, ".secrets", "odoo_credentials.json")
        use_mock = not os.path.exists(secrets_path)

        helper = OdooAPIHelper(
            credentials_path=secrets_path,
            mock=use_mock,
        )

        action_type = payload.get("action_type", "create_invoice")
        plan_id = payload.get("plan_id", "unknown")
        log(f"Odoo action: action_type={action_type}, mock={use_mock}")

        if action_type == "create_invoice":
            raw_partner_id = payload.get("partner_id")
            customer_name = payload.get("customer_name") or payload.get("topic")
            # Resolve partner: validate supplied ID, search by name, or pick first customer
            partner_id = helper.find_partner(
                name=customer_name,
                partner_id=int(raw_partner_id) if raw_partner_id else None,
            )
            if not partner_id:
                return {"success": False, "error": f"Customer '{customer_name}' not found in Odoo. Use the 'Create Customer' action first to add them, then create the invoice."}
            lines = payload.get("lines") or [
                {"name": payload.get("topic", "Service"), "quantity": 1, "price_unit": payload.get("amount", 1000)}
            ]
            result = helper.create_invoice(partner_id=partner_id, lines=lines)
            _odoo_audit_log("odoo_invoice_create", {"partner_id": partner_id, "line_count": len(lines)}, plan_id, result.get("success", False), result.get("error"))

        elif action_type == "post_invoice":
            invoice_id = payload.get("invoice_id")
            if not invoice_id:
                return {"success": False, "error": "post_invoice requires invoice_id in payload"}
            result = helper.post_invoice(invoice_id=int(invoice_id))
            _odoo_audit_log("odoo_invoice_post", {"invoice_id": invoice_id}, plan_id, result.get("success", False), result.get("error"))

        elif action_type == "register_payment":
            invoice_id = payload.get("invoice_id")
            amount = payload.get("amount")
            if not invoice_id or not amount:
                return {"success": False, "error": "register_payment requires invoice_id and amount"}
            # NEVER auto-retry payment operations (financial idempotency)
            result = helper.register_payment(invoice_id=int(invoice_id), amount=float(amount))
            _odoo_audit_log("odoo_payment_register", {"invoice_id": invoice_id, "amount": amount}, plan_id, result.get("success", False), result.get("error"))

        elif action_type == "create_customer":
            name = payload.get("customer_name") or payload.get("topic", "New Customer")
            result = helper.create_customer(name=name, email=payload.get("email"), phone=payload.get("phone"))
            _odoo_audit_log("odoo_customer_create", {"name": name}, plan_id, result.get("success", False), result.get("error"))

        elif action_type == "create_credit_note":
            invoice_id = payload.get("invoice_id")
            if not invoice_id:
                return {"success": False, "error": "create_credit_note requires invoice_id"}
            result = helper.create_credit_note(invoice_id=int(invoice_id), reason=payload.get("reason", ""))
            _odoo_audit_log("odoo_credit_note_create", {"invoice_id": invoice_id}, plan_id, result.get("success", False), result.get("error"))

        else:
            return {"success": False, "error": f"Unsupported Odoo action: {action_type}"}

        if result.get("success"):
            log(f"Odoo action successful: {action_type}")
            return {"success": True, "result": result}
        else:
            return {"success": False, "error": result.get("error", "Odoo action failed")}

    except Exception as e:
        tb = traceback.format_exc()
        log(f"Odoo action error: {e}", "ERROR")
        return {"success": False, "error": str(e), "traceback": tb}


CHANNEL_HANDLERS = {
    ("linkedin", "post_text"): execute_linkedin_post,
    ("linkedin", "post_image"): execute_linkedin_post,
    ("gmail", "draft_email"): execute_gmail_draft,
    ("gmail", "send_email"): execute_gmail_send,
    ("whatsapp", "send_message"): execute_whatsapp_message,
    ("instagram", "post_image"): execute_instagram_post,
    # Odoo — read-only queries (no approval needed)
    ("odoo", "query_invoices"): execute_odoo_query,
    ("odoo", "revenue_summary"): execute_odoo_query,
    ("odoo", "ar_aging"): execute_odoo_query,
    ("odoo", "list_customers"): execute_odoo_query,
    # Odoo — write actions (approval required)
    ("odoo", "create_invoice"): execute_odoo_action,
    ("odoo", "post_invoice"): execute_odoo_action,
    ("odoo", "register_payment"): execute_odoo_action,
    ("odoo", "create_customer"): execute_odoo_action,
    ("odoo", "create_credit_note"): execute_odoo_action,
}


def main():
    parser = argparse.ArgumentParser(description="Execute a web-created plan via Python skills")
    parser.add_argument("--json", required=True, help="JSON payload string")
    args = parser.parse_args()

    try:
        data = json.loads(args.json)
    except json.JSONDecodeError as e:
        log(f"Invalid JSON payload: {e}", "ERROR")
        sys.exit(1)

    plan_id = data.get("plan_id", "unknown")
    channel = data.get("channel", "").lower()
    action_type = data.get("action_type", "").lower()
    payload = data.get("payload", {})

    # Merge action_type into payload for convenience
    payload["action_type"] = action_type

    log(f"Executing plan {plan_id}: {channel}/{action_type}")

    handler = CHANNEL_HANDLERS.get((channel, action_type))
    if not handler:
        # Try channel-only fallback (pick first matching handler)
        handler = next(
            (h for (c, _a), h in CHANNEL_HANDLERS.items() if c == channel),
            None
        )

    if not handler:
        msg = f"Unsupported channel/action: {channel}/{action_type}"
        log(msg, "ERROR")
        print(json.dumps({"success": False, "error": msg}), flush=True)
        sys.exit(2)

    result = handler(payload)
    print(json.dumps(result), flush=True)

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
