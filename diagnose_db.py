import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def diagnose():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase = create_client(url, key)

    emails = supabase.table("emails").select("id", count="exact", head=True).execute()
    processed = supabase.table("emails").select("id", count="exact", head=True).eq("processed_by_ai", True).execute()
    insights = supabase.table("email_insights").select("id", count="exact", head=True).execute()
    
    print("\n--- DATABASE DIAGNOSTIC REPORT ---")
    print(f"Total Emails in 'emails' table: {emails.count}")
    print(f"Emails with 'processed_by_ai=True': {processed.count}")
    print(f"Total rows in 'email_insights' table: {insights.count}")
    
    if emails.count > 0 and insights.count == 0 and processed.count > 0:
        print("\nALERT: Emails are being marked processed, but no insights are being created.")
    elif emails.count > 0 and insights.count == 0 and processed.count == 0:
        print("\nALERT: No emails have been processed yet.")
    elif insights.count > 0 and processed.count != insights.count:
        print(f"\nNOTE: Discrepancy between processed flag ({processed.count}) and insights count ({insights.count}).")

if __name__ == "__main__":
    diagnose()
