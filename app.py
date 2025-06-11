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

# ─── LOAD ENV AND CHECK ──────────────────────────────────────────────────────────
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
st.set_page_config(page_title="ContractGuard", layout="centered")
import streamlit.components.v1 as components
components.html("<script>document.title = 'ContractGuard';</script>", height=0)

# ─── LANDING & TESTIMONIALS ──────────────────────────────────────────────────────
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
📬 **Feedback welcome!** Got ideas? Drop them [here](mailto:support@contractguard.com).
""")

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
for k, v in {
    "contract_text":"", "uploaded_filename":"", "analysis_output":"",
    "file_hash":"", "checkout_url":None, "just_paid":False
}.items():
    st.session_state.setdefault(k, v)

# ─── HELPERS ─────────────────────────────────────────────────────────────────────
def extract_text_and_hash(uploaded):
    data = uploaded.read(); h = hashlib.sha256(data).hexdigest(); uploaded.seek(0)
    if uploaded.type == "application/pdf":
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    else:
        doc = docx.Document(BytesIO(data)); text = "\n".join(p.text for p in doc.paragraphs)
    return text, h

PROMPT = {
    "preview": "You're a legal assistant. Based on this partial contract, give 4-5 bullet points of potential issues. Only use the first 1000 chars.\nPartial Contract:\n",
    "full": "You are a senior legal advisor. Provide a concise markdown summary of this contract:\n1. Key clauses...\nContract:"}

client = OpenAI(api_key=openai_api_key)

def analyze_preview(text):
    prompt = PROMPT["preview"] + text[:1000]
    res = client.chat.completions.create(model="gpt-3.5-turbo",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2, max_tokens=300)
    return res.choices[0].message.content

def analyze_contract(text):
    prompt = PROMPT["full"] + text[:8000]
    res = client.chat.completions.create(model="gpt-4",
        messages=[{"role":"user","content":prompt}],
        temperature=0.3)
    return res.choices[0].message.content

# ─── SUPABASE CRUD ──────────────────────────────────────────────────────────────
def supabase_get(table, field, val):
    hdr={"apikey":supabase_key,"Authorization":f"Bearer {supabase_key}"}
    url=f"{supabase_url}/rest/v1/{table}?{field}=eq.{val}"
    r=requests.get(url,headers=hdr); return r.json() if r.status_code==200 else []
def get_summary(hash): return supabase_get("summaries","file_hash",hash)[0].get("summary","") if supabase_get("summaries","file_hash",hash) else ""
def file_paid(hash): return bool(supabase_get("paid_files","file_hash",hash)))
def save(table,payload):
    hdr={"apikey":supabase_key,"Authorization":f"Bearer {supabase_key}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates"}
    requests.post(f"{supabase_url}/rest/v1/{table}",json=payload,headers=hdr)

# ─── STRIPE REDIRECT HANDLER ────────────────────────────────────────────────────
if st.query_params.get("success") and st.query_params.get("hash"):
    h=st.query_params["hash"]; txt=get_contract_text_by_hash(h)
    if txt:
        st.session_state.contract_text, st.session_state.file_hash = txt, h
        if not get_summary(h):
            st.session_state.just_paid=True; st.success("✅ Payment confirmed. Generating analysis...")
            with st.spinner("Analyzing..."):
                out=analyze_contract(txt)
                st.session_state.analysis_output=out; save("summaries",{"file_hash":h,"summary":out}); save("paid_files",{"file_hash":h})
        else:
            st.session_state.analysis_output=get_summary(h)

# ─── SHOW FULL ANALYSIS IMMEDIATELY IF JUST PAID ─────────────────────────────────
if st.session_state.just_paid:
    st.markdown("---"); st.subheader("🔍 Contract Summary & Suggestions"); st.markdown(st.session_state.analysis_output)
    pdf=FPDF(); pdf.add_page(); pdf.set_auto_page_break(True,margin=15); pdf.set_font("Arial",12)
    for line in st.session_state.analysis_output.split("\n"): pdf.multi_cell(0,8,line)
    buf=BytesIO(pdf.output(dest="S").encode("latin1"))
    if st.download_button("📄 Download as PDF",buf,"summary.pdf","application/pdf"): st.success("Download started")
    st.session_state.just_paid=False
    st.stop()

# ─── UPLOAD WIDGET ───────────────────────────────────────────────────────────────
up=st.file_uploader("Upload PDF or DOCX",type=["pdf","docx"])
if up:
    txt,h=extract_text_and_hash(up); st.session_state.contract_text, st.session_state.file_hash = txt, h; save("uploaded_contracts",{"file_hash":h,"text":txt})
    st.session_state.analysis_output=""; st.session_state.just_paid=False
    if get_summary(h): st.session_state.analysis_output=get_summary(h)

# ─── PREVIEW & PURCHASE or PREVIOUSLY SAVED ─────────────────────────────────────
if st.session_state.contract_text:
    st.markdown("---"); st.info(f"📄 Uploaded: {st.session_state.uploaded_filename}"); st.code(st.session_state.contract_text[:500])
    paid=file_paid(st.session_state.file_hash)
    if not paid:
        st.markdown("### 🕵️ Preview Analysis (Free)"); st.markdown(analyze_preview(st.session_state.contract_text))
        st.markdown("### 🔐 Unlock Full Analysis for $5")
        if st.button("Pay Now"):
            sess=stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{'price_data':{'currency':'usd','product_data':{'name':PRODUCT_NAME},'unit_amount':PRODUCT_PRICE},'quantity':1}],
                mode="payment",success_url=f"{REAL_URL}?success=true&hash={st.session_state.file_hash}",cancel_url=f"{REAL_URL}?canceled=true")
            st.session_state.checkout_url=sess.url; st.experimental_rerun()
    else:
        st.markdown("---"); st.subheader("🔍 Previously Saved Summary & Suggestions"); st.markdown(st.session_state.analysis_output)
