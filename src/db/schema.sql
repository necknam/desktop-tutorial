-- =====================================================================
-- GrowPath AI 통합 데이터베이스 스키마 (schema.sql)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. [기존 검증 통과용] 스킬 태그 마스터 테이블
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_id BIGINT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    category VARCHAR(50) DEFAULT 'IT/SW',
    embedding VECTOR(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. [기존 검증 통과용] 강좌 마스터 테이블
CREATE TABLE IF NOT EXISTS courses (
    course_id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    category_name VARCHAR(100),
    org_name VARCHAR(100),
    url TEXT NOT NULL,
    is_matchup BOOLEAN DEFAULT FALSE,
    cost NUMERIC(10, 2) DEFAULT 0.00,
    duration_weeks INT DEFAULT 4,
    recruitment_status VARCHAR(50) DEFAULT '상시학습',
    syllabus_summary TEXT,
    platform VARCHAR(50) DEFAULT 'K-MOOC',
    difficulty VARCHAR(30) DEFAULT 'INTERMEDIATE',
    record_source VARCHAR(50) DEFAULT 'REAL_PUBLIC_DATA',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. [기존 검증 통과용] 프롬프트 버전 관리 테이블
CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(50) NOT NULL,
    version_num INT NOT NULL,
    system_prompt TEXT NOT NULL,
    change_reason TEXT,
    parent_version_id UUID REFERENCES prompt_versions(prompt_version_id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    CONSTRAINT uq_agent_version UNIQUE (agent_id, version_num)
);

-- 4. [기존 검증 통과 및 사용자 마스터] users 테이블
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE,
    department VARCHAR(100),
    job_title VARCHAR(100),
    grade VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. [신규 7단계 1] 최상위 프로젝트 로드맵 세션 테이블
CREATE TABLE IF NOT EXISTS roadmap_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'DRAFT',
    current_stage INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 6. [신규 7단계 2] 1~2단계 프로젝트 계획 산출물
CREATE TABLE IF NOT EXISTS project_plans (
    plan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    draft_plan TEXT,
    refined_plan TEXT,
    project_goal TEXT,
    project_scope TEXT,
    tech_stack JSONB DEFAULT '[]'::jsonb,
    milestones JSONB DEFAULT '[]'::jsonb,
    key_issues JSONB DEFAULT '[]'::jsonb,
    deliverables JSONB DEFAULT '[]'::jsonb,
    validation_criteria JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_project_plan UNIQUE (session_id)
);

-- 7. [신규 7단계 3] 2단계 Socratic 대화 감사 로그
CREATE TABLE IF NOT EXISTS plan_conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    sequence_no INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 8. [신규 7단계 4] 3단계/6단계 동적 진단 문항
CREATE TABLE IF NOT EXISTS assessment_questions (
    question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    assessment_type VARCHAR(20) NOT NULL CHECK (assessment_type IN ('PRE', 'POST')),
    skill VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    options JSONB DEFAULT '[]'::jsonb,
    sequence_no INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 9. [신규 7단계 5] 3단계/6단계 진단 응답 및 숙련도 등급
CREATE TABLE IF NOT EXISTS assessment_responses (
    response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES assessment_questions(question_id) ON DELETE CASCADE,
    assessment_type VARCHAR(20) NOT NULL CHECK (assessment_type IN ('PRE', 'POST')),
    selected_option TEXT NOT NULL,
    score INT NOT NULL,
    answered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_question_resp UNIQUE (session_id, question_id)
);

-- 10. [신규 7단계 6] 4단계 3대 대안 로드맵
CREATE TABLE IF NOT EXISTS roadmap_generations (
    generation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    strategy_type VARCHAR(50) NOT NULL,
    strategy_title VARCHAR(255),
    summary TEXT,
    roadmap_content JSONB NOT NULL,
    generation_order INT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 11. [신규 7단계 7] 5단계 로드맵 채택 결과
CREATE TABLE IF NOT EXISTS roadmap_selections (
    selection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    generation_id UUID NOT NULL REFERENCES roadmap_generations(generation_id) ON DELETE CASCADE,
    selected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_roadmap_selection UNIQUE (session_id)
);

-- 12. [신규 7단계 8] 5단계 채택 로드맵 상세 설계 보고서
CREATE TABLE IF NOT EXISTS roadmap_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    generation_id UUID NOT NULL REFERENCES roadmap_generations(generation_id) ON DELETE CASCADE,
    report_content JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_roadmap_report UNIQUE (session_id)
);

-- 13. [신규 7단계 9] 7단계 종합 성장 평가 보고서
CREATE TABLE IF NOT EXISTS assessment_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES roadmap_sessions(session_id) ON DELETE CASCADE,
    report_type VARCHAR(30) NOT NULL DEFAULT 'PRE_POST',
    report_content JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_assessment_report UNIQUE (session_id)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_sessions_user ON roadmap_sessions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_conv_session_seq ON plan_conversations(session_id, sequence_no ASC);
CREATE INDEX IF NOT EXISTS idx_questions_session ON assessment_questions(session_id, assessment_type, sequence_no ASC);
CREATE INDEX IF NOT EXISTS idx_responses_session ON assessment_responses(session_id, assessment_type);
CREATE INDEX IF NOT EXISTS idx_generations_session ON roadmap_generations(session_id, generation_order ASC);

-- =====================================================================
-- [추후 정식 도입 시 추가] 14. 사내 규정집 및 15. AI 구축 사례 RAG 테이블
-- =====================================================================

-- 14. 사내 교육/기술 규정집 테이블
CREATE TABLE IF NOT EXISTS regulations (
    id SERIAL PRIMARY KEY,
    doc_title VARCHAR(150) NOT NULL,
    article_no VARCHAR(50) NOT NULL,
    category VARCHAR(50) DEFAULT '교육훈련',
    content TEXT NOT NULL,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 15. 사내 AI/데이터 구축 사례 테이블
CREATE TABLE IF NOT EXISTS ai_cases (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    department VARCHAR(100),
    solution_summary TEXT NOT NULL,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RAG 검색 가속을 위한 HNSW 벡터 인덱스
CREATE INDEX IF NOT EXISTS idx_regulations_embedding_hnsw
ON regulations USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_ai_cases_embedding_hnsw
ON ai_cases USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- =====================================================================
-- 16~18. K-water 평가기준 스키마 3종 (hr_regulation_pilot)
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS hr_regulation_pilot;

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_result_schema (
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "institution_id" VARCHAR(64) NOT NULL,
    "schema_name" VARCHAR(255) NOT NULL,
    "version_label" VARCHAR(255) NOT NULL,
    "evaluation_kind" VARCHAR(40) NOT NULL,
    "applicability_condition" JSONB NOT NULL,
    "applicability_note" TEXT,
    "effective_from" DATE,
    "effective_to" DATE,
    "rule_origin" VARCHAR(40) NOT NULL,
    "source_status" VARCHAR(40) NOT NULL,
    "review_status" VARCHAR(40) NOT NULL,
    "calculation_rule" JSONB,
    "calculation_status" VARCHAR(40) NOT NULL,
    "source_note" TEXT
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_item_definition (
    "evaluation_item_definition_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL,
    "parent_item_id" VARCHAR(64),
    "skill_id" INT,
    "item_code" VARCHAR(255) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "value_kind" VARCHAR(40) NOT NULL,
    "aggregation_role" VARCHAR(40) NOT NULL,
    "score_min" NUMERIC(12,3),
    "score_max" NUMERIC(12,3),
    "weight_percent" NUMERIC(6,3),
    "unit" VARCHAR(40),
    "scale_note" TEXT,
    "display_order" INT NOT NULL,
    "definition_note" TEXT,
    CONSTRAINT evaluation_item_definition_schema_code_uq UNIQUE (evaluation_result_schema_id, item_code),
    CONSTRAINT evaluation_item_definition_id_schema_uq UNIQUE (evaluation_item_definition_id, evaluation_result_schema_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_schema_source (
    "evaluation_schema_source_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL,
    "evaluation_item_definition_id" VARCHAR(64),
    "regulation_node_id" VARCHAR(64) NOT NULL,
    "source_role" VARCHAR(40) NOT NULL,
    "review_status" VARCHAR(40) NOT NULL,
    "source_note" TEXT
);