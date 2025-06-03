import streamlit as st
import openai
import pdfplumber
import docx
import os
import stripe
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

# Load API keys
stripe.api_key = st.secrets["api_key"]
openai_api_key = st.secrets["openai_api_key"]

# Set product price and ID
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"
REAL_URL = "https://contractguard.streamlit.app"

# Session state to persist contract text across reruns
if "contract_text" not in st.session_state:
    st.session_state.contract_text = ""

# Helper to extract text from uploaded file
def extract_text(file):
    if file.type == "application/pdf":
        with pdfplumber.open(file) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    elif file.type in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        doc = docx.Document(file)
        return "\n".join([para.text for para in doc.paragraphs])
    else:
        return "Unsupported file type."

# GPT Prompt
PROMPT_TEMPLATE = """
You are a legal assistant. Summarize the following contract:

1. Key Clauses (e.g., Payment Terms, Termination, Scope of Work)
2. Potential Risks or Ambiguous Language (explain clearly)
3. Overall Risk Level (Low/Medium/High) and why

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
        temperature=0.4
    )
    return response.choices[0].message.content

# Streamlit UI
st.set_page_config(page_title="ClauseGuard - Contract Analyzer", layout="centered")

# --- Hero Section / Landing Page ---
st.markdown("""
# 📄 **ClauseGuard**
### _Don't sign blind._

Upload any contract and get a clear, AI-powered summary with key clauses and potential red flags — in seconds.

✅ Understand payment terms, scope, and liability  
🚩 Spot risky language or unclear terms  
🔐 One-time payment of **$5** — no subscription

---
""")

# Upload section
uploaded_file = st.file_uploader("Upload a contract (PDF or Word)", type=["pdf", "docx"])

# Show example or result if uploaded
if uploaded_file:
    st.session_state.contract_text = extract_text(uploaded_file)
    if st.session_state.contract_text.startswith("Unsupported"):
        st.error(st.session_state.contract_text)
    else:
        st.markdown("---")
        st.write("✅ File uploaded. Here's a preview of your analysis (first 1000 characters):")
        st.code(st.session_state.contract_text[:1000])

        # Ask user to pay before full analysis
        st.markdown("### 🔐 Unlock Full Analysis for $5")
        if st.button("Pay with Stripe"):
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
                success_url=f"{REAL_URL}?success=true",
                cancel_url=f"{REAL_URL}?canceled=true",
            )
            st.markdown(f"[Click here to complete payment]({session.url})")

# Unlock analysis if payment confirmed (for now, toggle manually)
if st.query_params.get("success"):
    if st.session_state.contract_text:
        st.success("Payment confirmed! Analyzing your contract...")
        with st.spinner("Analyzing..."):
            output = analyze_contract(st.session_state.contract_text)
        st.markdown("---")
        st.subheader("🔍 Contract Summary")
        st.markdown(output)
    else:
        st.warning("Please upload a contract before accessing the analysis.")

elif st.query_params.get("canceled"):
    st.warning("Payment was canceled. Try again when ready.")
