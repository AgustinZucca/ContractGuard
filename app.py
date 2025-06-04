import streamlit as st
import openai
import pdfplumber
import docx
import os
import stripe
import hashlib
import requests
import json
from io import BytesIO
from dotenv import load_dotenv
from datetime import datetime
from fpdf import FPDF

# 1. Load environment variables
load_dotenv()

# 2. Streamlit page configuration
st.set_page_config(
    page_title="ContractGuard - Contract Analyzer",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 3. Load API keys and endpoints
stripe.api_key = st.secrets["api_key"]
openai_api_key = st.secrets["openai_api_key"]
supabase_url = st.secrets["supabase_url"]
supabase_key = st.secrets["supabase_key"]
postmark_server_token = st.secrets.get("postmark_server_token", None)

# 4. Constants
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"
REAL_URL = "https://contractguard.streamlit.app"

# 5. Supabase table names
TABLE_UPLOADED = "uploaded_contracts"
TABLE_PAID = "paid_files"
TABLE_SUMMARIES = "summaries"
TABLE_HISTORY = "uploaded_contracts"  # reuse for history

# 6. Session state defaults
st.session_state.setdefault("user_email", "")
st.session_state.setdefault("contract_text", "")
st.session_state.setdefault("uploaded_filename", "")
st.session_state.setdefault("analysis_output", "")
st.session_state.setdefault("file_hash", "")
st.session_state.setdefault("checkout_url", None)
st.session_state.setdefault("language", "en")
st.session_state.setdefault("last_hash", None)
st.session_state.setdefault("history", [])

# --- Helper Functions ---

def extract_text_and_hash(file):
    """
    Extracts text from a PDF or DOCX file and computes SHA256 hash of its bytes.
    """
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

# GPT prompt templates (multilingual support)
PROMPT_TEMPLATES = {
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

# Initialize OpenAI client
from openai import OpenAI
client = OpenAI(api_key=openai_api_key)

def analyze_contract(text, lang="en"):
    """
    Calls OpenAI GPT-4 to analyze the contract text using the appropriate prompt.
    """
    prompt = PROMPT_TEMPLATES.get(lang, PROMPT_TEMPLATES["en"])
    full_prompt = prompt + text[:8000]
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Error during GPT analysis: {e}")
        return ""

# Supabase helper functions

def supabase_get(table, query=""):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}"
    }
    url = f"{supabase_url}/rest/v1/{table}{query}"
    r = requests.get(url, headers=headers)
    if r.status_code in (200, 201):
        return r.json()
    else:
        return []

def supabase_insert(table, data, upsert=False):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates" if upsert else "return=representation"
    }
    url = f"{supabase_url}/rest/v1/{table}"
    r = requests.post(url, json=data, headers=headers)
    return r.status_code in (200, 201)

def file_already_paid(file_hash):
    """
    Checks if the file_hash exists in paid_files table.
    """
    results = supabase_get(TABLE_PAID, f"?file_hash=eq.{file_hash}")
    return len(results) > 0

def get_summary_by_hash(file_hash):
    """
    Retrieves a saved summary from Supabase summaries table.
    """
    results = supabase_get(TABLE_SUMMARIES, f"?file_hash=eq.{file_hash}")
    if results:
        return results[0].get("summary", "")
    return ""

def save_uploaded_contract(file_hash, contract_text):
    """
    Saves the uploaded contract text to Supabase for future retrieval.
    """
    data = {"file_hash": file_hash, "text": contract_text, "created_at": datetime.utcnow().isoformat()}
    supabase_insert(TABLE_UPLOADED, data, upsert=True)

def save_summary(file_hash, summary_text):
    """
    Saves or updates a generated summary in the Supabase summaries table.
    """
    data = {"file_hash": file_hash, "summary": summary_text, "created_at": datetime.utcnow().isoformat()}
    supabase_insert(TABLE_SUMMARIES, data, upsert=True)

def send_email_summary(to_email, subject, body_html):
    """
    Sends the summary via Postmark. Requires POSTMARK_SERVER_TOKEN in secrets.
    """
    if not postmark_server_token:
        return False
    url = "https://api.postmarkapp.com/email"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": postmark_server_token
    }
    data = {
        "From": "no-reply@contractguard.app",
        "To": to_email,
        "Subject": subject,
        "HtmlBody": body_html
    }
    r = requests.post(url, headers=headers, json=data)
    return r.status_code == 200

def get_user_history():
    """
    Fetches the list of contracts (file_hash and timestamps) that the user has uploaded and paid for.
    """
    # This example does not implement real user accounts; using last_hash in session for demo
    results = supabase_get(TABLE_HISTORY, "")
    # Sort by created_at descending
    results_sorted = sorted(results, key=lambda x: x.get("created_at", ""), reverse=True)
    return results_sorted

def split_into_chunks(text, max_chars=15000):
    """
    Splits a long text into chunks of roughly max_chars, preserving paragraph boundaries.
    """
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current += para + "\n\n"
        else:
            chunks.append(current)
            current = para + "\n\n"
    if current:
        chunks.append(current)
    return chunks

# --- UI Layout ---

# Sidebar: Language selection and History
with st.sidebar:
    st.header("Settings & History")
    st.selectbox("Choose Language", ["English", "Español", "Português"], index=["en", "es", "pt"].index(st.session_state.language), key="language", on_change=lambda: st.experimental_rerun())
    st.markdown("---")
    st.subheader("My Documents")
    history_data = get_user_history()
    if history_data:
        for record in history_data:
            h_hash = record.get("file_hash")
            h_date = record.get("created_at", "")[:10]
            if st.button(f"{h_date} – View {h_hash[:8]}", key=f"hist_{h_hash}"):
                # Load that summary
                text = get_contract_text_by_hash(h_hash)
                if text:
                    st.session_state.contract_text = text
                    st.session_state.file_hash = h_hash
                    summary = get_summary_by_hash(h_hash)
                    if summary:
                        st.session_state.analysis_output = summary
                        st.session_state.uploaded_filename = f"Recovered {h_hash[:8]}"
                        st.experimental_rerun()
    else:
        st.info("No documents yet. Upload a contract to get started.")

# Main header (logo placeholder)
st.markdown(
    """
    <div style="display:flex; align-items:center; padding:10px 0;">
        <img src="https://via.placeholder.com/40" style="margin-right:10px;" />
        <h1 style="margin:0;">ContractGuard</h1>
    </div>
    """, 
    unsafe_allow_html=True
)

# Handle payment redirect with file hash in query
if st.query_params.get("success") and st.query_params.get("hash"):
    file_hash = st.query_params.get("hash")[0]
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
                # Handle large contracts by chunking
                chunks = split_into_chunks(text)
                if len(chunks) == 1:
                    output = analyze_contract(text, lang=st.session_state.language)
                else:
                    # Summarize each chunk and combine
                    partial_summaries = []
                    for chunk in chunks:
                        part = analyze_contract(chunk, lang=st.session_state.language)
                        partial_summaries.append(part)
                    combined = "\n\n".join(partial_summaries)
                    # Final pass to unify
                    output = analyze_contract(combined, lang=st.session_state.language)
                st.session_state.analysis_output = output
                save_summary(file_hash, output)

# Upload section
st.markdown("## Upload Contract")
uploaded_file = st.file_uploader("Choose a PDF or Word (.docx) file:", type=["pdf", "docx"])
if uploaded_file:
    contract_text, file_hash = extract_text_and_hash(uploaded_file)
    st.session_state.contract_text = contract_text
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.file_hash = file_hash
    st.session_state.analysis_output = ""
    save_uploaded_contract(file_hash, contract_text)
    st.session_state.last_hash = file_hash

    # Check if summary exists and load it directly
    existing = get_summary_by_hash(file_hash)
    if existing:
        st.session_state.analysis_output = existing
        st.success("⚡ Retrieved saved summary for this document.")

# Show preview if contract is available
if st.session_state.contract_text:
    st.markdown("---")
    if st.session_state.uploaded_filename:
        st.info(f"✅ Uploaded: {st.session_state.uploaded_filename}")
    st.write("### Contract Preview")
    st.code(st.session_state.contract_text[:1000])

    already_paid = file_already_paid(st.session_state.file_hash)

    # If a summary already exists, show it immediately
    if st.session_state.analysis_output:
        st.markdown("---")
        st.subheader("🔍 Contract Summary & Suggestions")
        st.markdown(st.session_state.analysis_output)
    # Otherwise, proceed with pay + generate flow
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

    # Copy to clipboard button
    if st.button("Copy Summary to Clipboard"):
        st.write(
            f"<textarea id='clip' style='opacity:0;'>{st.session_state.analysis_output}</textarea>"
            "<script>document.getElementById('clip').select();document.execCommand('copy');</script>",
            unsafe_allow_html=True,
        )
        st.success("Copied to clipboard!")

    # Download summary as PDF
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

    # Option to refresh summary without repaying
    if st.button("🔄 Refresh Summary (no charge)"):
        with st.spinner("Re-analyzing..."):
            output = analyze_contract(st.session_state.contract_text, lang=st.session_state.language)
            st.session_state.analysis_output = output
            save_summary(st.session_state.file_hash, output)
            st.success("Updated!")

    # Optionally email summary
    if st.session_state.user_email:
        if st.button("📧 Email Summary"):
            html_body = f"<h1>ContractGuard Summary</h1><pre>{st.session_state.analysis_output}</pre>"
            if send_email_summary(st.session_state.user_email, "Your Contract Summary", html_body):
                st.success("Email sent!")
            else:
                st.error("Failed to send email. Check Postmark configuration.")

elif st.query_params.get("canceled"):
    st.warning("⚠️ Payment was canceled. Try again when ready.")

# --- Usage Metrics (admin-only section) ---
if st.sidebar.checkbox("Show Usage Dashboard"):
    st.sidebar.markdown("---")
    st.sidebar.subheader("Admin: Usage Metrics")
    # Total contracts analyzed
    total_analyses = supabase_get(TABLE_SUMMARIES)
    st.sidebar.metric("Total Summaries Generated", len(total_analyses))
    # Unique paid files
    unique_paid = supabase_get(TABLE_PAID)
    st.sidebar.metric("Total Paid Files", len(unique_paid))
    # Top 5 most common risk flag keywords (mock example)
    st.sidebar.markdown("**Top 5 Risky Keywords**")
    flag_counts = {"indemnify": 10, "perpetual": 7, "termination": 12, "liability": 9, "governing law": 5}
    for k, v in sorted(flag_counts.items(), key=lambda x: -x[1])[:5]:
        st.sidebar.write(f"- {k}: {v} occurrences")

# --- End of App ---
