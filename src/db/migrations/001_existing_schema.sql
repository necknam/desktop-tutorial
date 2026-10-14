CREATE SCHEMA IF NOT EXISTS extensions;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

SET LOCAL search_path TO public, extensions;

CREATE TABLE IF NOT EXISTS public."agent_generations" (
  "generation_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "request_id" uuid NOT NULL,
  "agent_id" character varying(50) NOT NULL,
  "prompt_version_id" uuid,
  "provider" character varying(50) NOT NULL,
  "model" character varying(100) NOT NULL,
  "roadmap_content" jsonb NOT NULL,
  "summary" text NOT NULL,
  "latency_ms" integer NOT NULL,
  "status" character varying(20) DEFAULT 'SUCCESS'::character varying NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "agent_generations_agent_id_check" CHECK (agent_id::text = ANY (ARRAY['agent_practical'::character varying, 'agent_certified'::character varying, 'agent_fasttrack'::character varying]::text[])),
  CONSTRAINT "agent_generations_pkey" PRIMARY KEY (generation_id),
  CONSTRAINT "agent_generations_status_check" CHECK (status::text = ANY (ARRAY['SUCCESS'::character varying, 'FAILED'::character varying, 'TIMEOUT'::character varying]::text[]))
);

CREATE TABLE IF NOT EXISTS public."agent_selections" (
  "selection_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "request_id" uuid NOT NULL,
  "chosen_generation_id" uuid NOT NULL,
  "chosen_agent_id" character varying(50) NOT NULL,
  "selection_reason" text,
  "user_type" character varying(20) NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "agent_selections_chosen_agent_id_check" CHECK (chosen_agent_id::text = ANY (ARRAY['agent_practical'::character varying, 'agent_certified'::character varying, 'agent_fasttrack'::character varying]::text[])),
  CONSTRAINT "agent_selections_pkey" PRIMARY KEY (selection_id),
  CONSTRAINT "agent_selections_request_id_key" UNIQUE (request_id),
  CONSTRAINT "agent_selections_user_type_check" CHECK (user_type::text = ANY (ARRAY['REAL_USER'::character varying, 'TEST_MANUAL'::character varying, 'TEST_AUTO'::character varying]::text[]))
);

CREATE SEQUENCE IF NOT EXISTS public."ai_cases_id_seq";

CREATE TABLE IF NOT EXISTS public."ai_cases" (
  "id" integer DEFAULT nextval('ai_cases_id_seq'::regclass) NOT NULL,
  "title" character varying(200) NOT NULL,
  "department" character varying(100),
  "solution_summary" text NOT NULL,
  "embedding" vector(1536),
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "ai_cases_pkey" PRIMARY KEY (id)
);

ALTER SEQUENCE public."ai_cases_id_seq" OWNED BY public."ai_cases"."id";

CREATE TABLE IF NOT EXISTS public."book_skill_map" (
  "isbn" character varying(20) NOT NULL,
  "skill_id" integer NOT NULL,
  CONSTRAINT "book_skill_map_pkey" PRIMARY KEY (isbn, skill_id)
);

CREATE TABLE IF NOT EXISTS public."books" (
  "isbn" character varying(20) NOT NULL,
  "title" character varying(200) NOT NULL,
  "author" character varying(100),
  "publisher" character varying(100),
  "loan_count" integer DEFAULT 0,
  "kdc_code" character varying(10) DEFAULT '004'::character varying,
  "last_batch_updated" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "embedding" vector(1536),
  "record_source" character varying(40) DEFAULT 'EXTERNAL_INGESTION'::character varying NOT NULL,
  CONSTRAINT "books_pkey" PRIMARY KEY (isbn)
);

CREATE TABLE IF NOT EXISTS public."cert_skill_map" (
  "cert_id" integer NOT NULL,
  "skill_id" integer NOT NULL,
  CONSTRAINT "cert_skill_map_pkey" PRIMARY KEY (cert_id, skill_id)
);

CREATE SEQUENCE IF NOT EXISTS public."certifications_cert_id_seq";

CREATE TABLE IF NOT EXISTS public."certifications" (
  "cert_id" integer DEFAULT nextval('certifications_cert_id_seq'::regclass) NOT NULL,
  "title" character varying(100) NOT NULL,
  "provider" character varying(50) DEFAULT '한국데이터산업진흥원 (K-DATA)'::character varying NOT NULL,
  "test_subjects" text,
  "career_outlook" text,
  "exam_type" character varying(20),
  "pass_score" integer,
  "last_batch_updated" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "embedding" vector(1536),
  "record_source" character varying(40) DEFAULT 'EXTERNAL_INGESTION'::character varying NOT NULL,
  CONSTRAINT "certifications_pkey" PRIMARY KEY (cert_id),
  CONSTRAINT "certifications_title_key" UNIQUE (title)
);

ALTER SEQUENCE public."certifications_cert_id_seq" OWNED BY public."certifications"."cert_id";

CREATE TABLE IF NOT EXISTS public."course_skill_map" (
  "course_id" character varying(50) NOT NULL,
  "skill_id" integer NOT NULL,
  CONSTRAINT "course_skill_map_pkey" PRIMARY KEY (course_id, skill_id)
);

CREATE TABLE IF NOT EXISTS public."courses" (
  "course_id" character varying(50) NOT NULL,
  "title" character varying(200) NOT NULL,
  "category_name" character varying(50),
  "org_name" character varying(100),
  "url" character varying(255),
  "is_matchup" boolean DEFAULT false NOT NULL,
  "cost" numeric(10,2) DEFAULT 0.00 NOT NULL,
  "duration_weeks" integer DEFAULT 4,
  "recruitment_status" character varying(30) DEFAULT 'OPEN'::character varying,
  "syllabus_summary" text,
  "last_batch_updated" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "embedding" vector(1536),
  "platform" character varying(50) DEFAULT 'K-MOOC'::character varying NOT NULL,
  "difficulty" character varying(20) DEFAULT 'BEGINNER'::character varying NOT NULL,
  "record_source" character varying(40) DEFAULT 'EXTERNAL_INGESTION'::character varying NOT NULL,
  CONSTRAINT "chk_courses_difficulty" CHECK (difficulty::text = ANY (ARRAY['BEGINNER'::character varying, 'INTERMEDIATE'::character varying, 'ADVANCED'::character varying]::text[])),
  CONSTRAINT "courses_pkey" PRIMARY KEY (course_id)
);

CREATE TABLE IF NOT EXISTS public."finetuning_datasets" (
  "dataset_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "dataset_type" character varying(20) NOT NULL,
  "record_count" integer NOT NULL,
  "export_path" text NOT NULL,
  "pii_masked" boolean DEFAULT true NOT NULL,
  "filter_criteria" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "finetuning_datasets_dataset_type_check" CHECK (dataset_type::text = ANY (ARRAY['SFT_JSONL'::character varying, 'DPO_JSONL'::character varying, 'HF_DATASET'::character varying]::text[])),
  CONSTRAINT "finetuning_datasets_pkey" PRIMARY KEY (dataset_id)
);

CREATE TABLE IF NOT EXISTS public."job_skill_map" (
  "job_id" integer NOT NULL,
  "skill_id" integer NOT NULL,
  CONSTRAINT "job_skill_map_pkey" PRIMARY KEY (job_id, skill_id)
);

CREATE TABLE IF NOT EXISTS public."jobs" (
  "job_id" integer NOT NULL,
  "title" character varying(100) NOT NULL,
  "ncs_units" text,
  "last_batch_updated" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "embedding" vector(1536),
  "record_source" character varying(40) DEFAULT 'EXTERNAL_INGESTION'::character varying NOT NULL,
  CONSTRAINT "jobs_pkey" PRIMARY KEY (job_id)
);

CREATE TABLE IF NOT EXISTS public."major_skill_map" (
  "major_id" integer NOT NULL,
  "skill_id" integer NOT NULL,
  CONSTRAINT "major_skill_map_pkey" PRIMARY KEY (major_id, skill_id)
);

CREATE TABLE IF NOT EXISTS public."majors" (
  "major_id" integer NOT NULL,
  "title" character varying(100) NOT NULL,
  "curriculum" text,
  "last_batch_updated" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "embedding" vector(1536),
  CONSTRAINT "majors_pkey" PRIMARY KEY (major_id)
);

CREATE TABLE IF NOT EXISTS public."preference_pairs" (
  "pair_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "request_id" uuid NOT NULL,
  "chosen_generation_id" uuid NOT NULL,
  "rejected_generation_id" uuid NOT NULL,
  "prompt_input" jsonb NOT NULL,
  "chosen_output" jsonb NOT NULL,
  "rejected_output" jsonb NOT NULL,
  "user_type" character varying(20) NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "preference_pairs_pkey" PRIMARY KEY (pair_id),
  CONSTRAINT "preference_pairs_user_type_check" CHECK (user_type::text = ANY (ARRAY['REAL_USER'::character varying, 'TEST_MANUAL'::character varying, 'TEST_AUTO'::character varying]::text[]))
);

CREATE TABLE IF NOT EXISTS public."prompt_evaluations" (
  "evaluation_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "prompt_version_id" uuid NOT NULL,
  "evaluator_model" character varying(100) NOT NULL,
  "rubric_scores" jsonb NOT NULL,
  "total_score" numeric(4,2) NOT NULL,
  "passed" boolean DEFAULT false NOT NULL,
  "eval_report" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "prompt_evaluations_pkey" PRIMARY KEY (evaluation_id)
);

CREATE TABLE IF NOT EXISTS public."prompt_optimizations" (
  "optimization_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "target_agent_id" character varying(50) NOT NULL,
  "trigger_threshold" integer DEFAULT 10 NOT NULL,
  "cumulative_real_selections" integer NOT NULL,
  "agent_selection_rate" numeric(5,4) NOT NULL,
  "old_prompt_version_id" uuid NOT NULL,
  "candidate_prompt_text" text NOT NULL,
  "evaluation_id" uuid,
  "status" character varying(30) NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "prompt_optimizations_pkey" PRIMARY KEY (optimization_id),
  CONSTRAINT "prompt_optimizations_status_check" CHECK (status::text = ANY (ARRAY['TRIGGERED'::character varying, 'EVAL_PASSED'::character varying, 'EVAL_FAILED'::character varying, 'ROLLED_BACK'::character varying]::text[])),
  CONSTRAINT "prompt_optimizations_target_agent_id_check" CHECK (target_agent_id::text = ANY (ARRAY['agent_practical'::character varying, 'agent_certified'::character varying, 'agent_fasttrack'::character varying]::text[]))
);

CREATE TABLE IF NOT EXISTS public."prompt_versions" (
  "prompt_version_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "agent_id" character varying(50) NOT NULL,
  "version_num" integer NOT NULL,
  "system_prompt" text NOT NULL,
  "is_active" boolean DEFAULT false NOT NULL,
  "parent_version_id" uuid,
  "change_reason" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "deactivated_at" timestamp with time zone,
  CONSTRAINT "prompt_versions_agent_id_check" CHECK (agent_id::text = ANY (ARRAY['agent_practical'::character varying, 'agent_certified'::character varying, 'agent_fasttrack'::character varying]::text[])),
  CONSTRAINT "prompt_versions_pkey" PRIMARY KEY (prompt_version_id),
  CONSTRAINT "uq_agent_version" UNIQUE (agent_id, version_num)
);

CREATE SEQUENCE IF NOT EXISTS public."regulations_id_seq";

CREATE TABLE IF NOT EXISTS public."regulations" (
  "id" integer DEFAULT nextval('regulations_id_seq'::regclass) NOT NULL,
  "doc_title" character varying(150) NOT NULL,
  "article_no" character varying(50) NOT NULL,
  "category" character varying(50) DEFAULT '교육훈련'::character varying,
  "content" text NOT NULL,
  "embedding" vector(1536),
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "regulations_pkey" PRIMARY KEY (id)
);

ALTER SEQUENCE public."regulations_id_seq" OWNED BY public."regulations"."id";

CREATE TABLE IF NOT EXISTS public."roadmap_requests" (
  "request_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "user_type" character varying(20) NOT NULL,
  "employee_id" character varying(64),
  "raw_user_prompt" text NOT NULL,
  "conversation_history" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "user_requirements" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "retrieved_context" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "roadmap_requests_pkey" PRIMARY KEY (request_id),
  CONSTRAINT "roadmap_requests_user_type_check" CHECK (user_type::text = ANY (ARRAY['REAL_USER'::character varying, 'TEST_MANUAL'::character varying, 'TEST_AUTO'::character varying]::text[]))
);

CREATE SEQUENCE IF NOT EXISTS public."skill_tags_skill_id_seq";

CREATE TABLE IF NOT EXISTS public."skill_tags" (
  "skill_id" integer DEFAULT nextval('skill_tags_skill_id_seq'::regclass) NOT NULL,
  "name" character varying(50) NOT NULL,
  "category" character varying(50) DEFAULT 'IT/Tech'::character varying,
  "embedding" vector(1536),
  CONSTRAINT "skill_tags_pkey" PRIMARY KEY (skill_id),
  CONSTRAINT "skill_tags_skill_name_key" UNIQUE (name)
);

ALTER SEQUENCE public."skill_tags_skill_id_seq" OWNED BY public."skill_tags"."skill_id";

CREATE TABLE IF NOT EXISTS public."test_mock_profiles" (
  "profile_id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "source_type" character varying(20) NOT NULL,
  "profile_name" character varying(100) NOT NULL,
  "department" character varying(100) NOT NULL,
  "grade" character varying(50) NOT NULL,
  "job_title" character varying(100) NOT NULL,
  "career_years" integer NOT NULL,
  "current_skills" jsonb NOT NULL,
  "target_skills" jsonb NOT NULL,
  "work_context_summary" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  CONSTRAINT "test_mock_profiles_pkey" PRIMARY KEY (profile_id),
  CONSTRAINT "test_mock_profiles_source_type_check" CHECK (source_type::text = ANY (ARRAY['TEST_MANUAL'::character varying, 'TEST_AUTO'::character varying]::text[]))
);

CREATE SEQUENCE IF NOT EXISTS public."user_skill_gap_gap_id_seq";

CREATE TABLE IF NOT EXISTS public."user_skill_gap" (
  "gap_id" integer DEFAULT nextval('user_skill_gap_gap_id_seq'::regclass) NOT NULL,
  "user_id" integer,
  "skill_id" integer,
  "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "gap_level" character varying(10) DEFAULT 'HIGH'::character varying,
  CONSTRAINT "user_skill_gap_pkey" PRIMARY KEY (gap_id),
  CONSTRAINT "user_skill_gap_user_id_skill_id_key" UNIQUE (user_id, skill_id)
);

ALTER SEQUENCE public."user_skill_gap_gap_id_seq" OWNED BY public."user_skill_gap"."gap_id";

CREATE SEQUENCE IF NOT EXISTS public."users_user_id_seq";

CREATE TABLE IF NOT EXISTS public."users" (
  "user_id" integer DEFAULT nextval('users_user_id_seq'::regclass) NOT NULL,
  "name" character varying(50) NOT NULL,
  "target_job_id" integer,
  "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  "is_degree_seeker" boolean DEFAULT false,
  CONSTRAINT "users_pkey" PRIMARY KEY (user_id)
);

ALTER SEQUENCE public."users_user_id_seq" OWNED BY public."users"."user_id";

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.agent_generations'::regclass AND conname = 'agent_generations_prompt_version_id_fkey') THEN
    ALTER TABLE public."agent_generations" ADD CONSTRAINT "agent_generations_prompt_version_id_fkey" FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(prompt_version_id);
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.agent_generations'::regclass AND conname = 'agent_generations_request_id_fkey') THEN
    ALTER TABLE public."agent_generations" ADD CONSTRAINT "agent_generations_request_id_fkey" FOREIGN KEY (request_id) REFERENCES roadmap_requests(request_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.agent_selections'::regclass AND conname = 'agent_selections_chosen_generation_id_fkey') THEN
    ALTER TABLE public."agent_selections" ADD CONSTRAINT "agent_selections_chosen_generation_id_fkey" FOREIGN KEY (chosen_generation_id) REFERENCES agent_generations(generation_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.agent_selections'::regclass AND conname = 'agent_selections_request_id_fkey') THEN
    ALTER TABLE public."agent_selections" ADD CONSTRAINT "agent_selections_request_id_fkey" FOREIGN KEY (request_id) REFERENCES roadmap_requests(request_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.book_skill_map'::regclass AND conname = 'book_skill_map_isbn_fkey') THEN
    ALTER TABLE public."book_skill_map" ADD CONSTRAINT "book_skill_map_isbn_fkey" FOREIGN KEY (isbn) REFERENCES books(isbn) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.book_skill_map'::regclass AND conname = 'book_skill_map_skill_id_fkey') THEN
    ALTER TABLE public."book_skill_map" ADD CONSTRAINT "book_skill_map_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.cert_skill_map'::regclass AND conname = 'cert_skill_map_cert_id_fkey') THEN
    ALTER TABLE public."cert_skill_map" ADD CONSTRAINT "cert_skill_map_cert_id_fkey" FOREIGN KEY (cert_id) REFERENCES certifications(cert_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.cert_skill_map'::regclass AND conname = 'cert_skill_map_skill_id_fkey') THEN
    ALTER TABLE public."cert_skill_map" ADD CONSTRAINT "cert_skill_map_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.course_skill_map'::regclass AND conname = 'course_skill_map_course_id_fkey') THEN
    ALTER TABLE public."course_skill_map" ADD CONSTRAINT "course_skill_map_course_id_fkey" FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.course_skill_map'::regclass AND conname = 'course_skill_map_skill_id_fkey') THEN
    ALTER TABLE public."course_skill_map" ADD CONSTRAINT "course_skill_map_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.job_skill_map'::regclass AND conname = 'job_skill_map_job_id_fkey') THEN
    ALTER TABLE public."job_skill_map" ADD CONSTRAINT "job_skill_map_job_id_fkey" FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.job_skill_map'::regclass AND conname = 'job_skill_map_skill_id_fkey') THEN
    ALTER TABLE public."job_skill_map" ADD CONSTRAINT "job_skill_map_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.major_skill_map'::regclass AND conname = 'major_skill_map_major_id_fkey') THEN
    ALTER TABLE public."major_skill_map" ADD CONSTRAINT "major_skill_map_major_id_fkey" FOREIGN KEY (major_id) REFERENCES majors(major_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.major_skill_map'::regclass AND conname = 'major_skill_map_skill_id_fkey') THEN
    ALTER TABLE public."major_skill_map" ADD CONSTRAINT "major_skill_map_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.preference_pairs'::regclass AND conname = 'preference_pairs_chosen_generation_id_fkey') THEN
    ALTER TABLE public."preference_pairs" ADD CONSTRAINT "preference_pairs_chosen_generation_id_fkey" FOREIGN KEY (chosen_generation_id) REFERENCES agent_generations(generation_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.preference_pairs'::regclass AND conname = 'preference_pairs_rejected_generation_id_fkey') THEN
    ALTER TABLE public."preference_pairs" ADD CONSTRAINT "preference_pairs_rejected_generation_id_fkey" FOREIGN KEY (rejected_generation_id) REFERENCES agent_generations(generation_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.preference_pairs'::regclass AND conname = 'preference_pairs_request_id_fkey') THEN
    ALTER TABLE public."preference_pairs" ADD CONSTRAINT "preference_pairs_request_id_fkey" FOREIGN KEY (request_id) REFERENCES roadmap_requests(request_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.prompt_evaluations'::regclass AND conname = 'prompt_evaluations_prompt_version_id_fkey') THEN
    ALTER TABLE public."prompt_evaluations" ADD CONSTRAINT "prompt_evaluations_prompt_version_id_fkey" FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(prompt_version_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.prompt_optimizations'::regclass AND conname = 'prompt_optimizations_evaluation_id_fkey') THEN
    ALTER TABLE public."prompt_optimizations" ADD CONSTRAINT "prompt_optimizations_evaluation_id_fkey" FOREIGN KEY (evaluation_id) REFERENCES prompt_evaluations(evaluation_id);
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.prompt_optimizations'::regclass AND conname = 'prompt_optimizations_old_prompt_version_id_fkey') THEN
    ALTER TABLE public."prompt_optimizations" ADD CONSTRAINT "prompt_optimizations_old_prompt_version_id_fkey" FOREIGN KEY (old_prompt_version_id) REFERENCES prompt_versions(prompt_version_id);
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.prompt_versions'::regclass AND conname = 'prompt_versions_parent_version_id_fkey') THEN
    ALTER TABLE public."prompt_versions" ADD CONSTRAINT "prompt_versions_parent_version_id_fkey" FOREIGN KEY (parent_version_id) REFERENCES prompt_versions(prompt_version_id);
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.user_skill_gap'::regclass AND conname = 'user_skill_gap_skill_id_fkey') THEN
    ALTER TABLE public."user_skill_gap" ADD CONSTRAINT "user_skill_gap_skill_id_fkey" FOREIGN KEY (skill_id) REFERENCES skill_tags(skill_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.user_skill_gap'::regclass AND conname = 'user_skill_gap_user_id_fkey') THEN
    ALTER TABLE public."user_skill_gap" ADD CONSTRAINT "user_skill_gap_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;
  END IF;
END $migration$;

DO $migration$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.users'::regclass AND conname = 'users_target_job_id_fkey') THEN
    ALTER TABLE public."users" ADD CONSTRAINT "users_target_job_id_fkey" FOREIGN KEY (target_job_id) REFERENCES jobs(job_id) ON DELETE SET NULL;
  END IF;
END $migration$;

CREATE UNIQUE INDEX IF NOT EXISTS agent_generations_pkey ON public.agent_generations USING btree (generation_id);

CREATE INDEX IF NOT EXISTS idx_agent_generations_request_id ON public.agent_generations USING btree (request_id);

CREATE INDEX IF NOT EXISTS idx_agent_generations_agent_id ON public.agent_generations USING btree (agent_id);

CREATE UNIQUE INDEX IF NOT EXISTS agent_selections_pkey ON public.agent_selections USING btree (selection_id);

CREATE UNIQUE INDEX IF NOT EXISTS agent_selections_request_id_key ON public.agent_selections USING btree (request_id);

CREATE INDEX IF NOT EXISTS idx_agent_selections_chosen_agent ON public.agent_selections USING btree (chosen_agent_id);

CREATE INDEX IF NOT EXISTS idx_agent_selections_user_type ON public.agent_selections USING btree (user_type);

CREATE UNIQUE INDEX IF NOT EXISTS ai_cases_pkey ON public.ai_cases USING btree (id);

CREATE INDEX IF NOT EXISTS idx_ai_cases_embedding_hnsw ON public.ai_cases USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');

ALTER TABLE public."ai_cases" ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX IF NOT EXISTS book_skill_map_pkey ON public.book_skill_map USING btree (isbn, skill_id);

CREATE INDEX IF NOT EXISTS idx_book_skill_map_skill_id ON public.book_skill_map USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS books_pkey ON public.books USING btree (isbn);

CREATE INDEX IF NOT EXISTS idx_books_loan_count ON public.books USING btree (loan_count DESC);

CREATE INDEX IF NOT EXISTS idx_books_embedding ON public.books USING hnsw (embedding vector_cosine_ops);

CREATE UNIQUE INDEX IF NOT EXISTS cert_skill_map_pkey ON public.cert_skill_map USING btree (cert_id, skill_id);

CREATE INDEX IF NOT EXISTS idx_cert_skill_map_skill_id ON public.cert_skill_map USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS certifications_pkey ON public.certifications USING btree (cert_id);

CREATE UNIQUE INDEX IF NOT EXISTS certifications_title_key ON public.certifications USING btree (title);

CREATE UNIQUE INDEX IF NOT EXISTS course_skill_map_pkey ON public.course_skill_map USING btree (course_id, skill_id);

CREATE INDEX IF NOT EXISTS idx_course_skill_map_skill_id ON public.course_skill_map USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS courses_pkey ON public.courses USING btree (course_id);

CREATE INDEX IF NOT EXISTS idx_courses_embedding ON public.courses USING hnsw (embedding vector_cosine_ops);

CREATE UNIQUE INDEX IF NOT EXISTS finetuning_datasets_pkey ON public.finetuning_datasets USING btree (dataset_id);

CREATE UNIQUE INDEX IF NOT EXISTS job_skill_map_pkey ON public.job_skill_map USING btree (job_id, skill_id);

CREATE INDEX IF NOT EXISTS idx_job_skill_map_skill_id ON public.job_skill_map USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS jobs_pkey ON public.jobs USING btree (job_id);

CREATE UNIQUE INDEX IF NOT EXISTS major_skill_map_pkey ON public.major_skill_map USING btree (major_id, skill_id);

CREATE INDEX IF NOT EXISTS idx_major_skill_map_skill_id ON public.major_skill_map USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS majors_pkey ON public.majors USING btree (major_id);

CREATE UNIQUE INDEX IF NOT EXISTS preference_pairs_pkey ON public.preference_pairs USING btree (pair_id);

CREATE INDEX IF NOT EXISTS idx_preference_pairs_request ON public.preference_pairs USING btree (request_id);

CREATE INDEX IF NOT EXISTS idx_preference_pairs_user_type ON public.preference_pairs USING btree (user_type);

CREATE UNIQUE INDEX IF NOT EXISTS prompt_evaluations_pkey ON public.prompt_evaluations USING btree (evaluation_id);

CREATE INDEX IF NOT EXISTS idx_prompt_evaluations_version ON public.prompt_evaluations USING btree (prompt_version_id);

CREATE UNIQUE INDEX IF NOT EXISTS prompt_optimizations_pkey ON public.prompt_optimizations USING btree (optimization_id);

CREATE UNIQUE INDEX IF NOT EXISTS prompt_versions_pkey ON public.prompt_versions USING btree (prompt_version_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_version ON public.prompt_versions USING btree (agent_id, version_num);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_agent_prompt ON public.prompt_versions USING btree (agent_id) WHERE (is_active = true);

CREATE UNIQUE INDEX IF NOT EXISTS regulations_pkey ON public.regulations USING btree (id);

CREATE INDEX IF NOT EXISTS idx_regulations_embedding_hnsw ON public.regulations USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');

ALTER TABLE public."regulations" ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX IF NOT EXISTS roadmap_requests_pkey ON public.roadmap_requests USING btree (request_id);

CREATE INDEX IF NOT EXISTS idx_roadmap_requests_user_type ON public.roadmap_requests USING btree (user_type);

CREATE INDEX IF NOT EXISTS idx_roadmap_requests_created_at ON public.roadmap_requests USING btree (created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS skill_tags_pkey ON public.skill_tags USING btree (skill_id);

CREATE UNIQUE INDEX IF NOT EXISTS skill_tags_skill_name_key ON public.skill_tags USING btree (name);

CREATE UNIQUE INDEX IF NOT EXISTS test_mock_profiles_pkey ON public.test_mock_profiles USING btree (profile_id);

CREATE UNIQUE INDEX IF NOT EXISTS user_skill_gap_pkey ON public.user_skill_gap USING btree (gap_id);

CREATE UNIQUE INDEX IF NOT EXISTS user_skill_gap_user_id_skill_id_key ON public.user_skill_gap USING btree (user_id, skill_id);

CREATE INDEX IF NOT EXISTS idx_user_skill_gap_user_id ON public.user_skill_gap USING btree (user_id);

ALTER TABLE public."user_skill_gap" ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX IF NOT EXISTS users_pkey ON public.users USING btree (user_id);

ALTER TABLE public."users" ENABLE ROW LEVEL SECURITY;
