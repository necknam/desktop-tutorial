-- Only synthetic users/profiles are inserted. No skills, courses or regulations are seeded.
-- Run after migrations. The Python CLI wraps this SQL in a transaction.
INSERT INTO public.users(name,email,department,job_title,grade,source_type)
VALUES
  ('테스트_DB사용자','db.demo@growpath.example.invalid','가상 데이터팀','데이터베이스 엔지니어','테스트','TEST_MANUAL'),
  ('테스트_AI사용자','ai.demo@growpath.example.invalid','가상 AI팀','AI 엔지니어','테스트','TEST_MANUAL'),
  ('테스트_MLOps사용자','mlops.demo@growpath.example.invalid','가상 플랫폼팀','MLOps 엔지니어','테스트','TEST_MANUAL')
ON CONFLICT (email) DO NOTHING;

WITH examples(profile_id,profile_name,department,job_title,context) AS (
  VALUES
    ('f17590a8-0020-4000-8000-000000000001'::uuid,'테스트_DB사용자','가상 데이터팀','데이터베이스 엔지니어','쿼리 성능 개선 학습을 위한 가상 사용자'),
    ('f17590a8-0020-4000-8000-000000000002'::uuid,'테스트_AI사용자','가상 AI팀','AI 엔지니어','근거 기반 AI 서비스 학습을 위한 가상 사용자'),
    ('f17590a8-0020-4000-8000-000000000003'::uuid,'테스트_MLOps사용자','가상 플랫폼팀','MLOps 엔지니어','모델 배포 자동화 학습을 위한 가상 사용자')
)
INSERT INTO public.test_mock_profiles
  (profile_id,source_type,profile_name,department,grade,job_title,career_years,current_skills,target_skills,work_context_summary)
SELECT profile_id,'TEST_MANUAL',profile_name,department,'테스트',job_title,0,'[]'::jsonb,'[]'::jsonb,context
FROM examples
ON CONFLICT (profile_id) DO NOTHING;
