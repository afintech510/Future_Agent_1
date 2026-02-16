import os
import streamlit as st # To check if streamlit is available
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def run_diagnostic():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    oa_key = os.getenv("OPENAI_API_KEY")
    
    print("--- ENVIRONMENT CHECK ---")
    print(f"SUPABASE_URL: {'Present' if url else 'MISSING'}")
    print(f"SUPABASE_SERVICE_ROLE_KEY: {'Present' if key else 'MISSING'}")
    print(f"OPENAI_API_KEY: {'Present' if oa_key else 'MISSING'}")
    
    if url and key:
        print("\n--- DATABASE CONNECTION ---")
        try:
            supabase = create_client(url, key)
            # Check emails table
            emails = supabase.table("emails").select("id", count="exact").limit(1).execute()
            print(f"Emails found: {emails.count}")
            
            # Check insights table
            insights = supabase.table("email_insights").select("id", count="exact").limit(1).execute()
            print(f"Processed insights: {insights.count}")
            
            unprocessed = emails.count - insights.count if emails.count is not None and insights.count is not None else 0
            print(f"Calculated Unprocessed: {unprocessed}")
            
        except Exception as e:
            print(f"ERROR: {e}")
    else:
        print("\nSkipping connection check due to missing credentials.")

if __name__ == "__main__":
    run_diagnostic()
