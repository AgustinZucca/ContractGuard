import streamlit as st
import os
import stripe
import requests
import hashlib
import pdfplumber
import docx
from io import BytesIO
from dotenv import load_dotenv
from fpdf import FPDF
from openai import OpenAI

# ─── LOAD LOCAL .env (optional) ───────────────────────────────────────────────────
load_dotenv()

# ─── ENVIRONMENT VARIABLE CHECK ───────────────────────────────────────────────────
REQUIRED = ["STRIPE_API_KEY", "OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"]
missing = [v for v in REQUIRED if not os.getenv(v)]
if missing:
    st.error(f"Missing environment variables: {', '.join(missing)}")
    st.stop()

# ─── CONFIG ───────────────────────────────────────────────────────────────────────
stripe.api_key    = os.getenv("STRIPE_API_KEY")
openai_api_key    = os.getenv("OPENAI_API_KEY")
supabase_url      = os.getenv("SUPABASE_URL").rstrip("/")  # no trailing slash
supabase_key      = os.getenv("SUPABASE_KEY")

REAL_URL          = "https://contractguard-5sm3.onrender.com/"
PRODUCT_PRICE     = 500  # $5.00 in cents
PRODUCT_NAME      = "Contract Analysis"

# ─── STREAMLIT SETUP ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="ContractGuard", layout="centered")
st.markdown("""
# 📄 **ContractGuard**
### _Don't sign blind._

Upload your contract and get a clear, AI-powered summary — in seconds.
🔐 One-time payment of **$5**
---
""")

# ─── SESSION STATE ───────────────────────────────────────────────────────────────
for key, default in {
    "contract_text": "",
    "uploaded_filename": "",
    "analysis_output": "",
    "file_hash": "",
    "checkout_url": None,
    "just_paid": False
}.items():
    st.session_state.setdefault(key, default)

# ─── HELPERS ──────────────────────────────────────────────────────────────────────
def extract_text_and_hash(uploaded_file):
    data = uploaded_file.read()
    h = hashlib.sha256(data).hexdigest()
    uploaded_file.seek(0)
    if uploaded_file.type == "application/pdf":
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    else:  # docx
        doc = docx.Document(BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
    return text, h

PROMPT_TEMPLATE = """
You are a senior legal advisor. Provide a concise markdown summary of this contract:

1. Key clauses: Payment Terms, Termination, Scope of Work, etc.
2. Unclear/risky language with quotes + brief notes.
3. Risk Level (Low/Medium/High) with reasoning.
4. Suggestions for negotiation.

Contract:
"""

client = OpenAI(api_key=openai_api_key)

def analyze_contract(text):
    resp = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role":"user","content":PROMPT_TEMPLATE + text[:8000]}],
        temperature=0.3
    )
    return resp.choices[0].message.content

def supabase_get(table, eq_field, eq_value):
    hdr = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    url = f"{supabase_url}/rest/v1/{table}?{eq_field}=eq.{eq_value}"
    r = requests.get(url, headers=hdr)
    return r.json() if r.status_code==200 else []

def get_summary_by_hash(h):
    rows = supabase_get("summaries", "file_hash", h)
    return rows[0]["summary"] if rows else ""

def file_already_paid(h):
    return bool(supabase_get("paid_files", "file_hash", h))

def save_to_table(table, payload):
    hdr = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    requests.post(f"{supabase_url}/rest/v1/{table}", json=payload, headers=hdr)

def save_uploaded_contract(h, txt):
    save_to_table("uploaded_contracts", {"file_hash": h, "text": txt})

def save_summary(h, summary):
    save_to_table("summaries", {"file_hash": h, "summary": summary})

def save_paid_file(h):
    save_to_table("paid_files", {"file_hash": h})

def get_contract_text_by_hash(h):
    rows = supabase_get("uploaded_contracts", "file_hash", h)
    return rows[0]["text"] if rows else ""

# ─── HANDLE STRIPE REDIRECT ────────────────────────────────────────────────────────
if st.query_params.get("success") and st.query_params.get("hash"):
    h = st.query_params["hash"]
    txt = get_contract_text_by_hash(h)
    if txt:
        st.session_state.contract_text   = txt
        st.session_state.file_hash       = h
        st.session_state.uploaded_filename = "After Payment"
        existing = get_summary_by_hash(h)
        if existing:
            st.session_state.analysis_output = existing
            st.session_state.just_paid = False
        else:
            st.session_state.just_paid = True
            st.success("✅ Payment confirmed! Analyzing…")
            with st.spinner("Analyzing…"):
                out = analyze_contract(txt)
                st.session_state.analysis_output = out
                save_summary(h, out)
                save_paid_file(h)

# ─── UPLOAD WIDGET ───────────────────────────────────────────────────────────────
up = st.file_uploader("Upload PDF or DOCX", type=["pdf","docx"])
if up:
    txt, h = extract_text_and_hash(up)
    st.session_state.update({
        "contract_text": txt,
        "file_hash": h,
        "uploaded_filename": up.name,
        "analysis_output": "",
        "just_paid": False
    })
    save_uploaded_contract(h, txt)
    existing = get_summary_by_hash(h)
    if existing:
        st.session_state.analysis_output = existing

# ─── PREVIEW / PAYMENT FLOW / PREVIOUSLY SAVED ────────────────────────────────────
if st.session_state.contract_text:
    st.markdown("---")
    st.info(f"📄 Uploaded: {st.session_state.uploaded_filename}")
    st.write("### Contract Preview")
    st.code(st.session_state.contract_text[:500])

    paid = file_already_paid(st.session_state.file_hash)

    # Show “Previously Saved” only if we did NOT just generate a new summary
    if st.session_state.analysis_output and not st.session_state.just_paid:
        st.markdown("---")
        st.subheader("🔍 Previously Saved Summary & Suggestions")
        st.markdown(st.session_state.analysis_output)

    elif not paid:
        st.markdown("### 🔐 Unlock Full Analysis for $5")
        if st.button("Generate Stripe Link"):
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data":{
                        "currency":"usd",
                        "product_data":{"name":PRODUCT_NAME},
                        "unit_amount":PRODUCT_PRICE
                    },
                    "quantity":1
                }],
                mode="payment",
                success_url=f"{REAL_URL}?success=true&hash={st.session_state.file_hash}",
                cancel_url=f"{REAL_URL}?canceled=true"
            )
            st.session_state.checkout_url = session.url

        if st.session_state.checkout_url:
            st.markdown("---")
            st.success("✅ Stripe link generated")
            st.markdown(f"[Pay Now →]({st.session_state.checkout_url})", unsafe_allow_html=True)

# ─── FINAL ANALYSIS + DOWNLOAD & RESET ─────────────────────────────────────────────
if st.session_state.analysis_output:
    st.markdown("---")
    st.subheader("🔍 Contract Summary & Suggestions")
    st.markdown(st.session_state.analysis_output)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_font("Arial", size=12)
    for line in st.session_state.analysis_output.split("\n"):
        pdf.multi_cell(0, 8, line)

    buf = BytesIO(pdf.output(dest="S").encode("latin1"))
    if st.download_button("📄 Download as PDF", buf, "summary.pdf", "application/pdf"):
        st.success("Download started")

    # clear the just_paid flag so next visit shows “Previously Saved”
    st.session_state.pop("just_paid", None)

elif st.query_params.get("canceled"):
    st.warning("⚠️ Payment canceled.")
