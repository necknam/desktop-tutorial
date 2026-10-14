SET LOCAL search_path TO public, extensions;

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "email" character varying(255);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "department" character varying(100);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "job_title" character varying(100);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "grade" character varying(50);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "source_type" character varying(20) DEFAULT 'REAL_USER'::character varying;

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS "updated_at" timestamp with time zone DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON public.users(email);

CREATE TABLE IF NOT EXISTS public.roadmap_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'DRAFT',
    current_stage INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.roadmap_sessions ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.project_plans (
    plan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    draft_plan TEXT,
    refined_plan TEXT,
    project_goal TEXT,
    project_scope TEXT,
    tech_stack JSONB DEFAULT '[]'::jsonb,
    milestones JSONB DEFAULT '[]'::jsonb,
    key_issues JSONB DEFAULT '[]'::jsonb,
    deliverables JSONB DEFAULT '[]'::jsonb,
    validation_criteria JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_project_plan UNIQUE (session_id)
);

ALTER TABLE public.project_plans ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.plan_conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    sequence_no INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.plan_conversations ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.assessment_questions (
    question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    assessment_type VARCHAR(20) NOT NULL CHECK (assessment_type IN ('PRE', 'POST')),
    skill_id INTEGER REFERENCES skill_tags(skill_id),
    skill VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    options JSONB DEFAULT '[]'::jsonb,
    sequence_no INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_assessment_question_scope UNIQUE (question_id, session_id, assessment_type)
);

ALTER TABLE public.assessment_questions ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.assessment_responses (
    response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    question_id UUID NOT NULL,
    assessment_type VARCHAR(20) NOT NULL CHECK (assessment_type IN ('PRE', 'POST')),
    selected_option TEXT NOT NULL,
    score INT NOT NULL CHECK (score BETWEEN 1 AND 5),
    answered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_question_resp UNIQUE (session_id, question_id),
    CONSTRAINT fk_response_question_scope FOREIGN KEY (question_id, session_id, assessment_type) REFERENCES assessment_questions(question_id, session_id, assessment_type) ON DELETE CASCADE
);

ALTER TABLE public.assessment_responses ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.roadmap_generations (
    generation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    strategy_type VARCHAR(50) NOT NULL,
    strategy_title VARCHAR(255),
    summary TEXT,
    roadmap_content JSONB NOT NULL,
    generation_order INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_generation_scope UNIQUE (generation_id, session_id)
);

ALTER TABLE public.roadmap_generations ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.roadmap_selections (
    selection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    generation_id UUID NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_roadmap_selection UNIQUE (session_id),
    CONSTRAINT fk_roadmap_selections_generation_scope FOREIGN KEY (generation_id, session_id) REFERENCES roadmap_generations(generation_id, session_id) ON DELETE CASCADE
);

ALTER TABLE public.roadmap_selections ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.roadmap_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    generation_id UUID NOT NULL,
    report_content JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_roadmap_report UNIQUE (session_id),
    CONSTRAINT fk_roadmap_reports_generation_scope FOREIGN KEY (generation_id, session_id) REFERENCES roadmap_generations(generation_id, session_id) ON DELETE CASCADE
);

ALTER TABLE public.roadmap_reports ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.assessment_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    report_type VARCHAR(30) NOT NULL DEFAULT 'PRE_POST',
    report_content JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_assessment_report UNIQUE (session_id)
);

ALTER TABLE public.assessment_reports ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_bootstrap_roadmap_sessions ON public.roadmap_sessions (user_id, status);

CREATE INDEX IF NOT EXISTS idx_bootstrap_plan_conversations ON public.plan_conversations (session_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_bootstrap_assessment_questions ON public.assessment_questions (session_id, assessment_type, sequence_no);

CREATE INDEX IF NOT EXISTS idx_bootstrap_assessment_responses ON public.assessment_responses (session_id, assessment_type);

CREATE INDEX IF NOT EXISTS idx_bootstrap_roadmap_generations ON public.roadmap_generations (session_id, generation_order);
