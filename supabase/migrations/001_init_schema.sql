CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    CREATE TYPE job_portal_enum AS ENUM (
        'linkedin_easy',
        'naukri',
        'instahyre',
        'greenhouse',
        'lever',
        'ashby',
        'workday',
        'smartrecruiters',
        'other_ats'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TYPE job_portal_enum ADD VALUE IF NOT EXISTS 'smartrecruiters';

DO $$
BEGIN
    CREATE TYPE job_status_enum AS ENUM (
        'DISCOVERED',
        'SCORED_REJECTED',
        'QUEUED_FOR_TAILORING',
        'READY_TO_APPLY',
        'IN_PROGRESS',
        'PAUSED_HITL',
        'APPLIED',
        'SKIPPED_BY_USER',
        'FAILED_CIRCUIT_BREAKER'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS profile_vault (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    structured_data JSONB NOT NULL,
    embedding vector(768),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_url TEXT UNIQUE NOT NULL,
    url_hash CHAR(64) UNIQUE NOT NULL,
    external_job_id VARCHAR(128),
    discovery_source VARCHAR(64) DEFAULT 'jobspy',
    portal job_portal_enum NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    role_title VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    jd_raw_text TEXT NOT NULL,
    jd_embedding vector(768),
    match_score NUMERIC(5,2),
    match_reasoning JSONB,
    is_dream_company BOOLEAN DEFAULT FALSE,
    status job_status_enum DEFAULT 'DISCOVERED',
    tailored_pdf_path TEXT,
    retry_count INT DEFAULT 0,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    applied_at TIMESTAMPTZ
);

ALTER TABLE jobs_queue ADD COLUMN IF NOT EXISTS external_job_id VARCHAR(128);
ALTER TABLE jobs_queue ADD COLUMN IF NOT EXISTS discovery_source VARCHAR(64) DEFAULT 'jobspy';
ALTER TABLE jobs_queue ALTER COLUMN match_score TYPE NUMERIC(5,2);

CREATE TABLE IF NOT EXISTS qa_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_normalized TEXT NOT NULL,
    field_type VARCHAR(50) NOT NULL,
    canonical_answer TEXT NOT NULL,
    portal_context VARCHAR(50),
    question_embedding vector(768) NOT NULL,
    source VARCHAR(30) DEFAULT 'human_verified',
    usage_count INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS application_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES jobs_queue(id) ON DELETE CASCADE,
    langgraph_thread_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    form_payload JSONB,
    low_confidence_fields JSONB,
    screenshot_path TEXT,
    telegram_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_quota_ledger (
    quota_date DATE NOT NULL DEFAULT CURRENT_DATE,
    portal job_portal_enum NOT NULL,
    applied_count INT DEFAULT 0,
    max_daily_limit INT NOT NULL,
    last_applied_at TIMESTAMPTZ,
    circuit_breaker_tripped BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (quota_date, portal)
);

CREATE OR REPLACE FUNCTION match_qa_cache(
  query_embedding vector(768),
  match_threshold FLOAT,
  match_count INT
)
RETURNS TABLE (
  id UUID,
  question_normalized TEXT,
  field_type VARCHAR,
  canonical_answer TEXT,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    qa_cache.id,
    qa_cache.question_normalized,
    qa_cache.field_type,
    qa_cache.canonical_answer,
    1 - (qa_cache.question_embedding <=> query_embedding) AS similarity
  FROM qa_cache
  WHERE 1 - (qa_cache.question_embedding <=> query_embedding) > match_threshold
  ORDER BY qa_cache.question_embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres, service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres, service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO postgres, service_role;
NOTIFY pgrst, 'reload schema';
