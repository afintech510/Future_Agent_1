import streamlit as st
import pandas as pd
import os
import tempfile
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv
from pst_parser import PSTParser, generate_dedupe_hash
from ai_engine import AIEngine

load_dotenv()

# --- CONFIG & STYLING ---
st.set_page_config(page_title="Display Intel Command Center", page_icon="💎", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    div[data-testid="stMetricValue"] { color: #3b82f6; }
    .stMetric { background-color: #1f2937; padding: 15px; border-radius: 8px; border: 1px solid #374151; }
    </style>
""", unsafe_allow_html=True)

# --- INITIALIZATION ---
@st.cache_resource
def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, key)

supabase = get_supabase_client()
ai_engine = AIEngine()

# --- UTILS ---
def load_stats():
    try:
        # Efficient counts using head=True
        emails = supabase.table("emails").select("id", count="exact", head=True).execute()
        insights = supabase.table("email_insights").select("id", count="exact", head=True).execute()
        urgent = supabase.table("email_insights").select("id", count="exact", head=True).eq("priority", "P0").execute()
        parts = supabase.table("parts_recommended").select("id", count="exact", head=True).execute()
        
        return {
            "total": emails.count,
            "processed": insights.count,
            "urgent": urgent.count,
            "parts": parts.count
        }
    except Exception as e:
        print(f"⚠️ Stats load error: {e}")
        return {"total": 0, "processed": 0, "urgent": 0, "parts": 0}

# --- NAVIGATION ---
stats = load_stats()
page = st.sidebar.radio("Navigate", [
    "📊 Dashboard", 
    "🎯 Action Center", 
    "📝 Tasks & Follow-ups", # Added
    "📥 Import PST",
    "⚙️ Settings"
])
st.sidebar.divider()

# --- SIDEBAR AI ENRICHMENT ---
unprocessed_count = stats["total"] - stats["processed"]
if unprocessed_count > 0:
    st.sidebar.markdown(f"**{unprocessed_count}** emails pending.")
    if st.sidebar.button("🚀 Trigger Mega-Enrich", help="Runs in background. You can switch pages!"):
        import threading
        from streamlit.runtime.scriptrunner import add_script_run_ctx
        
        def run_enrichment():
            emails = ai_engine.get_unprocessed_emails(limit=150)
            ai_engine.process_emails(emails)
            st.toast("Background Enrichment Complete!")

        thread = threading.Thread(target=run_enrichment)
        add_script_run_ctx(thread)
        thread.start()
        st.sidebar.info("🤖 Processing in background...")
else:
    st.sidebar.success("✅ Inbox Analyzed")

st.sidebar.divider()

# --- DASHBOARD ---
if page == "📊 Dashboard":
    st.title("💎 Display Intel Dashboard")
    
    # Top Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Emails", stats["total"])
    c2.metric("AI Analyzed", stats["processed"])
    c3.metric("🔥 Urgent (P0)", stats["urgent"])
    c4.metric("Parts Detected", stats["parts"])
    
    st.divider()
    
    l_col, r_col = st.columns([2, 1])
    
    with l_col:
        st.subheader("Recent Intelligence")
        try:
            resp = supabase.table("email_insights").select(
                "priority, intent, summary, email:emails(subject, from_name, sent_at)"
            ).order("created_at", desc=True).limit(10).execute()
            
            if resp.data:
                # Flatten for display
                flat_data = []
                for r in resp.data:
                    flat_data.append({
                        "Priority": r['priority'],
                        "Intent": r['intent'],
                        "Summary": r['summary'],
                        "From": r['email']['from_name'],
                        "Subject": r['email']['subject']
                    })
                st.dataframe(pd.DataFrame(flat_data), use_container_width=True, hide_index=True)
            else:
                st.info("No insights found.")
        except Exception as e:
            st.warning(f"Database error (Intelligence): {e}")

    with r_col:
        st.subheader("Latest Parts")
        try:
            parts_resp = supabase.table("parts_recommended").select(
                "part_number, source_type, where_found"
            ).order("created_at", desc=True).limit(10).execute()
            if parts_resp.data:
                st.dataframe(parts_resp.data, hide_index=True, use_container_width=True)
        except Exception as e:
            st.warning(f"Database error (Parts): {e}")

# --- ACTION CENTER ---
elif page == "🎯 Action Center":
    st.title("🎯 Action Center")
    priorities = st.multiselect("Filter Priority", ["P0", "P1", "P2"], default=["P0", "P1"])
    
    if priorities:
        try:
            # We use an explicit join alias 'email' to avoid confusion and inner join for quality
            resp = supabase.table("email_insights").select(
                "*, email:emails!inner(*)"
            ).in_("priority", priorities).order("created_at", desc=True).execute()
            
            if not resp.data:
                st.info("No insights found for selected priorities. Try running AI Enrichment on unprocessed emails.")
            else:
                for item in resp.data:
                    email_data = item['email']
                    with st.expander(f"[{item['priority']}] {email_data['subject']} - {email_data['from_name']}"):
                        c1, c2 = st.columns([2, 1])
                        with c1:
                            st.markdown(f"**Intent:** `{item['intent']}`")
                            st.write(f"**Analysis:** {item['summary']}")
                            st.write(f"**Draft:**\n{item['draft_reply']}")
                        with c2:
                            st.write("**Missing Info:**")
                            for q in item['missing_info_questions']:
                                st.write(f"- {q}")
                            if st.button("Resolve", key=f"res_{item['id']}"):
                                st.toast("Marked as actioned!")
        except Exception as e:
            st.error(f"Action Center Error: {e}")
    else:
        st.info("Select at least one priority to view actions.")

elif page == "📝 Tasks & Follow-ups":
    st.title("📝 Tasks & Follow-ups")
    st.markdown("Automated commitments and follow-ups harvested from your sent emails.")
    
    try:
        resp = supabase.table("tasks").select("*, email:emails(*)").order("due_date").execute()
        
        if not resp.data:
            st.success("You're all caught up! No pending tasks.")
        else:
            for t in resp.data:
                icon = "🕒" if t['status'] == 'pending' else "✅"
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"### {icon} {t['description']}")
                        st.write(f"**Company:** {t['company_name']} | **Type:** `{t['task_type']}`")
                        if t['email']:
                            st.caption(f"Linked to: {t['email']['subject']}")
                    with col2:
                        st.write(f"**Due:** {t['due_date']}")
                        if t['status'] == 'pending':
                            if st.button("Mark Done", key=f"task_{t['id']}"):
                                supabase.table("tasks").update({"status": "completed"}).eq("id", t['id']).execute()
                                st.rerun()
    except Exception as e:
        st.error(f"Task Load Error: {e}")

# --- IMPORT PST ---
elif page == "📥 Import PST":
    st.title("📥 PST Ingestor")
    up_file = st.file_uploader("Upload .pst", type="pst")
    if up_file and st.button("Process PST"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pst") as tmp:
            tmp.write(up_file.getvalue())
            tmp_path = tmp.name
        
        try:
            # Create Import Record
            imp = supabase.table("imports").insert({"filename": up_file.name, "status": "processing"}).execute()
            imp_id = imp.data[0]['id']
            
            # Parse
            parser = PSTParser(tmp_path, import_id=imp_id)
            parser.open()
            stats = parser.parse()
            parser.close()
            
            supabase.table("imports").update({"status": "completed", "emails_processed": stats['processed']}).eq("id", imp_id).execute()
            st.success(f"Import {imp_id} finished. Processed {stats['processed']} messages.")
        except Exception as e:
            st.error(f"Import failed: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

# --- QUICK ADD ---
elif page == "⚡ Quick Add":
    st.title("⚡ Quick Thread Ingest")
    with st.form("quick_add"):
        frm_name = st.text_input("From Name")
        frm_email = st.text_input("From Email")
        sub = st.text_input("Subject")
        body = st.text_area("Body", height=200)
        if st.form_submit_button("Ingest & Analyze"):
            # Mock hash logic consistency
            d_hash = generate_dedupe_hash(frm_email, "me", sub, datetime.now().isoformat(), body)
            record = {
                "from_name": frm_name,
                "sender_email": frm_email,
                "subject": sub,
                "body": body,
                "dedupe_hash": d_hash,
                "sent_at": datetime.now().isoformat(),
                "processed_by_ai": False
            }
            try:
                res = supabase.table("emails").insert(record).execute()
                if res.data:
                    st.success("Email Ingested. Running AI...")
                    ai_engine.process_emails([res.data[0]])
                    st.success("Enrichment complete!")
            except Exception as e:
                st.warning(f"Likely duplicate or error: {e}")
