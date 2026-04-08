"""
Lead Qualifier & Appointment Booker — AI Agent Demo
For dental and chiropractic clinics.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import anthropic

app = Flask(__name__)

LEADS_FILE = Path(__file__).parent / "leads.json"
BOOKING_LINK = "https://calendly.com/your-clinic/appointment"  # placeholder


def _load_leads() -> list[dict]:
    if LEADS_FILE.exists():
        return json.loads(LEADS_FILE.read_text())
    return []


def _save_lead(lead: dict):
    leads = _load_leads()
    leads.append(lead)
    LEADS_FILE.write_text(json.dumps(leads, indent=2))


# ── Claude-powered lead scoring ────────────────────────────────────────────

SCORING_PROMPT = """\
You are a lead-qualification AI for a {clinic_type} clinic.

Score this inbound inquiry 1-10 based on:
- Urgency (pain, emergency, time-sensitive language)
- Service match (relevant to {clinic_type})
- Budget signals (insurance mention, willingness to pay)

Inquiry type: {inquiry_type}
Message: {message}

Respond ONLY in this exact format — no other text:
SCORE: <number>
REASON: <one sentence>"""


def score_lead(clinic_type: str, inquiry_type: str, message: str) -> tuple[int, str]:
    """Call Claude Haiku to score a lead. Returns (score, reason)."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        messages=[
            {
                "role": "user",
                "content": SCORING_PROMPT.format(
                    clinic_type=clinic_type,
                    inquiry_type=inquiry_type,
                    message=message,
                ),
            }
        ],
    )

    text = resp.content[0].text.strip()
    # Parse "SCORE: 8\nREASON: ..."
    score_match = re.search(r"SCORE:\s*(\d+)", text)
    reason_match = re.search(r"REASON:\s*(.+)", text)

    score = int(score_match.group(1)) if score_match else 5
    score = max(1, min(10, score))
    reason = reason_match.group(1).strip() if reason_match else "Unable to parse reason."
    return score, reason


# ── Auto-response generation ───────────────────────────────────────────────

def build_response(score: int, patient_name: str, clinic_name: str) -> dict:
    """Build a tier-based response object."""
    first = patient_name.split()[0] if patient_name else "there"

    if score >= 8:
        return {
            "tier": "priority",
            "subject": f"{clinic_name} — We're ready to see you right away",
            "body": (
                f"Hi {first},\n\n"
                f"Thank you for reaching out to {clinic_name}. Based on what you've "
                f"described, we'd like to get you in as soon as possible.\n\n"
                f"Book your priority appointment here:\n{BOOKING_LINK}\n\n"
                f"If you need to speak with someone immediately, reply to this message "
                f"and a team member will call you within 15 minutes.\n\n"
                f"We look forward to helping you.\n\n"
                f"— The {clinic_name} Team"
            ),
        }
    elif score >= 5:
        return {
            "tier": "nurture",
            "subject": f"{clinic_name} — Thanks for your inquiry",
            "body": (
                f"Hi {first},\n\n"
                f"Thanks for contacting {clinic_name}! We've received your inquiry "
                f"and want to make sure we find the right solution for you.\n\n"
                f"Here are some resources that may help in the meantime:\n"
                f"  - Our services overview: https://{clinic_name.lower().replace(' ', '')}.com/services\n"
                f"  - Patient FAQ: https://{clinic_name.lower().replace(' ', '')}.com/faq\n\n"
                f"When you're ready to book, use this link:\n{BOOKING_LINK}\n\n"
                f"We'll also follow up within 24 hours to answer any questions.\n\n"
                f"— The {clinic_name} Team"
            ),
        }
    else:
        return {
            "tier": "redirect",
            "subject": f"{clinic_name} — Thanks for reaching out",
            "body": (
                f"Hi {first},\n\n"
                f"Thank you for contacting {clinic_name}. After reviewing your inquiry, "
                f"it looks like your needs may be better served by a specialist.\n\n"
                f"We recommend:\n"
                f"  - Your primary care physician for a referral\n"
                f"  - ZocDoc (https://zocdoc.com) to find the right provider\n\n"
                f"If we misunderstood and you'd still like to connect, just reply "
                f"and we'll be happy to help.\n\n"
                f"— The {clinic_name} Team"
            ),
        }


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("form.html")


@app.route("/submit", methods=["POST"])
def submit_lead():
    data = request.form if request.form else request.get_json(force=True)

    clinic_name = data.get("clinic_name", "").strip()
    clinic_type = data.get("clinic_type", "dental").strip()
    patient_name = data.get("patient_name", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    inquiry_type = data.get("inquiry_type", "general").strip()
    message = data.get("message", "").strip()

    if not message or not patient_name:
        return jsonify({"error": "Patient name and message are required."}), 400

    # Score the lead via Claude
    score, reason = score_lead(clinic_type, inquiry_type, message)

    # Build auto-response
    response = build_response(score, patient_name, clinic_name or "Our Clinic")

    # Build lead record
    lead = {
        "id": len(_load_leads()) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "clinic_name": clinic_name,
        "clinic_type": clinic_type,
        "patient_name": patient_name,
        "email": email,
        "phone": phone,
        "inquiry_type": inquiry_type,
        "message": message,
        "score": score,
        "score_reason": reason,
        "response_tier": response["tier"],
        "auto_response_subject": response["subject"],
        "auto_response_body": response["body"],
    }

    _save_lead(lead)

    return jsonify(lead)


@app.route("/leads")
def view_leads():
    """Dashboard endpoint — returns all leads as JSON."""
    return jsonify(_load_leads())


@app.route("/dashboard")
def dashboard():
    """Simple visual dashboard."""
    leads = _load_leads()
    return render_template("dashboard.html", leads=leads)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
