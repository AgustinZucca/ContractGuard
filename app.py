import streamlit as stMore actions
import openai
import pdfplumber
import docx
import os
import stripe
import hashlib
import requests
from io import BytesIO
from dotenv import load_dotenv
from datetime import datetime
from fpdf import FPDF

load_dotenv()

# Streamlit UI
st.set_page_config(page_title="ContractGuard - Contract Analyzer", layout="centered")

# Load API keys
stripe.api_key = st.secrets["api_key"]
openai_api_key = st.secrets["openai_api_key"]
supabase_url = st.secrets["supabase_url"]
supabase_key = st.secrets["supabase_key"]

st.markdown("""
# 📄 **ContractGuard**
### _Don't sign blind._

Upload your contract and get a clear, AI-powered summary with key clauses, red flags, and what to change — in seconds.

✅ Understand payment terms and scope  
🚩 Spot risky or vague language  
🛠️ Know what to renegotiate  
📱 Optimized for mobile  
🔐 One-time payment of **$5**

---
""")

# Set product price and ID
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"
REAL_URL = "https://contractguard.streamlit.app"

# Session state to persist contract text and upload across reruns
st.session_state.setdefault("contract_text", "")
st.session_state.setdefault("uploaded_filename", "")
st.session_state.setdefault("analysis_output", "")
st.session_state.setdefault("file_hash", "")
st.session_state.setdefault("checkout_url", None)

# Helper to extract text and hash file
def extract_text_and_hash(file):
    file_bytes = file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    file.seek(0)
    if file.type == "application/pdf":
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    elif file.type in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        doc = docx.Document(BytesIO(file_bytes))
        text = "\n".join([para.text for para in doc.paragraphs])
    else:
        text = "Unsupported file type."
    return text, file_hash

# GPT Prompt
PROMPT_TEMPLATE = """
You are a senior legal advisor specializing in contract review. Provide a professional, concise summary of the following contract:

1. Summary of key clauses: Payment Terms, Termination, Scope of Work, and any others found.
2. Identify unclear or risky language with specific quotes and short explanations.
3. Assign a Risk Level (Low / Medium / High) with reasoning.
4. Provide direct suggestions for improvements or negotiation points a freelancer or small business should consider.

Respond in markdown format with clear headers and bullet points.

Contract:
"""

# GPT Analysis using new OpenAI SDK
from openai import OpenAI
client = OpenAI(api_key=openai_api_key)

def analyze_contract(text):
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "user", "content": PROMPT_TEMPLATE + text[:8000]}
        ],
        temperature=0.3
    )
    return response.choices[0].message.content

# Supabase helpers
def get_summary_by_hash(file_hash):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    url = f"{supabase_url}/rest/v1/summaries?file_hash=eq.{file_hash}"
    r = requests.get(url, headers=headers)
    if r.status_code == 200 and len(r.json()) > 0:
        return r.json()[0].get("summary", "")
    return ""

def file_already_paid(file_hash):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    url = f"{supabase_url}/rest/v1/paid_files?file_hash=eq.{file_hash}"
    r = requests.get(url, headers=headers)
    return r.status_code == 200 and len(r.json()) > 0

def save_uploaded_contract(file_hash, contract_text):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    data = {"file_hash": file_hash, "text": contract_text}
    url = f"{supabase_url}/rest/v1/uploaded_contracts"
    requests.post(url, json=data, headers=headers)

def save_summary(file_hash, summary_text):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    data = {"file_hash": file_hash, "summary": summary_text}
    url = f"{supabase_url}/rest/v1/summaries"
    requests.post(url, json=data, headers=headers)

def get_contract_text_by_hash(file_hash):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    url = f"{supabase_url}/rest/v1/uploaded_contracts?file_hash=eq.{file_hash}"
    r = requests.get(url, headers=headers)
    if r.status_code == 200 and len(r.json()) > 0:
        return r.json()[0].get("text", "")
    return ""

# Handle payment redirect with file hash in query
if st.query_params.get("success") and st.query_params.get("hash"):
    file_hash = st.query_params.get("hash")
    text = get_contract_text_by_hash(file_hash)
    if text:
        st.session_state.contract_text = text
        st.session_state.file_hash = file_hash
        st.session_state.uploaded_filename = "Recovered after payment"
        # Fetch existing summary if any
        existing = get_summary_by_hash(file_hash)
        if existing:
            st.session_state.analysis_output = existing
        else:
            st.success("✅ Payment confirmed! Analyzing your contract...")
            with st.spinner("Analyzing..."):
                output = analyze_contract(text)
                st.session_state.analysis_output = output
                save_summary(file_hash, output)

# Upload section
uploaded_file = st.file_uploader("Upload a contract (PDF or Word)", type=["pdf", "docx"])
if uploaded_file:
    contract_text, file_hash = extract_text_and_hash(uploaded_file)
    st.session_state.contract_text = contract_text
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.file_hash = file_hash
    st.session_state.analysis_output = ""
    save_uploaded_contract(file_hash, contract_text)

    # Check if summary exists and load it
    existing = get_summary_by_hash(file_hash)
    if existing:
        st.session_state.analysis_output = existing

# Show preview if contract is available
if st.session_state.contract_text:
    st.markdown("---")
    if st.session_state.uploaded_filename:
        st.info(f"✅ Uploaded: {st.session_state.uploaded_filename}")
    st.write("### Contract Preview")
    st.code(st.session_state.contract_text[:1000])

    already_paid = file_already_paid(st.session_state.file_hash)

    # If summary already exists, show it immediately
    if st.session_state.analysis_output:
        st.markdown("---")
        st.subheader("🔍 Previously Saved Summary & Suggestions")
        st.markdown(st.session_state.analysis_output)
    # Otherwise, proceed with pay+generate flow
    elif not already_paid:
        st.markdown("### 🔐 Unlock Full Analysis for $5")
        if st.button("Generate Stripe Link"):
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': PRODUCT_NAME,
                        },
                        'unit_amount': PRODUCT_PRICE,
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=f"{REAL_URL}?success=true&hash={st.session_state.file_hash}",
                cancel_url=f"{REAL_URL}?canceled=true"
            )
            st.session_state.checkout_url = session.url


        if st.session_state.checkout_url:
            st.markdown("---")
            st.success("✅ Stripe checkout link generated")
            st.markdown(
                f"[👉 Click here to securely pay with Stripe]({st.session_state.checkout_url})",
                unsafe_allow_html=True
            )

# Show analysis if we have it (either just generated or fetched)
if st.session_state.analysis_output:
    st.markdown("---")
    st.subheader("🔍 Contract Summary & Suggestions")
    st.markdown(st.session_state.analysis_output)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)
    for line in st.session_state.analysis_output.split("\n"):
        pdf.multi_cell(0, 10, line)

    pdf_output = pdf.output(dest='S').encode('latin1')
    pdf_buffer = BytesIO(pdf_output)

    if st.download_button("📄 Download as PDF", data=pdf_buffer, file_name="contract_summary.pdf", mime="application/pdf"):
        st.success("Download started")

elif st.query_params.get("canceled"):
    st.warning("⚠️ Payment was canceled. Try again when ready.")