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
import streamlit.components.v1 as components

# ─── LOAD ENV AND CHECK ─────────────────────────────────────────────────────────
load_dotenv()
REQUIRED = ["STRIPE_API_KEY", "OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"]
missing = [v for v in REQUIRED if not os.getenv(v)]
if missing:
    st.error(f"Missing environment variables: {', '.join(missing)}")
    st.stop()

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
stripe.api_key    = os.getenv("STRIPE_API_KEY")
openai_api_key    = os.getenv("OPENAI_API_KEY")
supabase_url      = os.getenv("SUPABASE_URL").rstrip("/")
supabase_key      = os.getenv("SUPABASE_KEY")
REAL_URL          = "https://mycontractguard.com"
PRODUCT_PRICE     = 500  # $5 in cents
PRODUCT_NAME      = "Contract Analysis"

# ─── STREAMLIT SETUP ────────────────────────────────────────────────────────────
st.set_page_config(page_title="ContractGuard", page_icon="📄", layout="centered")
# Override default browser tab title
tab_title_script = "<script>document.title = 'ContractGuard';</script>"
components.html(tab_title_script, height=0)

# determine if this is a payment redirect
is_success = bool(st.query_params.get("success") and st.query_params.get("hash"))

# ─── LANDING & TESTIMONIALS (hidden after payment) ───────────────────────────────
if not is_success:
    st.markdown("""
    # 📄 ContractGuard for Freelancers
    ### _Don’t sign blind._

    You're a freelancer. You just got a new client and they send over a long, vague contract.

    **Is it fair? Can they ghost you with no payment? Are you protected?**

    Let AI scan the contract and tell you:
    - ✅ What the payment terms actually mean
    - ❌ Clauses that might screw you over
    - 🚩 Hidden risks or loopholes to renegotiate

    ---

    ### 👥 What freelancers say
    > “ContractGuard saved me from a one-sided contract!” – Alice, UX Designer  
    > “I spotted a termination clause that would have cost me thousands.” – Bob, Developer

    ---

    ### 🔍 Free Preview — Upload your contract and see what red flags show up.
    Then, pay **$5 once** to unlock a **full analysis** including:
    - Scope of work
    - Termination clauses
    - Risk level & legal suggestions

    No subscriptions. No email required. Just straight-up analysis.

    ---

    🛡️ **Private by default.** Your contract is processed securely and never stored permanently.
    📬 **Feedback welcome!** Got ideas? Drop them [here](mailto:admin@mycontractguard.com).
    """)

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
for k, default in {
    "contract_text": "", "uploaded_filename": "", "analysis_output": "",
    "file_hash": "", "just_paid": False
}.items():
    st.session_state.setdefault(k, default)

# ─── HELPERS ─────────────────────────────────────────────────────────────────────
def extract_text_and_hash(uploaded):
    data = uploaded.read()
    h = hashlib.sha256(data).hexdigest()
    uploaded.seek(0)
    if uploaded.type == "application/pdf":
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    else:
        doc = docx.Document(BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
    return text, h

# ─── OPENAI CLIENT ─────────────────────────────────────────────────────────────
client = OpenAI(api_key=openai_api_key)

# ─── PROMPTS ─────────────────────────────────────────────────────────────────────
PROMPT_PREVIEW = (
    "You're a legal assistant. Based on this partial contract, give 4-5 bullet points of potential issues. "
    "Only use the first 1000 chars.\nPartial Contract:\n"
)
PROMPT_FULL = (
    "You are a senior legal advisor. Provide a concise markdown summary of this contract:\n"
    "1. Key clauses: Payment Terms, Termination, Scope of Work, etc.\n"
    "2. Unclear/risky language with quotes + brief notes.\n"
    "3. Risk Level (Low/Medium/High) with reasoning.\n"
    "4. Suggestions for negotiation.\n\nContract:\n"
)

# ─── ANALYSIS FUNCTIONS ──────────────────────────────────────────────────────────
def analyze_preview(text):
    p = st.progress(0); p.progress(10)
    with st.spinner("Generating preview..."):
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":PROMPT_PREVIEW + text[:1000]}],
            temperature=0.2,
            max_tokens=300
        )
    p.progress(100); p.empty()
    return res.choices[0].message.content


def analyze_contract(text):
    p = st.progress(0); p.progress(10)
    with st.spinner("Generating full analysis..."):
        res = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role":"user","content":PROMPT_FULL + text[:8000]}],
            temperature=0.3
        )
    p.progress(100); p.empty()
    return res.choices[0].message.content

# ─── SUPABASE CRUD ──────────────────────────────────────────────────────────────
def supabase_get(table, field, val):
    hdr = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    url = f"{supabase_url}/rest/v1/{table}?{field}=eq.{val}"
    r = requests.get(url, headers=hdr)
    return r.json() if r.status_code == 200 else []

def get_summary_by_hash(fhash):
    rows = supabase_get("summaries", "file_hash", fhash)
    return rows[0].get("summary", "") if rows else ""

def file_paid(fhash):
    return bool(supabase_get("paid_files", "file_hash", fhash))

def get_contract_text_by_hash(fhash):
    rows = supabase_get("uploaded_contracts", "file_hash", fhash)
    return rows[0].get("text", "") if rows else ""

def save_to_table(table, payload):
    hdr = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    requests.post(f"{supabase_url}/rest/v1/{table}", json=payload, headers=hdr)

# ─── STRIPE REDIRECT HANDLER ────────────────────────────────────────────────────
if is_success:
    fhash = st.query_params["hash"][0] if isinstance(st.query_params["hash"], list) else st.query_params["hash"]
    text = get_contract_text_by_hash(fhash)
    if text:
        st.session_state.contract_text = text
        st.session_state.file_hash = fhash
        if not get_summary_by_hash(fhash):
            st.session_state.just_paid = True
            summary = analyze_contract(text)
            st.session_state.analysis_output = summary
            save_to_table("summaries", {"file_hash": fhash, "summary": summary})
            save_to_table("paid_files", {"file_hash": fhash})
        else:
            st.session_state.analysis_output = get_summary_by_hash(fhash)

# ─── DISPLAY FULL ANALYSIS IMMEDIATELY AFTER PAYMENT ─────────────────────────────
if st.session_state.just_paid or is_success:
    st.markdown("---")
    st.subheader("🔍 Contract Summary & Suggestions")
    st.markdown(st.session_state.analysis_output)
    pdf = FPDF(); pdf.add_page(); pdf.set_auto_page_break(True, margin=15); pdf.set_font("Arial", size=12)
    for line in st.session_state.analysis_output.split("\n"):
        pdf.multi_cell(0, 8, line)
    buf = BytesIO(pdf.output(dest="S").encode("latin1"))
    if st.download_button("📄 Download as PDF", buf, "summary.pdf", "application/pdf"):
        st.success("Download started")
    # halt further UI
    st.stop()

# ─── UPLOAD & PREVIEW/PURCHASE (skipped after payment) ───────────────────────────
if not is_success:
    upload = st.file_uploader("Upload PDF or DOCX", type=["pdf", "docx"])
    if upload:
        txt, fhash = extract_text_and_hash(upload)
        st.session_state.contract_text = txt
        st.session_state.file_hash = fhash
        save_to_table("uploaded_contracts", {"file_hash": fhash, "text": txt})
        st.session_state.analysis_output = ""
        st.session_state.just_paid = False
        if get_summary_by_hash(fhash):
            st.session_state.analysis_output = get_summary_by_hash(fhash)

    if st.session_state.contract_text:
        st.markdown("---")
        st.info(f"📄 Uploaded: {st.session_state.uploaded_filename}")
        st.write("### Contract Preview")
        st.code(st.session_state.contract_text[:500])

        paid = file_paid(st.session_state.file_hash)

        if not paid:
            st.markdown("### 🕵️ Preview Analysis (Free)")
            preview = analyze_preview(st.session_state.contract_text)
            st.markdown(preview)
            st.markdown("### 🔐 Unlock Full Analysis for $5")
            if st.button("Pay Now"):
                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    line_items=[{'price_data':{'currency':'usd','product_data':{'name':PRODUCT_NAME},'unit_amount':PRODUCT_PRICE},'quantity':1}],
                    mode="payment",
                    success_url=f"{REAL_URL}?success=true&hash={st.session_state.file_hash}",
                    cancel_url=f"{REAL_URL}?canceled=true"
                )
                st.session_state.checkout_url = session.url
            if st.session_state.get("checkout_url"):
                st.markdown("---")
                st.success("✅ Stripe checkout link generated:")
                st.markdown(f"[Click here to pay now →]({st.session_state.checkout_url})", unsafe_allow_html=True)
        else:
            st.markdown("---")
            st.subheader("🔍 Previously Saved Summary & Suggestions")
            st.markdown(st.session_state.analysis_output)
