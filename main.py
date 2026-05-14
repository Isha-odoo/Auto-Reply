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
# INIT GEMINI
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
        "message": "Lead Automation API Running"
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
        "email": re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text),
        "pincode": re.findall(r'\b\d{5,6}\b', text)
    }

# =========================
# AI EXTRACTION
# =========================
def ai_extract(text):

    prompt = """
You are a precise CRM extraction assistant.

Extract buyer details from inquiry emails.

RULES:
1. Ignore IndiaMART support numbers/emails
2. Extract actual buyer info only
3. country must be 2-letter ISO code
4. Return blank if missing
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
# AI AUTO REPLY
# =========================
def generate_ai_reply(email_text, lead_data):

    try:

        prompt = f"""
You are a professional pharma sales assistant.

Generate a professional email reply.

CUSTOMER EMAIL:
{email_text}

LEAD DATA:
{json.dumps(lead_data, indent=2)}

RULES:
- Be professional
- Thank customer
- Reply according to inquiry
- Keep under 120 words
- Human sounding
- No fake promises
- No fake pricing
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
        data["phone"] = re.sub(r'[^\d+]', '', data["phone"])

    if data.get("email") and "@" not in data["email"]:
        data["email"] = ""

    return data

# =========================
# MERGE
# =========================
def merge(ai_data, regex_data):

    if not ai_data.get("phone") and regex_data.get("phone"):
        ai_data["phone"] = regex_data["phone"][0]

    if not ai_data.get("email") and regex_data.get("email"):
        ai_data["email"] = regex_data["email"][0]

    return ai_data

# =========================
# CREATE LEAD IN ODOO
# =========================
def create_odoo_lead(data, ai_reply):

    try:

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

        desc_text = data.get('description') or ""

        lead_vals = {

            'name': data.get('product') or "Website Lead",

            'contact_name': data.get('name') or "",

            'phone': data.get('phone') or "",

            'email_from': data.get('email') or "",

            'street': data.get('address') or "",

            'city': data.get('city') or "",

            'zip': data.get('pincode') or "",

            'description': desc_text,

        }

        # =========================
        # CREATE CRM LEAD
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
<b>🤖 AI Auto Reply Generated</b>

<br/><br/>

<b>Reply:</b>

<br/>

{ai_reply.replace(chr(10), '<br/>')}
"""

        models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'mail.message',
            'create',
            [{
                'body': chatter_body,
                'model': 'crm.lead',
                'res_id': lead_id,
                'message_type': 'comment',
                'subtype_xmlid': 'mail.mt_comment'
            }]
        )

        # =========================
        # SEND EMAIL FROM ODOO
        # =========================
        if data.get("email"):

            mail_values = {
                'subject': f"Re: {data.get('product') or 'Your Inquiry'}",
                'body_html': f"""
<p>{ai_reply.replace(chr(10), '<br/>')}</p>
""",
                'email_to': data.get("email"),
                'email_from': ODOO_USERNAME,
            }

            mail_id = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'create',
                [mail_values]
            )

            models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'send',
                [[mail_id]]
            )

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
    # LEAD GUARD
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
    # AI REPLY
    # =========================
    ai_reply = generate_ai_reply(text, data)

    data["ai_reply"] = ai_reply

    # =========================
    # CREATE LEAD + SEND MAIL
    # =========================
    lead_id, error = create_odoo_lead(
        data,
        ai_reply
    )

    data["odoo_lead_id"] = lead_id

    if error:
        data["odoo_error"] = error

    return data
