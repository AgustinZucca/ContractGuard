import os
import stripe
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE")

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        file_hash = session["metadata"].get("file_hash")
        email = session["metadata"].get("email")

        if file_hash:
            r = requests.post(
                f"{supabase_url}/rest/v1/paid_files",
                json={ "file_hash": file_hash, "email": email },
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation"
                }
            )
            if r.status_code in [200, 201]:
                return jsonify({"status": "stored"}), 200
            else:
                return jsonify({"error": r.text}), 500

    return jsonify({"status": "ignored"}), 200

if __name__ == "__main__":
    app.run(port=4242)
