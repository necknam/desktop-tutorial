-- ====================================================================
-- Step 1: HR 로드맵 멀티에이전트 고도화 통합 스키마 DDL
-- 파일 경로: database/schema_v2_roadmap.sql
-- ====================================================================

-- 1. 확장 기능 활성화 (UUID 생성 및 pgvector)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 2. 기존 마스터 테이블 기본 골격 보장 (누락 방지)
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_id INT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    category VARCHAR(30) NULL,
    embedding TEXT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id INT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    ncs_units TEXT NULL,
    embedding TEXT NULL,
    last_batch_updated TIMESTAMPTZ NULL,
    record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION'
);

CREATE TABLE IF NOT EXISTS courses (
    course_id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    category_name VARCHAR(50) NULL,
    org_name VARCHAR(100) NULL,
    url VARCHAR(255) NULL,
    is_matchup BOOLEAN NOT NULL DEFAULT false,
    cost NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    duration_weeks INT NOT NULL DEFAULT 4,
    recruitment_status VARCHAR(30) NULL,
    syllabus_summary TEXT NULL,
    platform VARCHAR(50) NOT NULL,
    difficulty VARCHAR(20) NOT NULL CHECK (difficulty IN ('BEGINNER', 'INTERMEDIATE', 'ADVANCED')),
    embedding TEXT NULL,
    last_batch_updated TIMESTAMPTZ NULL,
    record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION'
);

CREATE TABLE IF NOT EXISTS certifications (
    cert_id INT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    test_subjects TEXT NULL,
    career_outlook TEXT NULL,
    exam_type VARCHAR(20) NULL,
    pass_score INT NULL,
    embedding TEXT NULL,
    last_batch_updated TIMESTAMPTZ NULL,
    record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION'
);

CREATE TABLE IF NOT EXISTS books (
    isbn VARCHAR(20) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    author VARCHAR(100) NULL,
    publisher VARCHAR(100) NULL,
    loan_count INT NOT NULL DEFAULT 0,
    kdc_code VARCHAR(10) NULL,
    embedding TEXT NULL,
    last_batch_updated TIMESTAMPTZ NULL,
    record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION'
);

CREATE TABLE IF NOT EXISTS job_skill_map (
    job_id INT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id INT NOT NULL REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);

CREATE TABLE IF NOT EXISTS course_skill_map (
    course_id VARCHAR(50) NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    skill_id INT NOT NULL REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, skill_id)
);

CREATE TABLE IF NOT EXISTS cert_skill_map (
    cert_id INT NOT NULL REFERENCES certifications(cert_id) ON DELETE CASCADE,
    skill_id INT NOT NULL REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (cert_id, skill_id)
);

CREATE TABLE IF NOT EXISTS book_skill_map (
    isbn VARCHAR(20) NOT NULL REFERENCES books(isbn) ON DELETE CASCADE,
    skill_id INT NOT NULL REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (isbn, skill_id)
);

-- 기존 테이블이 이미 존재하는 경우 record_source 컬럼 안전하게 추가
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='record_source') THEN
        ALTER TABLE jobs ADD COLUMN record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='courses' AND column_name='record_source') THEN
        ALTER TABLE courses ADD COLUMN record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='certifications' AND column_name='record_source') THEN
        ALTER TABLE certifications ADD COLUMN record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='books' AND column_name='record_source') THEN
        ALTER TABLE books ADD COLUMN record_source VARCHAR(40) NOT NULL DEFAULT 'EXTERNAL_INGESTION';
    END IF;
END $$;

-- 3. 신규 9개 테이블 생성

-- (1) 로드맵 생성 요청 마스터 테이블
CREATE TABLE IF NOT EXISTS roadmap_requests (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_type VARCHAR(20) NOT NULL CHECK (user_type IN ('REAL_USER', 'TEST_MANUAL', 'TEST_AUTO')),
    employee_id VARCHAR(64) NULL,
    raw_user_prompt TEXT NOT NULL,
    conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_requirements JSONB NOT NULL DEFAULT '{}'::jsonb,
    retrieved_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);
CREATE INDEX IF NOT EXISTS idx_roadmap_requests_user_type ON roadmap_requests(user_type);
CREATE INDEX IF NOT EXISTS idx_roadmap_requests_created_at ON roadmap_requests(created_at DESC);

-- (2) 프롬프트 버전 관리 테이블 (선행 참조용으로 먼저 선언)
CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(50) NOT NULL CHECK (agent_id IN ('agent_practical', 'agent_certified', 'agent_fasttrack')),
    version_num INTEGER NOT NULL,
    system_prompt TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    parent_version_id UUID NULL REFERENCES prompt_versions(prompt_version_id),
    change_reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    deactivated_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_agent_version UNIQUE (agent_id, version_num)
);
-- 에이전트당 오직 단 하나의 활성 프롬프트만 허용하는 부분 유니크 인덱스
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_agent_prompt ON prompt_versions(agent_id) WHERE (is_active = true);

-- (3) 에이전트별 생성 결과 테이블
CREATE TABLE IF NOT EXISTS agent_generations (
    generation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES roadmap_requests(request_id) ON DELETE CASCADE,
    agent_id VARCHAR(50) NOT NULL CHECK (agent_id IN ('agent_practical', 'agent_certified', 'agent_fasttrack')),
    prompt_version_id UUID NULL REFERENCES prompt_versions(prompt_version_id),
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    roadmap_content JSONB NOT NULL,
    summary TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS' CHECK (status IN ('SUCCESS', 'FAILED', 'TIMEOUT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);
CREATE INDEX IF NOT EXISTS idx_agent_generations_request_id ON agent_generations(request_id);
CREATE INDEX IF NOT EXISTS idx_agent_generations_agent_id ON agent_generations(agent_id);

-- (4) 사용자 선택 로그 테이블
CREATE TABLE IF NOT EXISTS agent_selections (
    selection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE REFERENCES roadmap_requests(request_id) ON DELETE CASCADE,
    chosen_generation_id UUID NOT NULL REFERENCES agent_generations(generation_id) ON DELETE CASCADE,
    chosen_agent_id VARCHAR(50) NOT NULL CHECK (chosen_agent_id IN ('agent_practical', 'agent_certified', 'agent_fasttrack')),
    selection_reason TEXT NULL,
    user_type VARCHAR(20) NOT NULL CHECK (user_type IN ('REAL_USER', 'TEST_MANUAL', 'TEST_AUTO')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);
CREATE INDEX IF NOT EXISTS idx_agent_selections_chosen_agent ON agent_selections(chosen_agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_selections_user_type ON agent_selections(user_type);

-- (5) 프롬프트 가드레일 평가 결과 테이블
CREATE TABLE IF NOT EXISTS prompt_evaluations (
    evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_version_id UUID NOT NULL REFERENCES prompt_versions(prompt_version_id) ON DELETE CASCADE,
    evaluator_model VARCHAR(100) NOT NULL,
    rubric_scores JSONB NOT NULL,
    total_score NUMERIC(4, 2) NOT NULL,
    passed BOOLEAN NOT NULL DEFAULT false,
    eval_report TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);
CREATE INDEX IF NOT EXISTS idx_prompt_evaluations_version ON prompt_evaluations(prompt_version_id);

-- (6) 프롬프트 자가진화 실행 기록 테이블
CREATE TABLE IF NOT EXISTS prompt_optimizations (
    optimization_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_agent_id VARCHAR(50) NOT NULL CHECK (target_agent_id IN ('agent_practical', 'agent_certified', 'agent_fasttrack')),
    trigger_threshold INTEGER NOT NULL DEFAULT 10,
    cumulative_real_selections INTEGER NOT NULL,
    agent_selection_rate NUMERIC(5, 4) NOT NULL,
    old_prompt_version_id UUID NOT NULL REFERENCES prompt_versions(prompt_version_id),
    candidate_prompt_text TEXT NOT NULL,
    evaluation_id UUID NULL REFERENCES prompt_evaluations(evaluation_id),
    status VARCHAR(30) NOT NULL CHECK (status IN ('TRIGGERED', 'EVAL_PASSED', 'EVAL_FAILED', 'ROLLED_BACK')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- (7) DPO 선호도 데이터 쌍 테이블 (Chosen 1 : Rejected N)
CREATE TABLE IF NOT EXISTS preference_pairs (
    pair_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES roadmap_requests(request_id) ON DELETE CASCADE,
    chosen_generation_id UUID NOT NULL REFERENCES agent_generations(generation_id) ON DELETE CASCADE,
    rejected_generation_id UUID NOT NULL REFERENCES agent_generations(generation_id) ON DELETE CASCADE,
    prompt_input JSONB NOT NULL,
    chosen_output JSONB NOT NULL,
    rejected_output JSONB NOT NULL,
    user_type VARCHAR(20) NOT NULL CHECK (user_type IN ('REAL_USER', 'TEST_MANUAL', 'TEST_AUTO')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);
CREATE INDEX IF NOT EXISTS idx_preference_pairs_request ON preference_pairs(request_id);
CREATE INDEX IF NOT EXISTS idx_preference_pairs_user_type ON preference_pairs(user_type);

-- (8) 파인튜닝 데이터셋 익스포트 이력 테이블
CREATE TABLE IF NOT EXISTS finetuning_datasets (
    dataset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_type VARCHAR(20) NOT NULL CHECK (dataset_type IN ('SFT_JSONL', 'DPO_JSONL', 'HF_DATASET')),
    record_count INTEGER NOT NULL,
    export_path TEXT NOT NULL,
    pii_masked BOOLEAN NOT NULL DEFAULT true,
    filter_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- (9) 테스트용 임의 인사 프로필 풀 테이블
CREATE TABLE IF NOT EXISTS test_mock_profiles (
    profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(20) NOT NULL CHECK (source_type IN ('TEST_MANUAL', 'TEST_AUTO')),
    profile_name VARCHAR(100) NOT NULL,
    department VARCHAR(100) NOT NULL,
    grade VARCHAR(50) NOT NULL,
    job_title VARCHAR(100) NOT NULL,
    career_years INTEGER NOT NULL,
    current_skills JSONB NOT NULL,
    target_skills JSONB NOT NULL,
    work_context_summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 4. 3개 전략 에이전트 초기 프롬프트(v1) 시딩
INSERT INTO prompt_versions (agent_id, version_num, system_prompt, is_active, change_reason)
VALUES
(
    'agent_practical',
    1,
    '당신은 실무 프로젝트 중심 업무 개발 로드맵 전문가(Practical Project Specialist)입니다. 직무 현장에서 직접 요구되는 프로젝트 수행 역량, 문제 해결, 핸즈온 실습을 최우선으로 배치하세요. 추천 데이터는 반드시 컨텍스트로 제공된 실제 DB 검색 결과(강좌, 도서, 자격증)만을 사용하고 외부 데이터를 날조하지 마세요.',
    true,
    '초기 시스템 v1 기본 프롬프트 활성화'
),
(
    'agent_certified',
    1,
    '당신은 이론 및 공인자격 검증 중심 업무 개발 로드맵 전문가(Certified Theory Specialist)입니다. 학술적 기초 개념 확립, 표준 교재 독서, 공인 자격증 취득을 통한 객관적 역량 검증을 최우선 목표로 로드맵을 구성하세요. 추천 데이터는 반드시 컨텍스트로 제공된 실제 DB 검색 결과(강좌, 도서, 자격증)만을 사용하고 외부 데이터를 날조하지 마세요.',
    true,
    '초기 시스템 v1 기본 프롬프트 활성화'
),
(
    'agent_fasttrack',
    1,
    '당신은 단기 패스트트랙 중심 업무 개발 로드맵 전문가(Fast-Track Specialist)입니다. 최소한의 학습 시간 투입으로 핵심 직무에 즉각 투입될 수 있도록 단기 집중 매치업 강좌 및 요약 커리큘럼을 우선 배치하세요. 추천 데이터는 반드시 컨텍스트로 제공된 실제 DB 검색 결과(강좌, 도서, 자격증)만을 사용하고 외부 데이터를 날조하지 마세요.',
    true,
    '초기 시스템 v1 기본 프롬프트 활성화'
)
ON CONFLICT (agent_id, version_num) DO UPDATE
SET system_prompt = EXCLUDED.system_prompt,
    is_active = EXCLUDED.is_active;