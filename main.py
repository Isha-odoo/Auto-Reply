from fastapi import FastAPI
from pydantic import BaseModel
import re
import os
import json
import xmlrpc.client
from openai import OpenAI

app = FastAPI()

# =====================================================
# OPENROUTER CONFIG
# =====================================================
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

MODEL_NAME = "google/gemma-3-27b-it:free"

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

    text = re.sub('<.*?>', '\n', raw_html)

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

Extract the following details from the email.

RULES:
- Ignore IndiaMART support numbers/emails
- Ignore platform contact details
- Extract actual buyer details only
- Return ONLY valid JSON
- If value unavailable return ""

JSON FORMAT:
{{
"name":"",
"phone":"",
"email":"",
"product":"",
"description":"",
"address":"",
"city":"",
"state":"",
"pincode":"",
"country":""
}}

EMAIL:
{text}
"""

    try:

        response = client.chat.completions.create(

            model=MODEL_NAME,

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.1
        )

        result = response.choices[0].message.content.strip()

        print("RAW AI:", result)

        result = result.replace("```json", "")
        result = result.replace("```", "")
        result = result.strip()

        return json.loads(result)

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
# AI EMAIL REPLY
# =====================================================
def generate_ai_reply(email_text, lead_data):

    prompt = f"""
You are a professional pharma sales assistant.

Write a professional email reply.

CUSTOMER EMAIL:
{email_text}

CUSTOMER DATA:
{json.dumps(lead_data, indent=2)}

RULES:
- Professional
- Human sounding
- Under 120 words
- Thank customer
- Mention product if available
- No fake pricing
- No fake promises
- Plain email format only
"""

    try:

        response = client.chat.completions.create(

            model=MODEL_NAME,

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.4
        )

        reply = response.choices[0].message.content.strip()

        print("AI REPLY:", reply)

        return reply

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

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object",
            allow_none=True
        )

        # =====================================================
        # CREATE CONTACT
        # =====================================================
        partner_id = None

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
        # LEAD VALUES
        # =====================================================
        lead_vals = {
            'name': data.get('product') or "Website Inquiry",
            'partner_id': partner_id,
            'contact_name': data.get('name') or "Unknown Customer",
            'phone': data.get('phone') or "",
            'email_from': data.get('email') or "",
            'street': data.get('address') or "",
            'city': data.get('city') or "",
            'zip': data.get('pincode') or "",
            'description': desc_text.strip(),
        }

        # =====================================================
        # CREATE LEAD
        # =====================================================
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

        # =====================================================
        # SEND EMAIL
        # =====================================================
        if data.get("email"):

            email_html = f"""
<div style="font-family:Arial;font-size:14px;">
<p>{ai_reply.replace(chr(10), '<br/>')}</p>
</div>
"""

            mail_values = {
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
                [mail_values]
            )

            # FORCE SEND
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
    # AI + REGEX EXTRACTION
    # =====================================================
    ai_data = ai_extract(text)

    regex_data = regex_extract(text)

    data = merge(ai_data, regex_data)

    data = validate(data)

    # =====================================================
    # FALLBACKS
    # =====================================================
    if not data.get("name"):

        data["name"] = "Unknown Customer"

    if not data.get("product"):

        data["product"] = request.subject or "Website Inquiry"

    print("FINAL DATA:", data)

    # =====================================================
    # VALIDATION
    # =====================================================
    has_contact = bool(
        data.get("phone") or data.get("email")
    )

    if not has_contact:

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
````
