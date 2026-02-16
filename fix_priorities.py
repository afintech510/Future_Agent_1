import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

def normalize_priorities():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase = create_client(url, key)
    
    print("🔄 Fetching email insights to normalize priorities...")
    resp = supabase.table("email_insights").select("id, priority").execute()
    
    if not resp.data:
        print("No insights found.")
        return

    updates = []
    for row in resp.data:
        p = row['priority']
        new_p = p
        if "P0" in p: new_p = "P0"
        elif "P1" in p: new_p = "P1"
        elif "P2" in p: new_p = "P2"
        
        if new_p != p:
            updates.append({"id": row['id'], "priority": new_p})

    if updates:
        print(f"Updating {len(updates)} rows...")
        for up in updates:
            supabase.table("email_insights").update({"priority": up['priority']}).eq("id", up['id']).execute()
        print("✅ Normalization complete.")
    else:
        print("Already normalized.")

if __name__ == "__main__":
    normalize_priorities()
