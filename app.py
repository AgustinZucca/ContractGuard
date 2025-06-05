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
st.set_page_config(page_title="ContractGuard - Contract Analyzer", layout="wide")

# --- API Keys (from ENV) ---
stripe.api_key = os.getenv("api_key")
openai_api_key = os.getenv("openai_api_key")
supabase_url = os.getenv("supabase_url")
supabase_key = os.getenv("supabase_key")

# --- Constants ---
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"
REAL_URL = "https://contractguard.streamlit.app"

# --- Session State Defaults ---
st.session_state.setdefault("contract_text", "")
st.session_state.setdefault("uploaded_filename", "")
st.session_state.setdefault("analysis_output", "")
st.session_state.setdefault("file_hash", "")
st.session_state.setdefault("checkout_url", None)
st.session_state.setdefault("language", "en")

# --- Multilingual Prompts ---
PROMPTS = {
    "en": """
You are a senior legal advisor specializing in contract review. Provide a professional, concise summary of the following contract in English:

1. Summary of key clauses: Payment Terms, Termination, Scope of Work, and any others found.
2. Identify unclear or risky language with specific quotes and short explanations.
3. Assign a Risk Level (Low / Medium / High) with reasoning.
4. Provide direct suggestions for improvements or negotiation points a freelancer or small business should consider.

Respond in markdown format with clear headers and bullet points.

Contract:
""",
    "es": """
Eres un asesor legal experimentado especializado en revisión de contratos. Proporciona un resumen profesional y conciso del siguiente contrato en Español:

1. Resumen de cláusulas clave: Términos de pago, Terminación, Alcance del trabajo y otras que se identifiquen.
2. Identifica lenguaje ambiguo o de riesgo con citas específicas y breves explicaciones.
3. Asigna un nivel de riesgo (Bajo / Medio / Alto) con razonamiento.
4. Proporciona sugerencias directas para mejoras o puntos de negociación que un freelancer o pequeña empresa debería considerar.

Responde en formato markdown con encabezados claros y viñetas.

Contrato:
""",
    "pt": """
Você é um consultor jurídico experiente especializado em revisão de contratos. Forneça um resumo profissional e conciso do seguinte contrato em Português:

1. Resumo das cláusulas principais: Termos de Pagamento, Rescisão, Escopo do Trabalho e outras encontradas.
2. Identifique linguagem ambígua ou de risco com citações específicas e breves explicações.
3. Atribua um Nível de Risco (Baixo / Médio / Alto) com justificativa.
4. Forneça sugestões diretas para melhorias ou pontos de negociação que um freelancer ou pequena empresa deve considerar.

Responda em formato markdown com títulos claros e marcadores.

Contrato:
"""
}

# --- Helper Functions ---
def extract_text_and_hash(file):
    data = file.read()
    h = hashlib.sha256(data).hexdigest()
    file.seek(0)
    if file.type == "application/pdf":
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    elif file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        doc = docx.Document(BytesIO(data))
        text = "\n".join(para.text for para in doc.paragraphs)
    else:
        text = "Unsupported file type."
    return text, h

from openai import OpenAI
client = OpenAI(api_key=openai_api_key)

def analyze_contract(text):
    # Split into chunks if too long
    def split_chunks(t, max_chars=12000):
        paras = t.split("\n\n")
        chunks = []
        current = ""
        for p in paras:
            if len(current) + len(p) + 2 <= max_chars:
                current += p + "\n\n"
            else:
                chunks.append(current)
                current = p + "\n\n"
        if current:
            chunks.append(current)
        return chunks

    prompt = PROMPTS.get(st.session_state.language, PROMPTS["en"])
    chunks = split_chunks(text)
    if len(chunks) == 1:
        full = prompt + text[:8000]
        resp = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": full}],
            temperature=0.3
        )
        return resp.choices[0].message.content
    else:
        # Summarize each chunk and combine
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

def supabase_get(table, query=""):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    r = requests.get(f"{supabase_url}/rest/v1/{table}{query}", headers=headers, timeout=10)
    return r.json() if r.status_code in (200, 201) else []

def supabase_insert(table, data, upsert=False):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates" if upsert else "return=representation"
    }
    requests.post(f"{supabase_url}/rest/v1/{table}", json=data, headers=headers, timeout=10)

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

# --- “Last Viewed” Persistence via URL Param ---
last_hash = st.experimental_get_query_params().get("last_hash", [])
if last_hash and not st.session_state.contract_text:
    lh = last_hash[0]
    text = get_contract_text_by_hash(lh)
    if text:
        st.session_state.contract_text = text
        st.session_state.file_hash = lh
        summary = get_summary_by_hash(lh)
        if summary:
            st.session_state.analysis_output = summary

# --- Header & Language Selection ---
st.markdown(
    """
    <div style="display:flex; align-items:center; padding:10px 0;">
        <img src="https://via.placeholder.com/40" style="margin-right:10px;" />
        <h1 style="margin:0;">ContractGuard</h1>
    </div>
    """, unsafe_allow_html=True
)
st.session_state.language = st.selectbox(
    "Language",
    options=[("English","en"), ("Español","es"), ("Português","pt")],
    format_func=lambda x: x[0],
    index=["en","es","pt"].index(st.session_state.language)
)[1]

st.markdown("---")

# --- Handle Stripe Redirect ---
if st.experimental_get_query_params().get("success") and st.experimental_get_query_params().get("hash"):
    fh = st.experimental_get_query_params().get("hash")[0]
    text = get_contract_text_by_hash(fh)
    if text:
        st.session_state.contract_text = text
        st.session_state.file_hash = fh
        st.session_state.uploaded_filename = "Recovered after payment"
        existing = get_summary_by_hash(fh)
        if existing:
            st.session_state.analysis_output = existing
        else:
            st.success("✅ Payment confirmed! Analyzing your contract…")
            with st.spinner("Analyzing…"):
                out = analyze_contract(text)
                st.session_state.analysis_output = out
                save_summary(fh, out)
        # Persist last viewed
        st.experimental_set_query_params(last_hash=fh)

# --- Upload Section ---
st.markdown("## Upload Contract")
uploaded_file = st.file_uploader("Choose a PDF or Word (.docx) file:", type=["pdf","docx"])
if uploaded_file:
    text, h = extract_text_and_hash(uploaded_file)
    st.session_state.contract_text = text
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.file_hash = h
    save_uploaded_contract(h, text)
    existing = get_summary_by_hash(h)
    if existing:
        st.session_state.analysis_output = existing
    else:
        st.session_state.analysis_output = ""
    # Persist last viewed
    st.experimental_set_query_params(last_hash=h)

# --- Show Preview & Flow ---
if st.session_state.contract_text:
    st.markdown("---")
    if st.session_state.uploaded_filename:
        st.info(f"✅ Uploaded: {st.session_state.uploaded_filename}")
    st.write("### Contract Preview")
    st.code(st.session_state.contract_text[:1000])

    already = file_already_paid(st.session_state.file_hash)

    # --- Show summary once if exists ---
    if st.session_state.analysis_output:
        st.markdown("---")
        st.subheader("🔍 Contract Summary & Suggestions")
        st.markdown(st.session_state.analysis_output)

        # Copy to Clipboard
        if st.button("📋 Copy to Clipboard"):
            st.write(
                f"<textarea id='clip' style='opacity:0;'>{st.session_state.analysis_output}</textarea>"
                "<script>document.getElementById('clip').select();document.execCommand('copy');</script>",
                unsafe_allow_html=True
            )
            st.success("Copied!")

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

    # --- Otherwise, show Stripe flow if not paid ---
    elif not already:
        st.markdown("### 🔐 Unlock Full Analysis for $5")
        if st.button("Generate Stripe Link"):
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

        if st.session_state.checkout_url:
            st.markdown("---")
            st.success("✅ Stripe checkout link generated")
            st.markdown(
                f"[👉 Click here to securely pay with Stripe]({st.session_state.checkout_url})",
                unsafe_allow_html=True
            )

elif st.experimental_get_query_params().get("canceled"):
    st.warning("⚠️ Payment was canceled. Try again.")
