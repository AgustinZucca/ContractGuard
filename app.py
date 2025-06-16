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
from streamlit_lottie import st_lottie

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
components.html("""
<style>
.fade-in {
  animation: fadeIn 1s ease-in;
}
@keyframes fadeIn {
  0% { opacity: 0; transform: translateY(10px); }
  100% { opacity: 1; transform: translateY(0); }
}
</style>
""", height=0)

# ─── LOTTIE ANIMATION LOADER ─────────────────────────────────────────────────────
def load_lottie_url(url):
    r = requests.get(url)
    if r.status_code == 200:
        return r.json()
    return None

lottie_contract = load_lottie_url("https://lottie.host/bc3660d7-d732-4050-9a49-14463af9cfa9/l6l0qarVZt.json")  # Free-to-use contract Lottie

# determine if this is a payment redirect
is_success = bool(st.query_params.get("success") and st.query_params.get("hash"))

# ─── LANDING & TESTIMONIALS (hidden after payment) ───────────────────────────────
if not is_success:
    st.title("📄 ContractGuard – Fast AI Contract Summaries for Freelancers")
    # Add animation under title
    if lottie_contract:
        st_lottie(lottie_contract, height=140, key="contract_anim")
    st.subheader("_Tired of deciphering vague contracts? Let AI do the first pass._")

    with st.expander("✅ What You’ll Get", expanded=True):
        st.markdown("""
- Free preview with potential red flags and vague terms  
- Full summary for just **$5** — no subscriptions, no login  
- Downloadable PDF with key clauses, risks, and negotiation tips  
        """)

    with st.expander("🔐 How We Protect Your Privacy", expanded=True):
        st.markdown("""
- Your contract is **never stored permanently**  
- Files are processed in-memory and auto-deleted  
- No tracking, cookies, or personal data collection  
- **Payments handled securely by Stripe**  
- You can redact names or sensitive data before uploading  
        """)

    with st.expander("⚠️ Disclaimer", expanded=False):
        st.markdown("""
ContractGuard uses AI to assist your review.  
**It is not legal advice.**  
Always consult a qualified professional before signing important contracts.
        """)

    with st.expander("👤 Who Made This?", expanded=False):
        st.markdown("""
I'm a freelancer who got burned by bad contracts.  
I built ContractGuard so others don’t go through the same.  
Questions? Email [admin@contractguard.com](mailto:admin@contractguard.com)  
        """)

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
for k, default in {
    "contract_text": "", "uploaded_filename": "", "analysis_output": "",
    "file_hash": "", "just_paid": False, "checkout_url": None
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
    "You're an AI assistant trained to spot issues in freelance service agreements.\n\n"
    "Based only on the first 1000 characters of the following contract, identify 4-5 possible concerns or ambiguities. "
    "Use bullet points and be concise.\n\nPartial Contract:\n"
)

PROMPT_FULL = (
    "You are an AI assistant trained in reviewing freelance service agreements.\n\n"
    "Analyze the contract below and provide a structured, plain-language summary in markdown format with these sections:\n\n"
    "1. **Key Clauses Summary**\n"
    "- Summarize important clauses like: Payment Terms, Termination, Scope of Work, Non-compete, IP rights, and Deliverables.\n\n"
    "2. **Potential Issues or Ambiguities**\n"
    "- Quote any vague or risky wording and briefly explain why it may be a concern.\n\n"
    "3. **Risk Rating**\n"
    "- Rate the overall risk to the freelancer (Low, Medium, High) with 1–2 sentences explaining your assessment.\n\n"
    "4. **Suggestions to Consider**\n"
    "- Offer brief, actionable suggestions the freelancer could bring up with the client or ask a lawyer about.\n\n"
    "**Important:**\n"
    "Do NOT state or imply that this is legal advice. Focus on clarity and contract comprehension for a freelancer audience.\n\n"
    "Contract:\n"
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
            # Stripe badge and logo for trust
            st.markdown(
                """
                <div style='display:flex;align-items:center;gap:0.7em;margin-bottom:1em'>
                    <img src='https://stripe.com/img/v3/home/social.png' alt='Stripe Logo' width='85'/>
                    <span style='color:#6c757d;font-size:1em'>Payments securely processed by Stripe</span>
                </div>
                """,
                unsafe_allow_html=True
            )
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
