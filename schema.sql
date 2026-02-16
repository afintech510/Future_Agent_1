-- Enable pgvector if needed for future vector search
CREATE EXTENSION IF NOT EXISTS vector;

-- Imports table: Tracks every upload session
CREATE TABLE IF NOT EXISTS imports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    emails_processed INTEGER DEFAULT 0,
    emails_skipped INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB
);

-- Companies table
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    domain TEXT UNIQUE,
    industry TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Emails table: Enhanced for threading and metadata
CREATE TABLE IF NOT EXISTS emails (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id UUID REFERENCES imports(id),
    message_id TEXT, 
    dedupe_hash TEXT UNIQUE NOT NULL,
    thread_id TEXT, -- For grouping conversations
    references_header TEXT,
    sender_email TEXT,
    from_name TEXT,
    recipient_emails TEXT[],
    cc_emails TEXT[],
    subject TEXT,
    body TEXT,
    html_body TEXT,
    sent_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    timestamp_missing BOOLEAN DEFAULT FALSE,
    folder_path TEXT,
    attachments JSONB, -- Metadata about attachments
    transport_headers JSONB,
    processed_by_ai BOOLEAN DEFAULT FALSE,
    company_id UUID REFERENCES companies(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Email Insights: Expanded for detailed triage
CREATE TABLE IF NOT EXISTS email_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id UUID REFERENCES emails(id) UNIQUE,
    summary TEXT,
    intent TEXT,
    priority TEXT,
    quote_intent BOOLEAN DEFAULT FALSE,
    quote_fields JSONB, -- quantity, timeline, location
    technical_analysis TEXT,
    technical_specs TEXT[], -- Added
    technical_risks TEXT[],
    suggested_actions TEXT[],
    missing_info_questions TEXT[],
    draft_reply TEXT,
    raw_ai_output JSONB,
    model_metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Parts Recommended: Enhanced with evidence and context
CREATE TABLE IF NOT EXISTS parts_recommended (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id UUID REFERENCES emails(id),
    part_number TEXT NOT NULL,
    source_type TEXT NOT NULL, -- 'customer_provided' or 'recommended'
    description TEXT,
    quantity INTEGER,
    where_found TEXT, -- 'body', 'attachment', etc.
    evidence_snippet TEXT,
    recommended_at TIMESTAMPTZ,
    attribution_status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email_id, part_number, source_type)
);

-- Tasks table: for follow-ups and commitments
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id UUID REFERENCES emails(id),
    company_name TEXT,
    fsp_name TEXT,
    task_type TEXT, -- 'follow_up' or 'waiting_on_client'
    description TEXT,
    due_date DATE,
    status TEXT DEFAULT 'pending', -- 'pending', 'completed'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Opportunities table
CREATE TABLE IF NOT EXISTS opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    title TEXT NOT NULL,
    status TEXT DEFAULT 'lead', -- 'lead', 'qualified', 'closed_won', 'closed_lost'
    value DECIMAL(12, 2),
    estimated_close_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_emails_thread_id ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_dedupe_hash ON emails(dedupe_hash);
CREATE INDEX IF NOT EXISTS idx_emails_processed_by_ai ON emails(processed_by_ai);
CREATE INDEX IF NOT EXISTS idx_parts_composite ON parts_recommended(email_id, part_number, source_type);

-- Row Level Security (RLS)
-- Note: Assuming standard Supabase auth. Users can only see their project data.
-- Since this is a monolithic VPS app, we might use a service role, but RLS is good practice.
ALTER TABLE imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE emails ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_insights ENABLE ROW LEVEL SECURITY;
ALTER TABLE parts_recommended ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY;

-- Enable public read for now if no auth is configured, OR restrict to authenticated
-- Policy for authenticated users
CREATE POLICY "Enable all access for authenticated users" ON emails FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Enable all access for authenticated users" ON imports FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Enable all access for authenticated users" ON email_insights FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Enable all access for authenticated users" ON parts_recommended FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Enable all access for authenticated users" ON companies FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Enable all access for authenticated users" ON opportunities FOR ALL USING (auth.role() = 'authenticated');
