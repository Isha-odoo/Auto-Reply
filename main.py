from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
from openai import OpenAI

app = FastAPI()

# =====================================================
# OPENROUTER CLIENT
# =====================================================
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

# =====================================================
# ODOO CONFIG
# =====================================================
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")

# =====================================================
# REQUEST SCHEMA
# =====================================================
class EmailRequest(BaseModel):
    text: str
    from_email: str = ""
    subject: str = ""

# =====================================================
# HEALTH CHECK
# =====================================================
@app.get("/")
def home():
    return {
        "status": "Live",
        "message": "AI CRM Automation Running"
    }

# =====================================================
# CLEAN HTML
# =====================================================
def clean_html(raw_html):

    text = re.sub(r'<.*?>', '\n', raw_html)

    text = re.sub(r'\n+', '\n', text)

    return text.strip()

# =====================================================
# REGEX EXTRACTION
# =====================================================
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

# =====================================================
# AI EXTRACTION
# =====================================================
def ai_extract(text):

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
- Ignore IndiaMART support details
- Ignore platform phone numbers
- Extract actual customer details
- Return ONLY JSON
- Keep blank if unavailable

EMAIL:
{text}
"""

    try:

        response = client.chat.completions.create(

            model="deepseek/deepseek-chat-v3-0324:free",

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.1
        )

        result = response.choices[0].message.content

        result = result.replace("```json", "")
        result = result.replace("```", "")

        data = json.loads(result)

        return data

    except Exception as e:

        print("AI ERROR:", str(e))

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

# =====================================================
# AI REPLY GENERATOR
# =====================================================
def generate_ai_reply(email_text, lead_data):

    prompt = f"""
You are a professional pharma sales assistant.

Generate a professional customer email reply.

CUSTOMER EMAIL:
{email_text}

LEAD DATA:
{json.dumps(lead_data, indent=2)}

RULES:
- Professional tone
- Human sounding
- Under 120 words
- No fake pricing
- No fake commitments
- Output only email body
"""

    try:

        response = client.chat.completions.create(

            model="deepseek/deepseek-chat-v3-0324:free",

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.4
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

# =====================================================
# VALIDATION
# =====================================================
def validate(data):

    if data.get("phone"):

        data["phone"] = re.sub(
            r'[^\d+]',
            '',
            data["phone"]
        )

    if data.get("email"):

        data["email"] = data["email"].strip()

    return data

# =====================================================
# MERGE LOGIC
# =====================================================
def merge(ai_data, regex_data):

    if not ai_data.get("phone") and regex_data.get("phone"):

        ai_data["phone"] = regex_data["phone"][0]

    if not ai_data.get("email") and regex_data.get("email"):

        ai_data["email"] = regex_data["email"][0]

    return ai_data

# =====================================================
# CREATE ODOO LEAD
# =====================================================
def create_odoo_lead(data, ai_reply):

    try:

        # =====================================================
        # LOGIN
        # =====================================================
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
            return None, "Odoo Authentication Failed"

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object",
            allow_none=True
        )

        # =====================================================
        # SEARCH EXISTING CONTACT
        # =====================================================
        partner_id = None

        if data.get("email"):

            existing_partner = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'res.partner',
                'search',
                [[['email', '=', data.get("email")]]],
                {'limit': 1}
            )

            if existing_partner:
                partner_id = existing_partner[0]

        # =====================================================
        # CREATE CONTACT IF NOT EXISTS
        # =====================================================
        if not partner_id:

            partner_vals = {
                'name': data.get("name") or "Unknown",
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

        # =====================================================
        # DESCRIPTION
        # =====================================================
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

        # =====================================================
        # CREATE LEAD
        # =====================================================
        lead_vals = {
            'name': data.get('product') or "New Lead",
            'partner_id': partner_id,
            'contact_name': data.get('name') or "",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'street': data.get('address') or "",
            'city': data.get('city') or "",
            'zip': data.get('pincode') or "",
            'description': desc_text.strip(),
        }

        lead_id = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'crm.lead',
            'create',
            [lead_vals]
        )

        # =====================================================
        # CHATTER MESSAGE
        # =====================================================
        safe_reply = ai_reply.replace("\n", "<br/>")

        chatter_html = f"""
<div>
<h3>🤖 AI Auto Reply Sent to Customer</h3>
<p>{safe_reply}</p>
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

        # =====================================================
        # SEND EMAIL FROM ODOO
        # =====================================================
        if data.get("email"):

            email_html = f"""
<div style="font-family: Arial; font-size:14px;">
<p>{safe_reply}</p>
</div>
"""

            mail_values = {
                'subject': f"Re: {data.get('product') or 'Your Inquiry'}",
                'body_html': email_html,
                'email_to': data.get("email"),
                'email_from': ODOO_USERNAME,
                'reply_to': ODOO_USERNAME,
                'auto_delete': False,
            }

            mail_id = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'mail.mail',
                'create',
                [mail_values]
            )

            # FORCE SEND MAIL
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

        print("ODOO ERROR:", str(e))

        return None, str(e)

# =====================================================
# MAIN API
# =====================================================
@app.post("/extract")
def extract(request: EmailRequest):

    text = clean_html(request.text)

    # =====================================================
    # EXTRACT DATA
    # =====================================================
    ai_data = ai_extract(text)

    regex_data = regex_extract(text)

    data = merge(ai_data, regex_data)

    data = validate(data)

    print("FINAL DATA:", data)

    # =====================================================
    # VALIDATION
    # =====================================================
    has_contact = bool(
        data.get("phone") or data.get("email")
    )

    has_context = bool(
        data.get("name") or data.get("product")
    )

    if not (has_contact and has_context):

        data["odoo_error"] = "Skipped incomplete lead"

        return data

    # =====================================================
    # GENERATE AI REPLY
    # =====================================================
    ai_reply = generate_ai_reply(
        text,
        data
    )

    data["ai_reply"] = ai_reply

    # =====================================================
    # CREATE ODOO LEAD
    # =====================================================
    lead_id, error = create_odoo_lead(
        data,
        ai_reply
    )

    data["odoo_lead_id"] = lead_id

    if error:
        data["odoo_error"] = error

    return data
