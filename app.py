"""
EliteAI Solutions — Lead Qualifier, Client Dashboard, Admin Panel, Stripe Checkout
Production-ready Flask application for AI automation service.
"""

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for, abort

import anthropic
import stripe

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ── Stripe config ─────────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_placeholder")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "pk_test_placeholder")

# Price IDs — create these in Stripe Dashboard or via API
STRIPE_PRICES = {
    "starter_setup": os.environ.get("STRIPE_PRICE_STARTER_SETUP", "price_starter_setup"),
    "starter_monthly": os.environ.get("STRIPE_PRICE_STARTER_MONTHLY", "price_starter_monthly"),
    "pro_setup": os.environ.get("STRIPE_PRICE_PRO_SETUP", "price_pro_setup"),
    "pro_monthly": os.environ.get("STRIPE_PRICE_PRO_MONTHLY", "price_pro_monthly"),
}

BASE_URL = os.environ.get("BASE_URL", "https://web-production-f6c06.up.railway.app")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "eliteai2026")

# ── Data files ────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

LEADS_FILE = DATA_DIR / "leads.json"
CLIENTS_FILE = DATA_DIR / "clients.json"
EMAILS_LOG = DATA_DIR / "emails_log.json"
BOOKING_LINK = "https://calendly.com/your-clinic/appointment"


# ── Data helpers ──────────────────────────────────────────────────────────

def _load_json(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _save_json(path: Path, data: list[dict]):
    path.write_text(json.dumps(data, indent=2, default=str))


def _load_leads() -> list[dict]:
    # Migrate old leads.json from parent dir
    old_file = Path(__file__).parent / "leads.json"
    if old_file.exists() and not LEADS_FILE.exists():
        old_file.rename(LEADS_FILE)
    return _load_json(LEADS_FILE)


def _save_lead(lead: dict):
    leads = _load_leads()
    leads.append(lead)
    _save_json(LEADS_FILE, leads)


def _load_clients() -> list[dict]:
    return _load_json(CLIENTS_FILE)


def _save_client(client: dict):
    clients = _load_clients()
    clients.append(client)
    _save_json(CLIENTS_FILE, clients)


def _log_email(entry: dict):
    emails = _load_json(EMAILS_LOG)
    emails.append(entry)
    _save_json(EMAILS_LOG, emails)


# ── Auth helpers ──────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def client_token_valid(token: str) -> dict | None:
    """Validate a client access token. Returns client dict or None."""
    clients = _load_clients()
    for c in clients:
        if c.get("access_token") == token:
            return c
    return None


# ── Claude-powered lead scoring ──────────────────────────────────────────

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
    client = anthropic.Anthropic()

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
    score_match = re.search(r"SCORE:\s*(\d+)", text)
    reason_match = re.search(r"REASON:\s*(.+)", text)

    score = int(score_match.group(1)) if score_match else 5
    score = max(1, min(10, score))
    reason = reason_match.group(1).strip() if reason_match else "Unable to parse reason."
    return score, reason


# ── Auto-response generation ─────────────────────────────────────────────

def build_response(score: int, patient_name: str, clinic_name: str) -> dict:
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


# ══════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════

# ── Public: Lead intake form ──────────────────────────────────────────────

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

    score, reason = score_lead(clinic_type, inquiry_type, message)
    response = build_response(score, patient_name, clinic_name or "Our Clinic")

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
        "follow_up_status": "pending",
        "follow_up_count": 0,
    }

    _save_lead(lead)

    # Auto-schedule follow-up email
    if email:
        _log_email({
            "type": "auto_response",
            "to": email,
            "subject": response["subject"],
            "body": response["body"],
            "lead_id": lead["id"],
            "status": "logged",  # Will be "sent" when SendGrid connected
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return jsonify(lead)


@app.route("/leads")
def view_leads():
    return jsonify(_load_leads())


@app.route("/dashboard")
def dashboard():
    leads = _load_leads()
    return render_template("dashboard.html", leads=leads)


# ── Client Dashboard ─────────────────────────────────────────────────────

@app.route("/client/<token>")
def client_dashboard(token):
    client = client_token_valid(token)
    if not client:
        abort(404)

    all_leads = _load_leads()
    client_leads = [l for l in all_leads if l.get("clinic_name", "").lower() == client.get("clinic_name", "").lower()]
    all_emails = _load_json(EMAILS_LOG)
    client_lead_ids = {l["id"] for l in client_leads}
    client_emails = [e for e in all_emails if e.get("lead_id") in client_lead_ids]

    return render_template(
        "client_dashboard.html",
        client=client,
        leads=client_leads,
        emails=client_emails,
    )


# ── Admin Panel ───────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["admin_authed"] = True
            return redirect(url_for("admin_panel"))
        return render_template("admin_login.html", error="Invalid password")
    return render_template("admin_login.html", error=None)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_panel():
    leads = _load_leads()
    clients = _load_clients()
    emails = _load_json(EMAILS_LOG)
    return render_template("admin.html", leads=leads, clients=clients, emails=emails)


@app.route("/admin/add-client", methods=["POST"])
@admin_required
def admin_add_client():
    data = request.form
    token = secrets.token_urlsafe(24)
    client = {
        "id": len(_load_clients()) + 1,
        "clinic_name": data.get("clinic_name", "").strip(),
        "contact_name": data.get("contact_name", "").strip(),
        "email": data.get("email", "").strip(),
        "phone": data.get("phone", "").strip(),
        "plan": data.get("plan", "starter"),
        "status": "active",
        "access_token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stripe_customer_id": None,
        "mrr": 500 if data.get("plan") == "starter" else 750,
    }
    _save_client(client)
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete-client/<int:client_id>", methods=["POST"])
@admin_required
def admin_delete_client(client_id):
    clients = _load_clients()
    clients = [c for c in clients if c.get("id") != client_id]
    _save_json(CLIENTS_FILE, clients)
    return redirect(url_for("admin_panel"))


# ── Email Automation Endpoints ────────────────────────────────────────────

@app.route("/api/email/send-followup", methods=["POST"])
def send_followup():
    """Trigger a follow-up email for a lead. Logs it for now (SendGrid later)."""
    data = request.get_json(force=True)
    lead_id = data.get("lead_id")
    template = data.get("template", "followup_1")

    leads = _load_leads()
    lead = next((l for l in leads if l.get("id") == lead_id), None)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    if not lead.get("email"):
        return jsonify({"error": "Lead has no email address"}), 400

    # Build follow-up based on template
    first = lead["patient_name"].split()[0] if lead.get("patient_name") else "there"
    clinic = lead.get("clinic_name", "Our Clinic")

    templates = {
        "followup_1": {
            "subject": f"{clinic} — Following up on your inquiry",
            "body": f"Hi {first},\n\nWe wanted to follow up on your recent inquiry to {clinic}. We'd love to help you get the care you need.\n\nBook an appointment: {BOOKING_LINK}\n\nBest,\nThe {clinic} Team"
        },
        "followup_2": {
            "subject": f"{clinic} — We haven't heard from you",
            "body": f"Hi {first},\n\nJust checking in — we noticed you haven't booked your appointment yet. We have openings this week and would love to see you.\n\nBook now: {BOOKING_LINK}\n\nThe {clinic} Team"
        },
        "followup_3": {
            "subject": f"{clinic} — Last chance: Special offer inside",
            "body": f"Hi {first},\n\nWe're reaching out one last time. As a thank you for your interest, we'd like to offer you a complimentary consultation.\n\nClaim your spot: {BOOKING_LINK}\n\nThe {clinic} Team"
        },
    }

    email_data = templates.get(template, templates["followup_1"])

    entry = {
        "type": "follow_up",
        "template": template,
        "to": lead["email"],
        "subject": email_data["subject"],
        "body": email_data["body"],
        "lead_id": lead_id,
        "status": "logged",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _log_email(entry)

    # Update lead follow-up count
    for l in leads:
        if l["id"] == lead_id:
            l["follow_up_count"] = l.get("follow_up_count", 0) + 1
            l["follow_up_status"] = "sent"
            break
    _save_json(LEADS_FILE, leads)

    return jsonify({"success": True, "email": entry})


@app.route("/api/email/bulk-followup", methods=["POST"])
def bulk_followup():
    """Send follow-up to all leads that haven't been followed up."""
    leads = _load_leads()
    sent = 0
    for lead in leads:
        if lead.get("email") and lead.get("follow_up_count", 0) == 0:
            first = lead["patient_name"].split()[0] if lead.get("patient_name") else "there"
            clinic = lead.get("clinic_name", "Our Clinic")
            entry = {
                "type": "bulk_follow_up",
                "to": lead["email"],
                "subject": f"{clinic} — Following up on your inquiry",
                "body": f"Hi {first},\n\nWe wanted to follow up on your recent inquiry. Book your appointment: {BOOKING_LINK}\n\nThe {clinic} Team",
                "lead_id": lead["id"],
                "status": "logged",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _log_email(entry)
            lead["follow_up_count"] = 1
            lead["follow_up_status"] = "sent"
            sent += 1

    _save_json(LEADS_FILE, leads)
    return jsonify({"success": True, "emails_sent": sent})


@app.route("/api/emails")
def get_emails():
    """Get all email logs."""
    return jsonify(_load_json(EMAILS_LOG))


# ── Stripe Checkout ───────────────────────────────────────────────────────

@app.route("/checkout/<plan>")
def checkout(plan):
    """Create a Stripe checkout session for a plan."""
    if plan not in ("starter", "pro"):
        return jsonify({"error": "Invalid plan. Use 'starter' or 'pro'."}), 400

    try:
        line_items = []
        if plan == "starter":
            line_items = [
                {"price": STRIPE_PRICES["starter_setup"], "quantity": 1},
                {"price": STRIPE_PRICES["starter_monthly"], "quantity": 1},
            ]
        else:
            line_items = [
                {"price": STRIPE_PRICES["pro_setup"], "quantity": 1},
                {"price": STRIPE_PRICES["pro_monthly"], "quantity": 1},
            ]

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="subscription",
            success_url=BASE_URL + "/checkout/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=BASE_URL + "/checkout/cancel",
            metadata={"plan": plan},
        )
        return redirect(checkout_session.url, code=303)

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        # Stripe not configured yet — show friendly message
        return render_template("checkout_placeholder.html", plan=plan)


@app.route("/checkout/success")
def checkout_success():
    return render_template("checkout_success.html")


@app.route("/checkout/cancel")
def checkout_cancel():
    return redirect(url_for("index"))


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        if endpoint_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        else:
            event = json.loads(payload)
    except Exception:
        abort(400)

    if event.get("type") == "checkout.session.completed":
        session_data = event["data"]["object"]
        # Log the new customer
        client = {
            "id": len(_load_clients()) + 1,
            "clinic_name": session_data.get("customer_details", {}).get("name", "New Client"),
            "contact_name": session_data.get("customer_details", {}).get("name", ""),
            "email": session_data.get("customer_details", {}).get("email", ""),
            "phone": "",
            "plan": session_data.get("metadata", {}).get("plan", "starter"),
            "status": "active",
            "access_token": secrets.token_urlsafe(24),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stripe_customer_id": session_data.get("customer"),
            "mrr": 500 if session_data.get("metadata", {}).get("plan") == "starter" else 750,
        }
        _save_client(client)

    return jsonify({"received": True})


# ── API: Stats ────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    leads = _load_leads()
    clients = _load_clients()
    emails = _load_json(EMAILS_LOG)

    total_leads = len(leads)
    priority = len([l for l in leads if l.get("score", 0) >= 8])
    nurture = len([l for l in leads if 5 <= l.get("score", 0) < 8])
    redirect_count = len([l for l in leads if l.get("score", 0) < 5])
    total_clients = len([c for c in clients if c.get("status") == "active"])
    total_mrr = sum(c.get("mrr", 0) for c in clients if c.get("status") == "active")
    total_emails = len(emails)

    avg_score = round(sum(l.get("score", 0) for l in leads) / max(total_leads, 1), 1)

    # Score distribution for charts
    score_dist = [0] * 10
    for l in leads:
        s = l.get("score", 0)
        if 1 <= s <= 10:
            score_dist[s - 1] += 1

    # Leads over time (last 30 days)
    from collections import Counter
    date_counts = Counter()
    for l in leads:
        ts = l.get("timestamp", "")
        if ts:
            date_counts[ts[:10]] += 1

    return jsonify({
        "total_leads": total_leads,
        "priority": priority,
        "nurture": nurture,
        "redirect": redirect_count,
        "avg_score": avg_score,
        "total_clients": total_clients,
        "total_mrr": total_mrr,
        "total_emails": total_emails,
        "score_distribution": score_dist,
        "leads_by_date": dict(sorted(date_counts.items())),
    })


# ── Health check ──────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
