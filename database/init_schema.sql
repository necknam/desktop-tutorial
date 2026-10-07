-- ==============================================================================
-- 1단계: HR 커리어 성장 및 스킬-갭 맞춤형 추천 플랫폼 통합 DDL (Full Vector RAG)
-- ==============================================================================

-- 0. PostgreSQL pgvector 벡터 검색 확장 모듈 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 중앙 표준 실무 스킬 태그 허브 (SKILL_TAGS)
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    category VARCHAR(30) DEFAULT 'IT/Tech',
    embedding vector(1536) -- OpenAI text-embedding-3-small 1536차원 벡터
);

-- 2. 5대 도메인 마스터 테이블 (전 도메인 벡터 컬럼 탑재)
CREATE TABLE IF NOT EXISTS jobs (
    job_id INT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    ncs_units TEXT,
    embedding vector(1536), -- 직무 NCS 요구역량 임베딩 벡터
    last_batch_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS courses (
    course_id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    category_name VARCHAR(50),
    org_name VARCHAR(100),
    url VARCHAR(255),
    is_matchup BOOLEAN DEFAULT FALSE,
    cost NUMERIC(10, 2) DEFAULT 0.00,
    duration_weeks INT DEFAULT 4,
    recruitment_status VARCHAR(30) DEFAULT 'OPEN',
    syllabus_summary TEXT,
    embedding vector(1536), -- K-MOOC 강좌 시맨틱 검색용 임베딩 벡터
    last_batch_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS certifications (
    cert_id SERIAL PRIMARY KEY,
    title VARCHAR(100) UNIQUE NOT NULL,
    provider VARCHAR(50) NOT NULL DEFAULT '한국데이터산업진흥원 (K-DATA)',
    test_subjects TEXT,
    career_outlook TEXT,
    exam_type VARCHAR(20),
    pass_score INT DEFAULT 60,
    embedding vector(1536), -- 자격증 출제과목 시맨틱 검색용 임베딩 벡터
    last_batch_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS books (
    isbn VARCHAR(20) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    author VARCHAR(100),
    publisher VARCHAR(100),
    loan_count INT DEFAULT 0,
    kdc_code VARCHAR(10) DEFAULT '004',
    embedding vector(1536), -- 도서관 정보나루 기술 도서 시맨틱 검색용 임베딩 벡터
    last_batch_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS majors (
    major_id INT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    curriculum TEXT,
    embedding vector(1536), -- 대학 전공 커리큘럼 시맨틱 검색용 임베딩 벡터
    last_batch_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 3. 사용자 프로필 및 진단 브릿지
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    target_job_id INT REFERENCES jobs(job_id) ON DELETE SET NULL,
    is_degree_seeker BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_skill_gap (
    gap_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(user_id) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    gap_level VARCHAR(10) DEFAULT 'HIGH' CHECK (gap_level IN ('HIGH', 'LOW')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, skill_id)
);

-- 4. 5대 도메인 ↔ 스킬 N:M 매핑 브릿지 테이블
CREATE TABLE IF NOT EXISTS course_skill_map (
    course_id VARCHAR(50) REFERENCES courses(course_id) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, skill_id)
);

CREATE TABLE IF NOT EXISTS cert_skill_map (
    cert_id INT REFERENCES certifications(cert_id) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (cert_id, skill_id)
);

CREATE TABLE IF NOT EXISTS book_skill_map (
    isbn VARCHAR(20) REFERENCES books(isbn) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (isbn, skill_id)
);

CREATE TABLE IF NOT EXISTS major_skill_map (
    major_id INT REFERENCES majors(major_id) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (major_id, skill_id)
);

CREATE TABLE IF NOT EXISTS job_skill_map (
    job_id INT REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id INT REFERENCES skill_tags(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);

-- 5. RDB B-Tree 가속 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_user_skill_gap_user_id ON user_skill_gap(user_id);
CREATE INDEX IF NOT EXISTS idx_course_skill_map_skill_id ON course_skill_map(skill_id);
CREATE INDEX IF NOT EXISTS idx_cert_skill_map_skill_id ON cert_skill_map(skill_id);
CREATE INDEX IF NOT EXISTS idx_book_skill_map_skill_id ON book_skill_map(skill_id);
CREATE INDEX IF NOT EXISTS idx_major_skill_map_skill_id ON major_skill_map(skill_id);
CREATE INDEX IF NOT EXISTS idx_job_skill_map_skill_id ON job_skill_map(skill_id);
CREATE INDEX IF NOT EXISTS idx_books_loan_count ON books(loan_count DESC);

-- 6. pgvector 초고속 코사인 유사도 검색을 위한 HNSW 인덱스 생성 (6개 테이블 전수 구축)
CREATE INDEX IF NOT EXISTS idx_courses_embedding ON courses USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_books_embedding ON books USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_certs_embedding ON certifications USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_majors_embedding ON majors USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_embedding ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_skill_tags_embedding ON skill_tags USING hnsw (embedding vector_cosine_ops);