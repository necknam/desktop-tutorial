-- Only synthetic users/profiles are inserted. No skills, courses or regulations are seeded.
-- Run after migrations. The Python CLI wraps this SQL in a transaction.
INSERT INTO public.users(name,email,department,job_title,grade,source_type)
VALUES
  ('테스트_DB사용자','db.demo@growpath.example.invalid','가상 데이터팀','데이터베이스 엔지니어','테스트','TEST_MANUAL'),
  ('테스트_AI사용자','ai.demo@growpath.example.invalid','가상 AI팀','AI 엔지니어','테스트','TEST_MANUAL'),
  ('테스트_MLOps사용자','mlops.demo@growpath.example.invalid','가상 플랫폼팀','MLOps 엔지니어','테스트','TEST_MANUAL'),
  -- K-water V11 일반직 3~5급 우선 상담 가상 직원 5명 (비부서장·비교대 팀원) 및 상급자(2급(을) 팀장) 후보
  ('김전산','jeonsan.kim@growpath.example.invalid','AI본부 디지털관리처','전산 엔지니어','일반직 4급','TEST_MANUAL'),
  ('이행정','haengjung.lee@growpath.example.invalid','경영부문 인재경영실','HR 행정 담당','일반직 4급','TEST_MANUAL'),
  ('박디지털','digital.park@growpath.example.invalid','AI본부 디지털관리처','디지털플랫폼 엔지니어','일반직 5급','TEST_MANUAL'),
  ('최인재','injae.choi@growpath.example.invalid','경영부문 인재경영실','인재육성 기획 담당','일반직 3급','TEST_MANUAL'),
  ('정데이터','data.jung@growpath.example.invalid','AI본부 디지털관리처','데이터시스템 엔지니어','일반직 3급','TEST_MANUAL'),
  ('강팀장','teamlead.kang@growpath.example.invalid','AI본부 디지털관리처','디지털관리 팀장 후보','일반직 2급(을)','TEST_MANUAL')
ON CONFLICT (email) DO NOTHING;

WITH examples(profile_id,profile_name,department,grade,job_title,career_years,context) AS (
  VALUES
    ('f17590a8-0020-4000-8000-000000000001'::uuid,'테스트_DB사용자','가상 데이터팀','테스트','데이터베이스 엔지니어',0,'쿼리 성능 개선 학습을 위한 가상 사용자'),
    ('f17590a8-0020-4000-8000-000000000002'::uuid,'테스트_AI사용자','가상 AI팀','테스트','AI 엔지니어',0,'근거 기반 AI 서비스 학습을 위한 가상 사용자'),
    ('f17590a8-0020-4000-8000-000000000003'::uuid,'테스트_MLOps사용자','가상 플랫폼팀','테스트','MLOps 엔지니어',0,'모델 배포 자동화 학습을 위한 가상 사용자'),
    ('f17590a8-0020-4000-8000-000000000011'::uuid,'김전산','AI본부 디지털관리처','일반직 4급','전산 엔지니어',4,'디지털관리처 전산 업무 팀원(비부서장·비교대), 연간 기준학습시간 80시간 및 직무역량 필수교육 상담 대상'),
    ('f17590a8-0020-4000-8000-000000000012'::uuid,'이행정','경영부문 인재경영실','일반직 4급','HR 행정 담당',5,'인재경영실 행정 업무 팀원(비부서장·비교대), 인사·교육훈련평정 및 승진소요최저연수 상담 대상'),
    ('f17590a8-0020-4000-8000-000000000013'::uuid,'박디지털','AI본부 디지털관리처','일반직 5급','디지털플랫폼 엔지니어',1,'디지털관리처 전산 업무 신입·주임 팀원(비부서장·비교대), 연간 기준학습시간 112시간(담당부서 40% 의무) 상담 대상'),
    ('f17590a8-0020-4000-8000-000000000014'::uuid,'최인재','경영부문 인재경영실','일반직 3급','인재육성 기획 담당',8,'인재경영실 행정 업무 선임 팀원(비부서장·비교대), 연간 기준학습시간 64시간 및 3급 근무성적·경력평정 상담 대상'),
    ('f17590a8-0020-4000-8000-000000000015'::uuid,'정데이터','AI본부 디지털관리처','일반직 3급','데이터시스템 엔지니어',7,'디지털관리처 전산 업무 선임 팀원(비부서장·비교대), 전산직렬 현장근무 예외 및 기술 자격 가감평정 상담 대상'),
    ('f17590a8-0020-4000-8000-000000000016'::uuid,'강팀장','AI본부 디지털관리처','일반직 2급(을)','디지털관리 팀장 후보',12,'상급자(팀장 직위 후보) 참고 프로필, 직급·직위 분리 운영 및 2급(을) 평정 기준 참고 대상')
)
INSERT INTO public.test_mock_profiles
  (profile_id,source_type,profile_name,department,grade,job_title,career_years,current_skills,target_skills,work_context_summary)
SELECT profile_id,'TEST_MANUAL',profile_name,department,grade,job_title,career_years,'[]'::jsonb,'[]'::jsonb,context
FROM examples
ON CONFLICT (profile_id) DO NOTHING;