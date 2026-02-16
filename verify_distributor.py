
import os
import json
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
import hashlib

from ai_engine import AIEngine

def extract_domain(email):
    if not email or "@" not in email:
        return None
    return email.split("@")[-1].lower()

def generate_thread_id(subject):
    clean = subject.lower().replace("re:", "").replace("fwd:", "").strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()

load_dotenv()

def verify_distributor_logic():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase = create_client(url, key)
    ai_engine = AIEngine()

    print("🚀 Starting Distributor Logic Verification...")

    # 1. Test Domain Parsing
    test_emails = [
        "john.doe@customer-auto.com",
        "adam.larkin@futureelectronics.com",
        "sales@supplier-displays.com"
    ]
    
    for e in test_emails:
        domain = extract_domain(e)
        is_internal = e.endswith("@futureelectronics.com")
        print(f"📧 Email: {e} -> Domain: {domain} | Internal: {is_internal}")

    # 2. Simulate Ingestion of a Customer Email
    print("\n--- Testing Customer Ingestion ---")
    customer_email = "buyer@nissan.com"
    customer_name = "Nissan Buyer"
    subject = "Quote Request for 7 inch display"
    body = "Hi Adam, we are looking for a quote for 10,000 units of a 7 inch HDMI display for our new dashboard project. Please advise on pricing and LT for AM-1280800N2TZQW-T48H."
    
    # We'll use a unique dedupe hash to avoid conflicts
    unique_id = str(uuid.uuid4())[:8]
    dedupe_hash = f"test_customer_{unique_id}"
    thread_id = generate_thread_id(subject)
    
    # Clean up any existing test data for this domain
    domain = "nissan.com"
    supabase.table("companies").delete().eq("domain", domain).execute()
    
    # Simulate the logic usually in PSTParser
    # 1. Company
    comp_resp = supabase.table("companies").insert({
        "name": "Nissan",
        "domain": domain,
        "type": "Unclassified"
    }).execute()
    comp_id = comp_resp.data[0]['id']
    
    # 2. Contact
    supabase.table("contacts").insert({
        "email": customer_email,
        "company_id": comp_id,
        "full_name": customer_name
    }).execute()
    
    # 3. Email
    email_resp = supabase.table("emails").insert({
        "from_name": customer_name,
        "sender_email": customer_email,
        "subject": subject,
        "body": body,
        "dedupe_hash": dedupe_hash,
        "sent_at": "2026-02-16T12:00:00Z",
        "related_company_id": comp_id,
        "processed_by_ai": False
    }).execute()
    email_id = email_resp.data[0]['id']
    
    print(f"✅ Ingested customer email. ID: {email_id}. Linked to Company: {comp_id}")

    # 3. Run AI Classification
    print("\n--- Testing AI Classification ---")
    processed, error = ai_engine.process_emails([email_resp.data[0]])
    if error:
        print(f"❌ AI Error: {error}")
    else:
        # Check classification
        updated_comp = supabase.table("companies").select("*").eq("id", comp_id).single().execute().data
        print(f"📊 Company Classification: {updated_comp['type']}")
        print(f"ℹ️ Reason: {updated_comp['classification_reason']}")
        
        # Check insight
        insight = supabase.table("email_insights").select("*").eq("email_id", email_id).single().execute().data
        print(f"💡 Intent: {insight['intent']} | Priority: {insight['priority']}")

    # 4. Simulate Supplier Ingestion
    print("\n--- Testing Supplier Ingestion ---")
    supplier_email = "sales@ampire.com.tw"
    supplier_name = "Ampire Sales"
    s_subject = "Spec update for AM-1280800"
    s_body = "Hi Adam, please find the latest spec sheet for the AM-1280800 series. Let us know if you have any technical questions."
    
    s_domain = "ampire.com.tw"
    supabase.table("companies").delete().eq("domain", s_domain).execute()
    
    s_comp_resp = supabase.table("companies").insert({
        "name": "Ampire",
        "domain": s_domain,
        "type": "Unclassified"
    }).execute()
    s_comp_id = s_comp_resp.data[0]['id']
    
    s_email_resp = supabase.table("emails").insert({
        "from_name": supplier_name,
        "sender_email": supplier_email,
        "subject": s_subject,
        "body": s_body,
        "dedupe_hash": f"test_supplier_{unique_id}",
        "sent_at": "2026-02-16T13:00:00Z",
        "related_company_id": s_comp_id,
        "processed_by_ai": False
    }).execute()
    
    ai_engine.process_emails([s_email_resp.data[0]])
    updated_s_comp = supabase.table("companies").select("*").eq("id", s_comp_id).single().execute().data
    print(f"📊 Supplier Classification: {updated_s_comp['type']}")

    print("\n🏁 Verification script complete.")

if __name__ == "__main__":
    verify_distributor_logic()
