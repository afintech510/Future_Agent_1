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

class ActionPlan(StrictBaseModel):
    suggested_actions: List[str]
    missing_info_questions: List[str]

class EmailAnalysisSchema(StrictBaseModel):
    summary: str
    intent: str = Field(..., description="quote_request | technical_support | order_status | intro | spam")
    priority: str = Field(..., description="P0 | P1 | P2")
    priority_reason: str = Field(..., description="Why this priority was chosen based on Adam's triage rules")
    quote_analysis: QuoteAnalysis
    part_numbers: PartNumbers
    technical_analysis: TechnicalAnalysis
    draft_reply: str
    action_plan: ActionPlan

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
        """Process a list of emails through the AI and update the DB. Returns (count, last_error)."""
        count = 0
        last_error = None
        for email in email_list:
            try:
                self._process_single_email(email)
                count += 1
            except Exception as e:
                last_error = str(e)
                print(f"Failed to process email {email['id']}: {e}")
                if "insufficient_quota" in last_error.lower():
                    break # Stop early if quota is gone
        return count, last_error

    def _process_single_email(self, email):
        print(f"🤖 Analyzing: {email['subject']}")
        
        # 1. FORMAT RECIPIENTS FOR AI CONTEXT
        to_list = ", ".join(email.get('recipient_emails', []) or [])
        cc_list = ", ".join(email.get('cc_emails', []) or [])

        user_prompt = USER_PROMPT_TEMPLATE.format(
            subject=email['subject'],
            sender_name=email.get('from_name', 'Unknown'),
            sender_email=email.get('sender_email', 'unknown@domain.com'),
            to_list=to_list,
            cc_list=cc_list,
            sent_at=email.get('sent_at', 'unknown'),
            body=email['body'][:12000]
        )

        # Using Structured Outputs API (parse)
        completion = self.openai.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format=EmailAnalysisSchema,
        )

        ai_data = completion.choices[0].message.parsed
        
        # 1. Prepare Insights Data
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
            "draft_reply": ai_data.draft_reply,
            "raw_ai_output": ai_data.model_dump(),
            "model_metadata": {"model": "gpt-4o-mini", "version": "v2.3-compat"}
        }
        
        # Upsert Insights
        self.supabase.table("email_insights").upsert(
            insights_data, on_conflict="email_id"
        ).execute()

        # 2. Prepare Recommended Parts
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
            self.supabase.table("parts_recommended").upsert(
                parts_batch, 
                on_conflict="email_id, part_number, source_type",
                ignore_duplicates=True
            ).execute()

        # 3. Mark as Processed
        self.supabase.table("emails").update({"processed_by_ai": True}).eq("id", email['id']).execute()
        
        print(f"✅ Successfully processed {email['id']}")

if __name__ == "__main__":
    engine = AIEngine()
    processed_count = engine.process_emails(engine.get_unprocessed_emails(limit=1))
    print(f"Cycle complete. Processed {processed_count} emails.")
