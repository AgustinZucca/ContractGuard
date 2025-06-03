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
openai.api_key = os.getenv("OPENAI_API_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Set product price and ID
PRODUCT_PRICE = 500  # $5.00 in cents
PRODUCT_NAME = "Contract Analysis"

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

# GPT Analysis
def analyze_contract(text):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "user", "content": PROMPT_TEMPLATE + text[:8000]}  # Limit to avoid token overflow
        ],
        temperature=0.4
    )
    return response.choices[0].message.content

# Streamlit UI
st.set_page_config(page_title="ClauseGuard - Contract Analyzer")
st.title("📄 ClauseGuard")
st.subheader("Upload your contract. Get an instant, AI-powered summary.")

# Upload section
uploaded_file = st.file_uploader("Upload a contract (PDF or Word)", type=["pdf", "docx"])

# Show example or result if uploaded
if uploaded_file:
    contract_text = extract_text(uploaded_file)
    if contract_text.startswith("Unsupported"):
        st.error(contract_text)
    else:
        st.markdown("---")
        st.write("✅ File uploaded. Here's a preview of your analysis (first 1000 characters):")
        st.code(contract_text[:1000])

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
                success_url="https://clauseguard.streamlit.app?success=true",
                cancel_url="https://clauseguard.streamlit.app?canceled=true",
            )
            st.markdown(f"[Click here to complete payment]({session.url})")

# Unlock analysis if payment confirmed (for now, toggle manually)
if st.query_params.get("success"):
    st.success("Payment confirmed! Analyzing your contract...")
    with st.spinner("Analyzing..."):
        output = analyze_contract(contract_text)
    st.markdown("---")
    st.subheader("🔍 Contract Summary")
    st.markdown(output)

elif st.query_params.get("canceled"):
    st.warning("Payment was canceled. Try again when ready.")

