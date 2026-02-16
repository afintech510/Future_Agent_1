import os
from dotenv import load_dotenv
from ai_engine import AIEngine
import uuid

load_dotenv()

def verify_pn_filtering():
    engine = AIEngine()
    
    # Mock technical email with "noisy" content
    test_email = {
        "id": str(uuid.uuid4()),
        "subject": "Fw: New Vision Display: Verification of Drawing",
        "body": """
        Hi Adam,
        We are checking weights for the initial sample units (5).
        Part numbers discussed:
        00-0158
        AM-1024600LTMQW-T01H
        Also need to check Resolution 1024x600 and Size 7-inch.
        Thanks,
        Lori
        """,
        "from_name": "Lori Vernon",
        "sender_email": "lori@customer.com",
        "sent_at": "2026-02-16T10:00:00Z"
    }

    print("🚀 Testing PN Filtering & UI Restoration...")
    
    try:
        # 1. Test Processing
        processed, error = engine.process_emails([test_email])
        
        if error:
            print(f"❌ Extraction failed: {error}")
            return

        # 2. Verify in DB
        res = engine.supabase.table("parts_recommended").select("*").eq("email_id", test_email['id']).execute()
        parts = [p['part_number'] for p in res.data]
        
        print(f"\nExtracted Parts: {parts}")
        
        # We expect real PNs like 'AM-1024600LTMQW-T01H' and maybe '00-0158'
        # We MUST NOT see '0', '1', '7', '7-inch', etc.
        
        junk_found = [p for p in parts if len(p) < 4 or p.lower() in ['7-inch', 'hdmi']]
        
        if not junk_found:
            print("✅ Verification SUCCESS: No junk part numbers found.")
        else:
            print(f"❌ Verification FAILED: Found junk PNs: {junk_found}")

    except Exception as e:
        print(f"❌ Error during verification: {e}")

if __name__ == "__main__":
    verify_pn_filtering()
