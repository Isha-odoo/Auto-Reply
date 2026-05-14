from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
from openai import OpenAI

app = FastAPI()

# =========================
# OPENROUTER CLIENT
# =========================
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

# =========================
# ODOO CONFIG
# =========================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

# =========================
# REQUEST SCHEMA
# =========================
class EmailRequest(BaseModel):
    text: str
    from_email: str = ""
    subject: str = ""

# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def home():
    return {
        "status": "Live",
        "message": "AI CRM Automation Running"
    }

# =========================
# CLEAN HTML
# =========================
def clean_html(raw_html):

    text = re.sub(r'<[^>]+>', '\n', raw_html)
    text = re.sub(r'\n+', '\n', text)

    return text.strip()

# =========================
# REGEX EXTRACTION
# =========================
def regex_extract(text):

    phones = re.findall(
        r'\+?\d[\d\s\-]{8,15}',
        text
    )

    emails = re.findall(
        r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        text
    )

    pincodes = re.findall(
        r'\b\d{5,6}\b',
        text
    )

    return {
        "phone": phones,
        "email": emails,
        "pincode": pincodes
    }

# =========================
# AI EXTRACTION
# =========================
def ai_extract(text, subject=""):

    prompt = f"""
You are a CRM lead extraction assistant.

Extract:
- name
- phone
- email
- product
- description
- address
- city
- state
- pincode
- country

RULES:
- Ignore IndiaMART support numbers
- Ignore seller/company internal numbers
- Extract only buyer/customer details
- Return ONLY valid JSON
- If name missing return "Unknown Customer"
- If product missing use email subject
- Keep description short

EMAIL SUBJECT:
{subject}

EMAIL BODY:
{text}
"""

    try:

        response = client.chat.completions.create(

            model="mistralai/mistral-7b-instruct:free",

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.1,
            max_tokens=500
        )

        result = response.choices[0].message.content.strip()

        result = result.replace("```json", "")
        result = result.replace("```", "")

        data = json.loads(result)

        return {
            "name": data.get("name", "Unknown Customer"),
            "phone": data.get("phone", ""),
            "email": data.get("email", ""),
            "product": data.get("product", subject or "New Inquiry"),
            "description": data.get("description", ""),
            "address": data.get("address", ""),
            "city": data.get("city", ""),
            "state": data.get("state", ""),
            "pincode": data.get("pincode", ""),
            "country": data.get("country", "")
        }

    except Exception as e:

        print("AI ERROR:", str(e))

        return {
            "name": "Unknown Customer",
            "phone": "",
            "email": "",
            "product": subject or "New Inquiry",
            "description": "",
            "address": "",
            "city": "",
            "state": "",
            "pincode": "",
            "country": ""
        }

# =========================
# AI REPLY
# =========================
def generate_ai_reply(email_text, lead_data):

    prompt = f"""
You are a professional pharma sales assistant.

Generate a professional reply email.

CUSTOMER EMAIL:
{email_text}

CUSTOMER DATA:
{json.dumps(lead_data, indent=2)}

RULES:
- Professional
- Human sounding
- Under 120 words
- No fake commitments
- No fake pricing
- Thank customer
"""

    try:

        response = client.chat.completions.create(

            model="mistralai/mistral-7b-instruct:free",

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.4,
            max_tokens=300
        )

        return response.choices[0].message.content.strip()

    except Exception as e:

        print("REPLY ERROR:", str(e))

        return """
Dear Customer,

Thank you for your inquiry.

Our sales team will contact you shortly.

Regards,
Sales Team
"""

# =========================
# VALIDATE
# =========================
def validate(data):

    if data.get("phone"):

        data["phone"] = re.sub(
            r'[^\d+]',
            '',
            data["phone"]
        )

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
# CREATE ODOO LEAD
# =========================
def create_odoo_lead(data, ai_reply):

    try:

        # LOGIN
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

        if not uid:

            return 0, "Odoo Authentication Failed"

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object",
            allow_none=True
        )

        # =========================
        # CREATE CONTACT
        # =========================
        partner_id = False

        if data.get("email") or data.get("name"):

            partner_vals = {
                'name': data.get("name") or "Unknown Customer",
                'email': data.get("email") or "",
                'phone': data.get("phone") or "",
                'street': data.get("address") or "",
                'city': data.get("city") or "",
                'zip': data.get("pincode") or "",
            }

            partner_id = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'res.partner',
                'create',
                [partner_vals]
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

            desc_text += "\n\nAddress: " + ", ".join(valid_address)

        # =========================
        # CREATE LEAD
        # =========================
        lead_vals = {
            'name': data.get('product') or "New Inquiry",
            'partner_id': partner_id,
            'contact_name': data.get('name') or "Unknown Customer",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'street': data.get('address') or "",
            'city': data.get('city') or "",
            'zip': data.get('pincode') or "",
            'description': desc_text,
        }

        lead_id = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'crm.lead',
            'create',
            [lead_vals]
        )

        # =========================
        # DEFAULT REPLY
        # =========================
        if not ai_reply:

            ai_reply = """
Dear Customer,

Thank you for your inquiry.

Our sales team will contact you shortly.

Regards,
Sales Team
"""

        # =========================
        # CHATTER
        # =========================
        chatter_html = f"""
<div>
<h3>🤖 AI Auto Reply Sent to Customer</h3>
<p>{ai_reply.replace(chr(10), '<br/>')}</p>
</div>
"""

        models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'crm.lead',
            'message_post',
            [[lead_id]],
            {
                'body': chatter_html,
                'message_type': 'comment'
            }
        )

        # =========================
        # SEND EMAIL
        # =========================
        if data.get("email"):

            email_html = f"""
<div style="font-family: Arial; font-size:14px;">
<p>{ai_reply.replace(chr(10), '<br/>')}</p>
</div>
"""

            mail_vals = {
                'subject': f"Re: {data.get('product') or 'Your Inquiry'}",
                'body_html': email_html,
                'email_to': data.get("email"),
                'email_from': ODOO_USERNAME,
                'auto_delete': False,
            }

            mail_id = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'create',
                [mail_vals]
            )

            models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'send',
                [[mail_id]]
            )

        return int(lead_id), ""

    except Exception as e:

        print("ODOO ERROR:", str(e))

        return 0, str(e)

# =========================
# MAIN API
# =========================
@app.post("/extract")
def extract(request: EmailRequest):

    text = clean_html(request.text)

    ai_data = ai_extract(
        text,
        request.subject
    )

    regex_data = regex_extract(text)

    data = merge(ai_data, regex_data)

    data = validate(data)

    print("FINAL DATA:", data)

    has_contact = bool(
        data.get("phone") or data.get("email")
    )

    if not has_contact:

        data["odoo_error"] = "Skipped incomplete lead"

        return data

    ai_reply = generate_ai_reply(
        text,
        data
    )

    data["ai_reply"] = ai_reply

    lead_id, error = create_odoo_lead(
        data,
        ai_reply
    )

    data["odoo_lead_id"] = lead_id

    if error:

        data["odoo_error"] = error

    return data
