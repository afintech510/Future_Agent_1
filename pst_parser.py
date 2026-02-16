import pypff
import hashlib
import os
import re
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") # Use Service Role for backend processing

if not SUPABASE_URL or not SUPABASE_KEY:
    # In production/deployment, we'd want this to be set. 
    # For now, we'll allow it to be None if just importing the class.
    pass

def clean_text(text):
    if not text:
        return ""
    return text.replace("\x00", "").strip()

def extract_header_field(headers, key):
    if not headers:
        return None
    pattern = rf"^{key}:\s*(.+)$"
    match = re.search(pattern, headers, re.MULTILINE | re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return None

def extract_emails(text):
    if not text:
        return []
    raw_emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    return list(set(e.lower() for e in raw_emails))

def generate_dedupe_hash(sender, recipients_str, subject, sent_time_iso, body_text):
    # Using a snippet of body to keep hash manageable but unique
    body_snippet = body_text[:200] if body_text else ""
    raw = f"{sender}|{recipients_str}|{subject}|{sent_time_iso}|{body_snippet}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def extract_domain(email):
    if not email or "@" not in email:
        return None
    return email.split("@")[-1].lower()

def generate_thread_id(subject):
    """
    Groups replies like "Re: Project" and "Fwd: Project" into one ID.
    """
    if not subject:
        return "unknown"
    # Remove Re:, Fwd:, [EXTERNAL], etc.
    clean = re.sub(r'^(re|fwd|fw|\[.*?\]):\s*', '', subject, flags=re.IGNORECASE).strip().lower()
    return hashlib.sha1(clean.encode('utf-8')).hexdigest()

class PSTParser:
    def __init__(self, file_path, import_id=None, batch_size=50):
        self.file_path = file_path
        self.import_id = import_id
        self.batch_size = batch_size
        self.pst = pypff.file()
        self.supabase = None
        if SUPABASE_URL and SUPABASE_KEY:
            self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        self.stats = {"processed": 0, "errors": 0}
        self.batch = []

    def open(self):
        self.pst.open(self.file_path)

    def close(self):
        self.pst.close()

    def parse(self):
        root = self.pst.get_root_folder()
        if root:
            self._parse_folder(root, "Root")
        self._flush_batch()
        return self.stats

    def _parse_folder(self, folder, path_str):
        for message in folder.sub_messages:
            try:
                self._process_message(message, path_str)
            except Exception as e:
                print(f"⚠️ Error in {path_str}: {e}")
                self.stats["errors"] += 1
        
        for sub_folder in folder.sub_folders:
            new_path = f"{path_str}/{sub_folder.name}"
            self._parse_folder(sub_folder, new_path)

    def _process_message(self, message, folder_path):
        # --- 1. BASIC EXTRACTION ---
        subject = clean_text(message.subject)
        headers = clean_text(message.transport_headers)
        body_text = clean_text(message.plain_text_body)
        if not body_text:
            try:
                body_text = message.html_body.decode('utf-8', errors='ignore')
            except:
                body_text = ""
        
        # --- 2. IDENTITY & HEADERS ---
        msg_id = extract_header_field(headers, "Message-ID")
        references = extract_header_field(headers, "References")
        
        header_from = extract_header_field(headers, "From")
        sender_emails = extract_emails(header_from)
        sender_email = sender_emails[0] if sender_emails else None
        
        sender_name = clean_text(message.sender_name)
        
        header_to = extract_header_field(headers, "To")
        header_cc = extract_header_field(headers, "Cc")
        to_emails = extract_emails(header_to)
        cc_emails = extract_emails(header_cc)
        
        # --- 3. TIMESTAMPS ---
        delivery_time = message.get_delivery_time()
        timestamp_missing = False
        if delivery_time:
            # Handle both naive and aware datetimes if necessary, pypff usually returns aware or local
            try:
                sent_at = delivery_time.astimezone(timezone.utc).isoformat()
            except:
                sent_at = delivery_time.isoformat()
        else:
            sent_at = None
            timestamp_missing = True

        # --- 4. DEDUPE & THREADING ---
        content_hash = generate_dedupe_hash(
            sender_email or "unknown", 
            ",".join(sorted(to_emails)), 
            subject, 
            sent_at or "missing", 
            body_text
        )
        
        thread_id = generate_thread_id(subject)
        
        # --- 4.5 DISTRIBUTOR LOGIC: Entity Linking ---
        related_company_id = None
        if self.supabase:
            # Gather all participants
            all_participants = list(set([sender_email] + to_emails + cc_emails))
            external_emails = [e for e in all_participants if e and not e.endswith("@futureelectronics.com")]
            
            if external_emails:
                # We'll use the primary external contact's domain for the company
                primary_external = external_emails[0]
                domain = extract_domain(primary_external)
                
                if domain:
                    # 1. Ensure Company Exists
                    try:
                        # Try to find existing company by domain
                        comp_resp = self.supabase.table("companies").select("id").eq("domain", domain).execute()
                        if comp_resp.data:
                            related_company_id = comp_resp.data[0]['id']
                        else:
                            # Create new "Unclassified" company
                            comp_name = domain.split(".")[0].capitalize()
                            new_comp = self.supabase.table("companies").insert({
                                "name": comp_name,
                                "domain": domain,
                                "type": "Unclassified"
                            }).execute()
                            if new_comp.data:
                                related_company_id = new_comp.data[0]['id']
                        
                        # 2. Ensure Contact Exists
                        if related_company_id:
                            # Logic for sender if external
                            if sender_email and sender_email in external_emails:
                                self.supabase.table("contacts").upsert({
                                    "email": sender_email,
                                    "company_id": related_company_id,
                                    "full_name": sender_name
                                }, on_conflict="email").execute()
                            
                            # 3. Ensure Thread Exists/Updated
                            self.supabase.table("email_threads").upsert({
                                "id": thread_id,
                                "subject": subject,
                                "related_company_id": related_company_id,
                                "last_message_at": sent_at
                            }, on_conflict="id").execute()
                    except Exception as entity_e:
                        print(f"⚠️ Entity linking error: {entity_e}")

        # --- 5. ATTACHMENTS ---
        attachments_meta = []
        for i in range(message.number_of_attachments):
            try:
                att = message.get_attachment(i)
                # Defensive check for name attribute/method
                name = ""
                if hasattr(att, 'get_name'):
                    name = att.get_name()
                elif hasattr(att, 'name'):
                    name = att.name
                
                attachments_meta.append({
                    "filename": clean_text(name) or f"attachment_{i}",
                    "size": att.get_size() if hasattr(att, 'get_size') else 0,
                    "index": i
                })
            except Exception as att_e:
                print(f"⚠️ Error extracting attachment {i}: {att_e}")

        # --- 6. RECORD CONSTRUCTION ---
        email_record = {
            "import_id": self.import_id,
            "message_id": msg_id, 
            "dedupe_hash": content_hash,
            "thread_id": thread_id,
            "references_header": references,
            
            "subject": subject,
            "body": body_text[:50000], # Renamed 'body_text' to 'body' to match schema.sql
            
            "from_name": sender_name,
            "sender_email": sender_email,
            
            "recipient_emails": to_emails,
            "cc_emails": cc_emails,
            
            "sent_at": sent_at,
            "timestamp_missing": timestamp_missing,
            
            "folder_path": folder_path,
            "attachments": attachments_meta,
            "transport_headers": headers,
            "processed_by_ai": False,
            "related_company_id": related_company_id
        }
        
        self.batch.append(email_record)
        self.stats["processed"] += 1

        if len(self.batch) >= self.batch_size:
            self._flush_batch()

    def _flush_batch(self):
        if not self.batch or not self.supabase:
            return
        try:
            # Upsert: Prioritizes existing Message-ID or dedupe_hash
            self.supabase.table("emails").upsert(
                self.batch, 
                on_conflict="dedupe_hash", 
                ignore_duplicates=True
            ).execute()
        except Exception as e:
            print(f"❌ Batch Insert Error: {e}")
        
        self.batch.clear()

if __name__ == "__main__":
    # Example usage
    # parser = PSTParser("sample.pst", "uuid")
    # parser.open()
    # stats = parser.parse()
    # parser.close()
    pass
