import os
from supabase import create_client, Client
from dotenv import load_dotenv
from ai_engine import AIEngine
import uuid

load_dotenv()

def verify_technical_extraction():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("❌ Missing Supabase credentials")
        return

    engine = AIEngine()
    
    # Mock technical email
    test_email = {
        "id": str(uuid.uuid4()),
        "subject": "New Project: Industrial Controller Display",
        "body": """
        Hi Adam,
        We are starting a new project for an industrial controller. 
        We need a 7.0" high-brightness display. 
        Specs:
        - Brightness: >1000 nits
        - Interface: LVDS
        - Resolution: 1024x600
        - We need a custom cover lens with 3mm AG coating.
        
        Our Estimated Annual Usage (EAU) is 10k units.
        Our target price is $18.50 per unit.
        
        Can you suggest a part from Ampire or Winstar?
        """,
        "from_name": "John Customer",
        "sender_email": "john@customer.com",
        "sent_at": "2026-02-16T10:00:00Z"
    }

    print("🚀 Testing Technical Sales Engineer Extraction...")
    
    # We'll use a modified _process_batch to just get the parsed result without saving to DB for this test
    # Or we can just run the full flow if we are okay with test data in the DB
    
    try:
        # For verification, we just want to see if the AI identifies the fields
        # We'll use the AIEngine logic
        processed, error = engine.process_emails([test_email])
        
        if error:
            print(f"❌ Extraction failed: {error}")
            return

        # Verify in DB
        res = engine.supabase.table("email_insights").select("*").eq("email_id", test_email['id']).single().execute()
        insight = res.data
        
        print("\n--- EXTRACTION RESULTS ---")
        print(f"EAU: {insight.get('eau')}")
        print(f"Target Price: {insight.get('target_price')}")
        print(f"Brightness: {insight.get('brightness_nits')}")
        print(f"Interface: {insight.get('interface')}")
        print(f"Resolution: {insight.get('resolution')}")
        print(f"Customization: {insight.get('customization_notes')}")
        
        success = True
        if "10k" not in insight.get('eau', ''): success = False
        if "18.50" not in insight.get('target_price', ''): success = False
        if "1000" not in insight.get('brightness_nits', ''): success = False
        
        if success:
            print("\n✅ Verification COMPLETE: Technical and Commercial vitals extracted correctly.")
        else:
            print("\n⚠️ Verification INCOMPLETE: Some fields were not extracted as expected.")

    except Exception as e:
        print(f"❌ Error during verification: {e}")

if __name__ == "__main__":
    verify_technical_extraction()
