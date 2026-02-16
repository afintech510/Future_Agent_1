import streamlit as st
import pandas as pd
import os
import tempfile
import re
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
    "📝 Tasks & Follow-ups",
    "🏢 Companies", # Added
    "📥 Import PST",
    "⚙️ Settings"
])
st.sidebar.divider()
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_resource.clear()
    st.rerun()

# --- SIDEBAR AI ENRICHMENT ---
st.sidebar.markdown(f"### 🤖 AI Agent Status")
unprocessed_count = stats["total"] - stats["processed"]

import threading
# Robust scriptrunner import
try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
except ImportError:
    try:
        from streamlit.runtime.scriptrunner.script_run_context import add_script_run_ctx
    except ImportError:
        from streamlit.scriptrunner import add_script_run_ctx

# Check if background thread is active
is_running = any(t.name == "EnrichmentThread" for t in threading.enumerate())

if is_running:
    st.sidebar.info("🤖 Adam is currently analyzing...")
    if st.sidebar.button("🔄 Check Status"):
        st.rerun()
else:
    btn_label = "🚀 Trigger Mega-Enrich" if unprocessed_count > 0 else "✅ Data Up to Date"
    if st.sidebar.button(btn_label, disabled=(unprocessed_count <= 0), help="Runs in background. You can switch pages!"):
        def run_enrichment():
            try:
                emails = ai_engine.get_unprocessed_emails(limit=150)
                if emails:
                    ai_engine.process_emails(emails)
                    print(f"✅ Background Enrichment Success: {len(emails)} items")
            except Exception as e:
                print(f"❌ Background Enrichment Error: {e}")

        thread = threading.Thread(target=run_enrichment, name="EnrichmentThread")
        add_script_run_ctx(thread)
        thread.start()
        st.sidebar.info("🤖 Processing started...")
        st.rerun()

if unprocessed_count > 0:
    st.sidebar.caption(f"**{unprocessed_count}** emails pending analysis.")

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
            resp = supabase.table("email_insights").select(
                "*, email:emails!inner(*, parts:parts_recommended(part_number), company:companies(id, name, type))"
            ).in_("priority", priorities).order("created_at", desc=True).execute()

            if not resp.data:
                st.info("No insights found for selected priorities. Try running AI Enrichment on unprocessed emails.")
            else:

                for item in resp.data:
                    email_data = item['email']
                    p_color = {"P0": "🔴", "P1": "🟡", "P2": "⚪"}.get(item['priority'], "")
                    
                    comp_data = email_data.get('company')
                    comp_name = comp_data['name'] if comp_data else "Unknown"
                    comp_type = comp_data['type'] if comp_data else ""
                    type_icon = "🏢" if comp_type == "Customer" else "🏭" if comp_type == "Supplier" else "❓"
                    
                    with st.expander(f"{p_color} {email_data['subject']} - {email_data['from_name']} ({type_icon} {comp_name})"):
                        # Top Metrics & Part Badges
                        col_meta, col_copy = st.columns([4, 1])
                        with col_meta:
                            st.markdown(f"**Intent:** :green[`{item['intent']}`]")
                            
                            # Part Number Badges
                            p_list = email_data.get('parts', [])
                            p_nums = sorted(list(set([p['part_number'] for p in p_list if len(p['part_number']) >= 4]))) if p_list else []
                            if p_nums:
                                st.caption("Detected Parts:")
                                # Use a container to keep badges together
                                with st.container():
                                    # Create columns for badges but with a fixed width approach or simple layout
                                    cols_per_row = 5
                                    for i in range(0, len(p_nums), cols_per_row):
                                        row_parts = p_nums[i:i + cols_per_row]
                                        cols = st.columns(len(row_parts))
                                        for idx, p in enumerate(row_parts):
                                            if cols[idx].button(f"Part: {p}", key=f"cp_{item['id']}_{p}", help="Click to copy"):
                                                st.components.v1.html(f"<script>navigator.clipboard.writeText('{p}');</script>", height=0)
                                                st.toast(f"Copied: {p}")
                        
                        with col_copy:
                            if st.button("📋 Copy Subject", key=f"sub_{item['id']}"):
                                st.components.v1.html(f"<script>navigator.clipboard.writeText('{email_data['subject']}');</script>", height=0)
                                st.toast("Subject line copied!")

                        st.divider()

                        # Technical & Commercial Vitals
                        v1, v2, v3 = st.columns(3)
                        with v1:
                            st.metric("EAU", item.get('eau') or 'N/A')
                            st.metric("Brightness", item.get('brightness_nits') or 'N/A')
                        with v2:
                            st.metric("Target Price", item.get('target_price') or 'N/A')
                            st.metric("Interface", item.get('interface') or 'N/A')
                        with v3:
                            st.metric("Resolution", item.get('resolution') or 'N/A')
                            if item.get('customization_notes') and item['customization_notes'] != "Not specified":
                                st.caption(f"🛠️ {item['customization_notes']}")

                        st.divider()

                        # Analysis and Draft
                        c1, c2 = st.columns([2, 1])
                        with c1:
                            st.markdown(f"**Analysis:** {item['summary']}")
                            
                            # Draft Block with Copy
                            st.markdown("### 📝 Suggested Reply")
                            d_col1, d_col2 = st.columns([5, 1])
                            with d_col1:
                                d_val = item.get('draft_reply') or ""
                                current_draft = st.text_area("Current Draft", d_val, height=200, key=f"draft_view_{item['id']}")
                            with d_col2:
                                if st.button("📋 Copy", key=f"copy_d_{item['id']}", help="Copy response to clipboard"):
                                    st.components.v1.html(f"<script>navigator.clipboard.writeText(`{current_draft}`);</script>", height=0)
                                    st.toast("Draft copied to clipboard!")
                            
                            # AI Refinement
                            st.divider()
                            st.markdown("#### ✨ Refine Suggested Reply")
                            ref_col1, ref_col2 = st.columns([4, 1])
                            refine_input = ref_col1.text_input("Refinement Instruction", placeholder="e.g. Make it more formal, ask for lead time...", key=f"ref_in_{item['id']}", label_visibility="collapsed")
                            if ref_col2.button("✨ Refine", key=f"btn_ref_{item['id']}", use_container_width=True):
                                if refine_input:
                                    with st.spinner("Adam is rewriting..."):
                                        new_draft = ai_engine.refine_draft(email_data['body'], current_draft, refine_input)
                                        # Update DB with new draft
                                        supabase.table("email_insights").update({"draft_reply": new_draft}).eq("id", item['id']).execute()
                                        st.rerun()
                                else:
                                    st.warning("Enter an instruction first.")

                        with c2:
                            st.write("**Missing Info / Questions:**")
                            for q in item['missing_info_questions']:
                                st.write(f"- {q}")
                            
                            st.divider()
                            if st.button("✅ Mark as Actioned", key=f"res_{item['id']}"):
                                st.toast("Insight resolved!")
                                
                            # Distributors / Sourcing Request
                            if item['intent'] == 'quote_request' and item['quote_intent']:
                                st.divider()
                                st.write("**Distributor Actions:**")
                                if st.button("🏭 Create Sourcing Request", key=f"src_{item['id']}", help="Forward to suppliers"):
                                    st.session_state.source_email_id = email_data['id']
                                    st.session_state.source_subject = email_data['subject']
                                    st.toast("Ready to create sourcing request!")

                        if "source_email_id" in st.session_state and st.session_state.source_email_id == email_data['id']:
                            with st.status("Creating Sourcing Request...", expanded=True):
                                suppliers = supabase.table("companies").select("id, name").eq("type", "Supplier").execute()
                                if suppliers.data:
                                    s_names = [s['name'] for s in suppliers.data]
                                    selected_s = st.selectbox("Select Supplier", s_names, key=f"sel_s_{item['id']}")
                                    s_id = next(s['id'] for s in suppliers.data if s['name'] == selected_s)
                                    notes = st.text_area("Notes for Sourcing Request", f"Request regarding: {st.session_state.source_subject}", key=f"notes_{item['id']}")
                                    if st.button("🚀 Send Request", key=f"send_s_{item['id']}"):
                                        # Create Opportunity if not exists or link existing
                                        # For simplified demo, we link to a generic or new one
                                        supabase.table("sourcing_requests").insert({
                                            "supplier_company_id": s_id,
                                            "status": "requested",
                                            "notes": notes
                                        }).execute()
                                        st.success(f"Sourcing request sent to {selected_s}!")
                                        del st.session_state.source_email_id
                                else:
                                    st.warning("No suppliers found. Mark companies as 'Supplier' first.")

                        # Audit View: Original Message
                        with st.expander("📄 Original Message Context"):
                            st.markdown(f"""
                            <div style="background-color: #1f2937; padding: 10px; border-radius: 5px; height: 192px; overflow-y: scroll; border: 1px solid #374151; color: #d1d5db; font-family: monospace; font-size: 0.85rem;">
                                {email_data['body'].replace('\n', '<br>')}
                            </div>
                            """, unsafe_allow_html=True)
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

elif page == "🏢 Companies":
    st.title("🏢 Company Intelligence")
    
    view_mode = st.radio("View", ["Directory", "Profile"], horizontal=True, label_visibility="collapsed")
    
    if view_mode == "Directory":
        c1, c2, c3 = st.columns(3)
        with c1:
            search = st.text_input("Search Companies", placeholder="Name or Domain...")
        with c2:
            type_f = st.multiselect("Filter Type", ["Customer", "Supplier", "Unclassified"], default=["Customer", "Supplier"])
            
        try:
            query = supabase.table("companies").select("*")
            if type_f:
                query = query.in_("type", type_f)
            if search:
                query = query.ilike("name", f"%{search}%")
            
            resp = query.order("name").execute()
            
            if resp.data:
                df = pd.DataFrame(resp.data)
                # Display pretty table
                st.dataframe(df[["name", "domain", "type", "classification_reason"]], use_container_width=True, hide_index=True)
                
                # Selection for profile
                selected_company = st.selectbox("Select for Profile View", [""] + [r['name'] for r in resp.data])
                if selected_company:
                    st.session_state.selected_company_name = selected_company
                    # view_mode = "Profile" # This won't work directly here, but we can instruct user
                    st.info(f"Switch to 'Profile' tab to view {selected_company}")
            else:
                st.info("No companies found.")
        except Exception as e:
            st.error(f"Load Error: {e}")

    elif view_mode == "Profile":
        comp_name = st.session_state.get("selected_company_name")
        if not comp_name:
            st.warning("Select a company from the Directory first.")
        else:
            try:
                comp = supabase.table("companies").select("*").eq("name", comp_name).single().execute().data
                if comp:
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        st.header(comp['name'])
                        st.write(f"**Domain:** {comp['domain']}")
                        st.write(f"**Type:** `{comp['type']}`")
                        st.caption(f"Reason: {comp['classification_reason']}")
                        
                        # Contacts
                        st.subheader("👤 Contacts")
                        contacts = supabase.table("contacts").select("*").eq("company_id", comp['id']).execute()
                        for c in contacts.data:
                            st.write(f"- **{c['full_name']}** ({c['email']})")

                        # Technical Profile (Latest Specs)
                        st.subheader("🛠️ Technical Profile")
                        latest_insight = supabase.table("email_insights").select(
                            "brightness_nits, interface, resolution, customization_notes"
                        ).eq("email:emails!inner(company_id)", comp['id']).order("created_at", desc=True).limit(1).execute()
                        
                        if latest_insight.data:
                            ins = latest_insight.data[0]
                            st.write(f"**Brightness:** {ins.get('brightness_nits', 'N/A')}")
                            st.write(f"**Interface:** {ins.get('interface', 'N/A')}")
                            st.write(f"**Resolution:** {ins.get('resolution', 'N/A')}")
                            if ins.get('customization_notes') and ins['customization_notes'] != "Not specified":
                                st.caption(f"Notes: {ins['customization_notes']}")
                        else:
                            st.caption("No technical profile data yet.")
                    
                    with col2:
                        st.subheader("🧵 Email Threads")
                        threads = supabase.table("email_threads").select("*").eq("related_company_id", comp['id']).order("last_message_at", desc=True).execute()
                        if not threads.data:
                            st.info("No threads linked yet.")
                        else:
                            for thread in threads.data:
                                with st.expander(f"Subject: {thread['subject']}"):
                                    st.caption(f"Last updated: {thread['last_message_at']}")
                                    # Fetch emails in this thread
                                    thread_emails = supabase.table("emails").select("*").eq("related_company_id", comp['id']).ilike("subject", f"%{thread['subject']}%").execute()
                                    for em in thread_emails.data:
                                        st.markdown(f"**{em['from_name']}** ({em['sent_at']})")
                                        st.caption(em['body'][:500] + "...")
                                        st.divider()
            except Exception as e:
                st.error(f"Profile Error: {e}")

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
