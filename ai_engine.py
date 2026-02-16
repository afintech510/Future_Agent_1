import os
import json
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, ConfigDict
from openai import OpenAI
from supabase import create_client, Client
from dotenv import load_dotenv
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

load_dotenv()

# --- STRUCTURED OUTPUT MODELS ---
# OpenAI requires all fields in 'required' array and no extra properties.

class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class QuoteFields(StrictBaseModel):
    quantity: str = Field(..., description="e.g. 10k/year. Use 'Not specified' if missing.")
    timeline: str = Field(..., description="e.g. MP Q3 2025. Use 'Not specified' if missing.")
    delivery_location: str = Field(..., description="e.g. HK. Use 'Not specified' if missing.")

class QuoteAnalysis(StrictBaseModel):
    is_quote_request: bool
    extracted_fields: QuoteFields

class PartInfo(StrictBaseModel):
    pn: str = Field(..., description="The part number found")
    context: str = Field(..., description="Context or reasoning for this part")
    snippet: str = Field(..., description="Exact quote from the email")

class PartNumbers(StrictBaseModel):
    customer_provided: List[PartInfo]
    recommended_by_you: List[PartInfo]

class TechnicalSpec(StrictBaseModel):
    label: str = Field(..., description="e.g. Brightness, Interface, etc.")
    value: str = Field(..., description="The value of the spec")

class TechnicalAnalysis(StrictBaseModel):
    application: str = Field(..., description="The customer's end application. Use 'Unknown' if not mentioned.")
    specs_detected: List[TechnicalSpec] = Field(..., description="List of technical specs found.")
    risks: List[str]

class Commitment(StrictBaseModel):
    task_type: str = Field(..., description="follow_up | waiting_on_client")
    description: str = Field(..., description="Summarized task for Adam")
    due_date_offset_days: int = Field(..., description="How many days from now should this be due?")

class CommitmentAnalysis(StrictBaseModel):
    detected: bool
    commitments: List[Commitment]

class EmailAnalysisSchema(StrictBaseModel):
    summary: str
    intent: str = Field(..., description="quote_request | technical_support | order_status | intro | spam | update")
    priority: str = Field(..., description="P0 | P1 | P2")
    priority_reason: str = Field(..., description="Why this priority was chosen based on Adam's triage rules")
    quote_analysis: QuoteAnalysis
    part_numbers: PartNumbers
    technical_analysis: TechnicalAnalysis
    draft_reply: str
    action_plan: ActionPlan
    commitment_analysis: CommitmentAnalysis # Added for Harvest mode

class BatchEmailAnalysis(StrictBaseModel):
    results: List[EmailAnalysisSchema] = Field(..., description="List of 30 analysis objects")

# --- AI ENGINE ---

class AIEngine:
    def __init__(self):
        url: str = os.getenv("SUPABASE_URL")
        key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise ValueError("Missing Supabase credentials in .env")
        self.supabase: Client = create_client(url, key)
        self.openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def get_unprocessed_emails(self, limit=10):
        """Fetch emails that haven't been analyzed by AI yet."""
        response = self.supabase.table("emails") \
            .select("id, message_id, subject, body, from_name, sender_email, sent_at, recipient_emails, cc_emails") \
            .eq("processed_by_ai", False) \
            .limit(limit) \
            .execute()
        return response.data

    def process_emails(self, email_list):
        """Process emails in parallel batches for maximum throughput."""
        if not email_list:
            return 0, None
            
        import concurrent.futures
        batch_size = 30
        max_workers = 5
        
        # 1. Chunking
        chunks = [email_list[i:i + batch_size] for i in range(0, len(email_list), batch_size)]
        
        total_processed = 0
        last_error = None
        
        print(f"🚀 Starting parallel processing: {len(email_list)} emails in {len(chunks)} batches.")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {executor.submit(self._process_batch, chunk): chunk for chunk in chunks}
            
            for future in concurrent.futures.as_completed(future_to_chunk):
                try:
                    processed_in_batch = future.result()
                    total_processed += processed_in_batch
                except Exception as e:
                    last_error = str(e)
                    print(f"❌ Batch failed: {e}")
                    
        return total_processed, last_error

    def process_emails(self, email_list, progress_callback=None):
        """Process emails in parallel batches for maximum throughput."""
        if not email_list:
            return 0, None
            
        import concurrent.futures
        batch_size = 30
        max_workers = 5
        
        chunks = [email_list[i:i + batch_size] for i in range(0, len(email_list), batch_size)]
        total_processed = 0
        last_error = None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {executor.submit(self._process_batch, chunk): chunk for chunk in chunks}
            
            for future in concurrent.futures.as_completed(future_to_chunk):
                try:
                    processed_in_batch = future.result()
                    total_processed += processed_in_batch
                    if progress_callback:
                        progress_callback(total_processed, len(email_list))
                except Exception as e:
                    last_error = str(e)
                    print(f"❌ Batch failed: {e}")
                    
        return total_processed, last_error

    def _process_batch(self, emails):
        """Process a single batch of 30 emails."""
        from prompts import BATCH_USER_PROMPT_TEMPLATE, BATCH_SYSTEM_PROMPT
        
        # 1. Format Batch Prompt
        emails_block = ""
        for i, email in enumerate(emails):
            emails_block += f"\n--- ITEM {i} ---\n"
            emails_block += f"FROM: {email.get('from_name')} <{email.get('sender_email')}>\n"
            emails_block += f"SUBJECT: {email['subject']}\n"
            emails_block += f"BODY: {email['body'][:2000]}\n"

        batch_prompt = BATCH_USER_PROMPT_TEMPLATE.format(
            count=len(emails),
            emails_block=emails_block
        )

        try:
            completion = self.openai.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": batch_prompt}
                ],
                response_format=BatchEmailAnalysis,
            )
            
            ai_batch = completion.choices[0].message.parsed.results
            
            # 2. Save Results (1:1 mapping)
            for i, ai_data in enumerate(ai_batch):
                if i < len(emails):
                    self._save_single_result(emails[i], ai_data)
            
            return len(emails)
        except Exception as e:
            print(f"AI Batch Error: {e}")
            raise e

    def _save_single_result(self, email, ai_data):
        """Individual save logic (extracted from old _process_single_email)."""
        is_outgoing = "adam.larkin" in email.get('sender_email', '').lower()
        
        # Prepare Insights
        specs_list = [f"- {s.label}: {s.value}" for s in ai_data.technical_analysis.specs_detected]
        tech_summary = f"Application: {ai_data.technical_analysis.application}\n" + "\n".join(specs_list)
        
        insights_data = {
            "email_id": email['id'],
            "summary": ai_data.summary,
            "intent": ai_data.intent,
            "priority": ai_data.priority,
            "quote_intent": ai_data.quote_analysis.is_quote_request,
            "quote_fields": ai_data.quote_analysis.extracted_fields.model_dump(),
            "technical_analysis": tech_summary,
            "technical_risks": ai_data.technical_analysis.risks,
            "suggested_actions": ai_data.action_plan.suggested_actions,
            "missing_info_questions": ai_data.action_plan.missing_info_questions,
            "draft_reply": ai_data.draft_reply if not is_outgoing else "",
            "raw_ai_output": ai_data.model_dump(),
            "model_metadata": {"model": "gpt-4o-mini", "version": "v2.5-mega-batch"}
        }
        
        self.supabase.table("email_insights").upsert(insights_data, on_conflict="email_id").execute()

        # Tasks / Parts logic (Incoming vs Outgoing)
        if is_outgoing and ai_data.commitment_analysis.detected:
            from datetime import datetime, timedelta
            tasks_batch = []
            domain = "Unknown"
            recipients = email.get('recipient_emails', []) or []
            if recipients and "@" in recipients[0]:
                domain = recipients[0].split("@")[-1].split(".")[0].capitalize()

            for comm in ai_data.commitment_analysis.commitments:
                due_date = (datetime.now() + timedelta(days=comm.due_date_offset_days)).strftime("%Y-%m-%d")
                tasks_batch.append({
                    "email_id": email['id'],
                    "company_name": domain,
                    "task_type": comm.task_type,
                    "description": comm.description,
                    "due_date": due_date,
                    "status": "pending"
                })
            
            if tasks_batch:
                self.supabase.table("tasks").insert(tasks_batch).execute()

        # Recommended Parts
        parts_batch = []
        def add_parts(part_list, source_type):
            for p in part_list:
                parts_batch.append({
                    "email_id": email['id'],
                    "part_number": p.pn,
                    "source_type": source_type,
                    "where_found": "body",
                    "evidence_snippet": p.snippet or p.context,
                    "recommended_at": email.get('sent_at'),
                    "attribution_status": "pending"
                })

        add_parts(ai_data.part_numbers.customer_provided, "customer_provided")
        add_parts(ai_data.part_numbers.recommended_by_you, "recommended")

        if parts_batch:
            self.supabase.table("parts_recommended").upsert(parts_batch, on_conflict="email_id, part_number, source_type", ignore_duplicates=True).execute()

        # Mark as Processed
        self.supabase.table("emails").update({"processed_by_ai": True}).eq("id", email['id']).execute()

if __name__ == "__main__":
    engine = AIEngine()
    processed_count, error = engine.process_emails(engine.get_unprocessed_emails(limit=30))
    print(f"Cycle complete. Processed {processed_count} emails. Error: {error}")
