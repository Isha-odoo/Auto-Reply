from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
import time

from google import genai
from google.genai import types

app = FastAPI()

# =========================
# SCHEMAS
# =========================
class EmailRequest(BaseModel):
    text: str
    from_email: str = ""
    subject: str = ""

class LeadSchema(BaseModel):
    name: str
    phone: str
    email: str
    product: str
    description: str
    address: str
    city: str
    state: str
    pincode: str
    country: str

# =========================
# GEMINI INIT
# =========================
client = genai.Client()

# =========================
# ODOO CONFIG
# =========================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

# =========================
# HEALTH CHECK
# =========================
@app.api_route("/", methods=["GET", "HEAD"])
def health_check():
    return {
        "status": "Live",
        "message": "AI CRM Automation Running"
    }

# =========================
# CLEAN HTML
# =========================
def clean_html(raw_html):
    text = re.sub('<.*?>', '\n', raw_html)
    text = re.sub(r'\n+', '\n', text)
    return text.strip()

# =========================
# REGEX EXTRACTION
# =========================
def regex_extract(text):
    return {
        "phone": re.findall(r'\+?\d[\d\s\-]{8,15}', text),
        "email": re.findall(
            r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
            text
        ),
        "pincode": re.findall(r'\b\d{5,6}\b', text)
    }

# =========================
# AI EXTRACTION
# =========================
def ai_extract(text):
    prompt = """
You are a CRM lead extraction assistant.

Extract actual customer details.

RULES:
- Ignore IndiaMART support info
- Ignore platform numbers
- Extract actual buyer details
- country must be ISO 2-letter code
- Return blank if unavailable
"""
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt + f"\n\nEMAIL:\n{text}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LeadSchema,
                    temperature=0.1
                ),
            )
            return json.loads(response.text)

        except Exception as e:
            print("AI ERROR:", str(e))
            if attempt < max_retries - 1:
                time.sleep(2)

    return {
        "name": "",
        "phone": "",
        "email": "",
        "product": "",
        "description": "",
        "address": "",
        "city": "",
        "state": "",
        "pincode": "",
        "country": ""
    }

# =========================
# AI REPLY GENERATOR
# =========================
def generate_ai_reply(email_text, lead_data):
    try:
        prompt = f"""
You are a professional pharma sales assistant.

Generate a professional customer email reply.

CUSTOMER EMAIL:
{email_text}

LEAD DATA:
{json.dumps(lead_data, indent=2)}

RULES:
- Professional tone
- Thank customer
- Reply according to inquiry
- Keep under 120 words
- Human sounding
- No fake pricing
- No fake commitments
- Output ONLY email body
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4
            ),
        )
        return response.text.strip()

    except Exception as e:
        print("REPLY ERROR:", str(e))
        return ""

# =========================
# VALIDATION
# =========================
def validate(data):
    if data.get("phone"):
        data["phone"] = re.sub(
            r'[^\d+]',
            '',
            data["phone"]
        )

    if data.get("email") and "@" not in data["email"]:
        data["email"] = ""

    return data

# =========================
# MERGE LOGIC
# =========================
def merge(ai_data, regex_data):
    if not ai_data.get("phone") and regex_data.get("phone"):
        ai_data["phone"] = regex_data["phone"][0]

    if not ai_data.get("email") and regex_data.get("email"):
        ai_data["email"] = regex_data["email"][0]

    return ai_data

# =========================
# CREATE ODOO LEAD
# =========================
def create_odoo_lead(data, ai_reply):
    try:
        # =========================
        # LOGIN
        # =========================
        common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common",
            allow_none=True
        )

        uid = common.authenticate(
            ODOO_DB,
            ODOO_USERNAME,
            ODOO_PASSWORD,
            {}
        )

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object",
            allow_none=True
        )

        # =========================
        # DESCRIPTION
        # =========================
        desc_text = data.get("description") or ""

        address_parts = [
            data.get("address"),
            data.get("city"),
            data.get("state"),
            data.get("pincode"),
            data.get("country")
        ]

        valid_address = [x for x in address_parts if x]

        if valid_address:
            desc_text += (
                "\n\nFull Address: "
                + ", ".join(valid_address)
            )

        # =========================
        # LEAD VALUES
        # =========================
        lead_vals = {
            'name': data.get('product') or "New Lead",
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'street': data.get('address') or "",
            'city': data.get('city') or "",
            'zip': data.get('pincode') or "",
            'description': desc_text.strip(),
        }

        # =========================
        # CREATE LEAD
        # =========================
        lead_id = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'crm.lead',
            'create',
            [lead_vals]
        )

        # =========================
        # CHATTER MESSAGE
        # =========================
        chatter_body = f"""
<h3>🤖 AI Auto Reply Sent to Customer</h3>
<p>
{ai_reply.replace(chr(10), '<br/>')}
</p>
"""

        models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'crm.lead',
            'message_post',
            [[lead_id]],
            {
                'body': chatter_body,
                'body_is_html': True
            }
        )

        # =========================
        # SEND EMAIL FROM ODOO
        # =========================
        if data.get("email"):
            mail_values = {
                'subject': (
                    f"Re: "
                    f"{data.get('product') or 'Your Inquiry'}"
                ),
                'body_html': f"""
<p>
{ai_reply.replace(chr(10), '<br/>')}
</p>
""",
                'email_to': data.get("email"),
                'email_from': ODOO_USERNAME,
                'state': 'outgoing' # Explicitly flag it for the Odoo mail queue
            }

            # Creating the mail record queues it automatically
            mail_id = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'create',
                [mail_values]
            )

            # ❌ REMOVED the models.execute_kw(..., 'mail.mail', 'send', ...) call
            # Odoo's internal Cron task will take over and send the email shortly.

        return lead_id, None

    except Exception as e:
        return None, str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):
    text = clean_html(request.text)

    # =========================
    # EXTRACT DATA
    # =========================
    ai_data = ai_extract(text)
    regex_data = regex_extract(text)
    data = merge(ai_data, regex_data)
    data = validate(data)

    print("FINAL DATA:", data)

    # =========================
    # LEAD VALIDATION
    # =========================
    has_contact = bool(
        data.get("phone") or data.get("email")
    )
    has_context = bool(
        data.get("name") or data.get("product")
    )

    if not (has_contact and has_context):
        data["odoo_error"] = "Skipped incomplete lead"
        return data

    # =========================
    # GENERATE AI REPLY
    # =========================
    ai_reply = generate_ai_reply(text, data)
    data["ai_reply"] = ai_reply

    # =========================
    # CREATE LEAD + SEND EMAIL
    # =========================
    lead_id, error = create_odoo_lead(data, ai_reply)
    data["odoo_lead_id"] = lead_id

    if error:
        data["odoo_error"] = error

    return data
