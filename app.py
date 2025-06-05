import os
import streamlit as st
import openai
import pdfplumber
import docx
import stripe
import hashlib
import requests
from io import BytesIO
from dotenv import load_dotenv
from datetime import datetime
from fpdf import FPDF

# --- Setup ---
load_dotenv()
st.set_page_config(page_title="ContractGuard - Contract Analyzer", layout="centered")

# --- API Keys (from ENV) ---
stripe_api_key = os.getenv("api_key")
openai_api_key = os.getenv("openai_api_key")
supabase_url = os.getenv("supabase_url")
supabase_key = os.getenv("supabase_key")

# --- Security: Early Key Validation ---
if not all([stripe_api_key, openai_api_key, supabase_url, supabase_key]):
    st.error("Missing critical API key or URL. Please contact support.")
    st.stop()
stripe.api_key = stripe_api_key

# --- Constants ---
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"
REAL_URL = "https://contractguard.streamlit.app"

# --- Session State Defaults ---
for k, v in [
    ("contract_text", ""),
    ("uploaded_filename", ""),
    ("analysis_output", ""),
    ("file_hash", ""),
    ("checkout_url", None),
    ("language", "en"),
    ("payment_processed", False),
]:
    st.session_state.setdefault(k, v)

# --- Multilingual Prompts (unchanged for brevity) ---
PROMPTS = {
    "en": """...""",
    "es": """...""",
    "pt": """..."""
}
# (Use your existing PROMPTS dictionary here.)

# --- Helper Functions ---
def extract_text_and_hash(uploaded_file):
    if uploaded_file.type not in ["application/pdf",
                                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        return "Unsupported file type.", None
    data = uploaded_file.read()
    file_hash = hashlib.sha256(data).hexdigest()
    uploaded_file.seek(0)
    text = ""
    try:
        if uploaded_file.type == "application/pdf":
            with pdfplumber.open(BytesIO(data)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            doc = docx.Document(BytesIO(data))
            text = "\n".join(para.text for para in doc.paragraphs)
    except Exception as e:
        st.error("File extraction failed. Please try another file.")
        return "", None
    return text, file_hash

from openai import OpenAI
client = OpenAI(api_key=openai_api_key)

def analyze_contract(text):
    def split_chunks(t, max_chars=12000):
        paras = t.split("\n\n")
        chunks, current = [], ""
        for p in paras:
            if len(current) + len(p) + 2 <= max_chars:
                current += p + "\n\n"
            else:
                chunks.append(current)
                current = p + "\n\n"
        if current:
            chunks.append(current)
        return chunks

    prompt = PROMPTS[st.session_state.language]
    chunks = split_chunks(text)
    try:
        if len(chunks) == 1:
            full = prompt + text[:8000]
            resp = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": full}],
                temperature=0.3
            )
            return resp.choices[0].message.content
        else:
            partials = []
            for c in chunks:
                full = prompt + c[:8000]
                resp = client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": full}],
                    temperature=0.3
                )
                partials.append(resp.choices[0].message.content)
            combined = "\n\n".join(partials)
            final_prompt = prompt + combined[:8000]
            final_resp = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": final_prompt}],
                temperature=0.3
            )
            return final_resp.choices[0].message.content
    except Exception:
        st.error("Contract analysis failed. Please try again later or contact support.")
        return ""

def supabase_get(table, query=""):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    try:
        r = requests.get(f"{supabase_url}/rest/v1/{table}{query}", headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return r.json()
        else:
            st.warning("Database fetch failed.")
            return []
    except Exception:
        st.warning("Supabase connection error.")
        return []

def supabase_insert(table, data, upsert=False):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates" if upsert else "return=representation"
    }
    try:
        r = requests.post(f"{supabase_url}/rest/v1/{table}", json=data, headers=headers, timeout=10)
        if r.status_code not in (200, 201):
            st.warning(f"Supabase insert failed: {r.status_code}")
    except Exception:
        st.warning("Supabase insert error.")

def file_already_paid(file_hash):
    return len(supabase_get("paid_files", f"?file_hash=eq.{file_hash}")) > 0

def save_uploaded_contract(file_hash, text):
    supabase_insert("uploaded_contracts", {
        "file_hash": file_hash,
        "text": text,
        "created_at": datetime.utcnow().isoformat()
    }, upsert=True)

def get_contract_text_by_hash(file_hash):
    recs = supabase_get("uploaded_contracts", f"?file_hash=eq.{file_hash}")
    return recs[0]["text"] if recs else ""

def get_summary_by_hash(file_hash):
    recs = supabase_get("summaries", f"?file_hash=eq.{file_hash}")
    return recs[0]["summary"] if recs else ""

def save_summary(file_hash, summary):
    supabase_insert("summaries", {
        "file_hash": file_hash,
        "summary": summary,
        "created_at": datetime.utcnow().isoformat()
    }, upsert=True)

# --- ContractGuard Header & Details (unchanged) ---

st.markdown("""
# 📄 **ContractGuard**
### _Don't sign blind._

Upload any contract and get a clear, AI-powered summary with key clauses and potential red flags—all in seconds.

✅ Understand payment terms, scope, and liability  
🚩 Spot risky language or unclear terms  
🛠️ Get direct suggestions on what to renegotiate  
📱 Fully responsive and mobile-friendly  
🔐 One-time payment of **$5** — no subscription needed

---
""")

# --- Language Selection ---
lang_display = st.selectbox(
    "Choose summary language:",
    ["English", "Español", "Português"],
    index=["English", "Español", "Português"].index(
        {"en": "English", "es": "Español", "pt": "Português"}[st.session_state.language]
    )
)
lang_map = {"English": "en", "Español": "es", "Português": "pt"}
st.session_state.language = lang_map[lang_display]
st.markdown("---")

# --- Handle Stripe Redirect via query_params ---
success_hash = None
if st.query_params.get("success") and st.query_params.get("hash"):
    success_hash = st.query_params.get("hash")[0]
    st.session_state.file_hash = success_hash
    st.session_state.contract_text = get_contract_text_by_hash(success_hash)
    st.session_state.uploaded_filename = "Recovered after payment"
    existing_summary = get_summary_by_hash(success_hash)
    if existing_summary:
        st.session_state.analysis_output = existing_summary
    elif st.session_state.contract_text:
        st.success("✅ Payment confirmed! Analyzing your contract…")
        with st.spinner("Analyzing…"):
            output = analyze_contract(st.session_state.contract_text)
            st.session_state.analysis_output = output
            save_summary(success_hash, output)
    st.experimental_set_query_params()
    st.session_state.checkout_url = None

# --- Upload Section: With Rerun Fix ---
uploaded_file = st.file_uploader("Choose a PDF or Word (.docx) file:", type=["pdf", "docx"])

if uploaded_file and not st.session_state.get("just_reran"):
    ctx, fh = extract_text_and_hash(uploaded_file)
    if not fh:
        st.stop()
    st.session_state.contract_text = ctx
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.file_hash = fh
    save_uploaded_contract(fh, ctx)
    existing = get_summary_by_hash(fh)
    st.session_state.analysis_output = existing if existing else ""
    st.session_state.checkout_url = None
    st.session_state.just_reran = True
    st.experimental_rerun()

if st.session_state.get("just_reran"):
    del st.session_state["just_reran"]


# --- Show Preview & Flow ---
if st.session_state.contract_text:
    st.markdown("---")
    if st.session_state.uploaded_filename:
        st.info(f"✅ Uploaded: {st.session_state.uploaded_filename}")
    st.write("### Contract Preview")
    st.code(st.session_state.contract_text[:1000])

    # If summary exists, render only once
    if st.session_state.analysis_output:
        st.markdown("---")
        st.subheader("🔍 Contract Summary & Suggestions")
        st.markdown(st.session_state.analysis_output)
        # Copy to Clipboard
        if st.button("📋 Copy to Clipboard"):
            st.markdown(
                f"<textarea id='clip' style='opacity:0;'>{st.session_state.analysis_output}</textarea>"
                "<script>document.getElementById('clip').select();document.execCommand('copy');</script>",
                unsafe_allow_html=True
            )
            st.success("Copied to clipboard!")
        # Download as PDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", size=12)
        for line in st.session_state.analysis_output.split("\n"):
            pdf.multi_cell(0, 10, line)
        buffer = BytesIO(pdf.output(dest='S').encode('latin1'))
        if st.download_button("📄 Download as PDF", data=buffer, file_name="contract_summary.pdf", mime="application/pdf"):
            st.success("Download started")
    else:
        # Check if paid (should work for both fresh and paid)
        if file_already_paid(st.session_state.file_hash):
            st.success("✅ Payment previously confirmed! Analyzing your contract…")
            with st.spinner("Analyzing…"):
                output = analyze_contract(st.session_state.contract_text)
                st.session_state.analysis_output = output
                save_summary(st.session_state.file_hash, output)
        else:
            # Not paid yet
            st.markdown("### 🔐 Unlock Full Analysis for $5")
            if st.button("Generate Stripe Link"):
                try:
                    session = stripe.checkout.Session.create(
                        payment_method_types=['card'],
                        line_items=[{
                            'price_data': {
                                'currency': 'usd',
                                'product_data': {'name': PRODUCT_NAME},
                                'unit_amount': PRODUCT_PRICE,
                            },
                            'quantity': 1,
                        }],
                        mode='payment',
                        success_url=f"{REAL_URL}?success=true&hash={st.session_state.file_hash}",
                        cancel_url=f"{REAL_URL}?canceled=true"
                    )
                    st.session_state.checkout_url = session.url
                except Exception:
                    st.error("Payment link generation failed. Please try again.")
            if st.session_state.checkout_url:
                st.markdown("---")
                st.success("✅ Stripe checkout link generated")
                st.markdown(
                    f"[👉 Click here to securely pay with Stripe]({st.session_state.checkout_url})",
                    unsafe_allow_html=True
                )

elif st.query_params.get("canceled"):
    st.warning("⚠️ Payment was canceled. Try again.")
