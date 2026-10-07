-- ====================================================================
-- Step 1: IT 직무 도메인 모의 마스터 데이터 시딩 스크립트
-- ====================================================================

-- 1. 표준 스킬 태그 시딩 (기존 skill_tags 보완)
INSERT INTO skill_tags (skill_id, name, category) VALUES
(101, 'Python', 'Programming'),
(102, 'FastAPI', 'Backend'),
(103, 'PostgreSQL', 'Database'),
(104, 'Docker', 'DevOps'),
(105, 'Kubernetes', 'DevOps'),
(106, 'AWS Cloud', 'Cloud'),
(107, 'Git/GitHub', 'Tools'),
(108, 'CI/CD Pipeline', 'DevOps'),
(109, 'SQL Tuning', 'Database'),
(110, 'Data Modeling', 'Database')
ON CONFLICT (skill_id) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category;

-- 2. 표준 직무 마스터 시딩
INSERT INTO jobs (job_id, title, ncs_units, record_source) VALUES
(1, '백엔드 소프트웨어 엔지니어', '서버 아키텍처 설계, RESTful/gRPC API 구현, 데이터베이스 모델링 및 최적화', 'MOCK_INIT_SEED'),
(2, '클라우드 인프라/데브옵스 엔지니어', '클라우드 인프라 구축, 컨테이너 오케스트레이션, CI/CD 무중단 자동화 파이프라인 운영', 'MOCK_INIT_SEED'),
(3, '데이터 플랫폼 엔지니어', '대용량 데이터 수집 및 정제, 데이터 웨어하우스/레이크 모델링, SQL 질의 최적화', 'MOCK_INIT_SEED')
ON CONFLICT (job_id) DO UPDATE SET title = EXCLUDED.title, ncs_units = EXCLUDED.ncs_units, record_source = EXCLUDED.record_source;

-- 3. 온라인 교육 강좌 마스터 시딩 (is_matchup 컬럼에 false / true 불리언 적용)
INSERT INTO courses (
    course_id, title, category_name, org_name, url, is_matchup,
    cost, duration_weeks, recruitment_status, syllabus_summary,
    platform, difficulty, record_source
) VALUES
('C_KMOOC_001', 'FastAPI와 PostgreSQL 기반 마이크로서비스 백엔드 개발', 'SW/AI', '한국SW산업협회', 'https://www.kmooc.kr/course/fastapi_pg', false, 0.00, 8, '학습가능', '비동기 파이썬 기초부터 FastAPI 프레임워크 실습 및 RDBMS 데이터 모델링', 'K-MOOC', 'INTERMEDIATE', 'MOCK_INIT_SEED'),
('C_MS_002', 'Docker & Kubernetes 클라우드 컨테이너 아키텍처 실무', 'Cloud', 'Microsoft Learn', 'https://learn.microsoft.com/k8s-arch', true, 0.00, 4, '상시학습', '컨테이너 가상화 원리와 Kubernetes 클러스터 배포 및 오토스케일링 실무', 'MS Learn', 'INTERMEDIATE', 'MOCK_INIT_SEED'),
('C_MS_003', 'AWS 클라우드 인프라 아키텍처 설계와 Terraform 자동화', 'Cloud', 'Amazon Web Services', 'https://aws.amazon.com/training/arch-iac', true, 0.00, 4, '상시학습', 'VPC 네트워크 설계, IAM 보안 권한 수립 및 코드로 관리하는 인프라 실습', 'MS Learn', 'ADVANCED', 'MOCK_INIT_SEED'),
('C_KMOOC_004', 'Python 프로그래밍 기초 및 객체지향 설계 원리', '기초SW', '서울대학교', 'https://www.kmooc.kr/course/py_basic', false, 0.00, 6, '학습가능', '파이썬 기초 문법, 자료구조, 객체지향 및 함수형 프로그래밍 입문', 'K-MOOC', 'BEGINNER', 'MOCK_INIT_SEED'),
('C_KMOOC_005', '대용량 데이터베이스 모델링 및 SQL 성능 튜닝 실무', 'Database', '한국데이터산업진흥원', 'https://www.kmooc.kr/course/sql_tuning', false, 0.00, 8, '학습가능', '관계형 데이터베이스 인덱스 최적화, 실행계획 분석 및 트랜잭션 격리', 'K-MOOC', 'ADVANCED', 'MOCK_INIT_SEED'),
('C_MS_006', 'GitHub Actions를 활용한 무중단 CI/CD 배포 자동화', 'DevOps', 'GitHub Learning Lab', 'https://learn.microsoft.com/github-cicd', true, 0.00, 2, '상시학습', '테스트 자동화, 컨테이너 빌드 및 운영 환경 무중단 배포 파이프라인 구성', 'MS Learn', 'BEGINNER', 'MOCK_INIT_SEED')
ON CONFLICT (course_id) DO UPDATE SET title = EXCLUDED.title, is_matchup = EXCLUDED.is_matchup, record_source = EXCLUDED.record_source;

-- 4. 공인 자격증 마스터 시딩
INSERT INTO certifications (cert_id, title, provider, test_subjects, career_outlook, exam_type, pass_score, record_source) VALUES
(201, '정보처리기사', '한국산업인력공단', '소프트웨어 설계, 소프트웨어 개발, 데이터베이스 구축, 프로그래밍 언어 활용, 정보시스템 구축관리', 'SW 개발 및 공공/엔터프라이즈 프로젝트 필수 국가공인 자격', '필기/실기', 60, 'MOCK_INIT_SEED'),
(202, 'AWS Certified Solutions Architect - Associate', 'Amazon Web Services', '복원력 있는 아키텍처 설계, 고성능 아키텍처 설계, 보안 애플리케이션 및 아키텍처 설계, 비용 최적화 아키텍처 설계', '글로벌 엔터프라이즈 클라우드 엔지니어링 표준 자격', 'CBT 객관식', 720, 'MOCK_INIT_SEED'),
(203, 'SQL 개발자 (SQLD)', '한국데이터산업진흥원', '데이터 모델링의 이해, SQL 기본 및 활용', '백엔드 및 데이터 엔지니어의 쿼리 작성 및 RDBMS 최적화 공인 자격', '필기 객관식/단답형', 60, 'MOCK_INIT_SEED'),
(204, 'Certified Kubernetes Administrator (CKA)', 'Cloud Native Computing Foundation', '스토리지, 트러블슈팅, 아키텍처/빌드/유지보수, 워크로드/스케줄링, 클러스터 보안/네트워킹', '쿠버네티스 컨테이너 환경 공인 운영 전문가 실습 자격', '실습형 CBT', 66, 'MOCK_INIT_SEED')
ON CONFLICT (cert_id) DO UPDATE SET title = EXCLUDED.title, record_source = EXCLUDED.record_source;

-- 5. 전문 도서 마스터 시딩 (KDC 004 컴퓨터과학)
INSERT INTO books (isbn, title, author, publisher, loan_count, kdc_code, record_source) VALUES
('9788966263301', '파이썬 코딩의 기술 (내용을 더 쉽게 만드는 90가지 방법)', '브렛 슬라킨', '길벗', 185, '004', 'MOCK_INIT_SEED'),
('9788966262281', '쿠버네티스 인 액션 (기초부터 배포와 클러스터 관리까지)', '마르코 루크샤', '에이콘출판', 142, '004', 'MOCK_INIT_SEED'),
('9788968484698', '가상 면접 사례로 배우는 대규모 시스템 설계 기초', '알렉스 쉬', '인사이트', 310, '004', 'MOCK_INIT_SEED'),
('9788960777415', 'SQL 첫걸음 (아침 8시부터 읽는 데이터베이스 기초)', '아사이 아츠시', '한빛미디어', 230, '004', 'MOCK_INIT_SEED')
ON CONFLICT (isbn) DO UPDATE SET title = EXCLUDED.title, record_source = EXCLUDED.record_source;

-- 6. 스킬-엔티티 매핑 데이터 시딩
-- (1) 직무-스킬 매핑
INSERT INTO job_skill_map (job_id, skill_id) VALUES
(1, 101), (1, 102), (1, 103), (1, 107), (1, 109), (1, 110),
(2, 104), (2, 105), (2, 106), (2, 107), (2, 108),
(3, 101), (3, 103), (3, 109), (3, 110)
ON CONFLICT DO NOTHING;

-- (2) 강좌-스킬 매핑
INSERT INTO course_skill_map (course_id, skill_id) VALUES
('C_KMOOC_001', 101), ('C_KMOOC_001', 102), ('C_KMOOC_001', 103),
('C_MS_002', 104), ('C_MS_002', 105),
('C_MS_003', 106),
('C_KMOOC_004', 101),
('C_KMOOC_005', 103), ('C_KMOOC_005', 109), ('C_KMOOC_005', 110),
('C_MS_006', 107), ('C_MS_006', 108)
ON CONFLICT DO NOTHING;

-- (3) 자격증-스킬 매핑
INSERT INTO cert_skill_map (cert_id, skill_id) VALUES
(201, 101), (201, 103), (201, 107),
(202, 106),
(203, 103), (203, 109), (203, 110),
(204, 104), (204, 105)
ON CONFLICT DO NOTHING;

-- (4) 도서-스킬 매핑
INSERT INTO book_skill_map (isbn, skill_id) VALUES
('9788966263301', 101),
('9788966262281', 104), ('9788966262281', 105),
('9788968484698', 102), ('9788968484698', 106),
('9788960777415', 103), ('9788960777415', 109)
ON CONFLICT DO NOTHING;

-- 7. 테스트용 임의 인사 프로필 풀 시딩
INSERT INTO test_mock_profiles (
    source_type, profile_name, department, grade, job_title,
    career_years, current_skills, target_skills, work_context_summary
) VALUES
(
    'TEST_MANUAL',
    '김백엔',
    '플랫폼개발1팀',
    '선임연구원 (사원)',
    '백엔드 개발자',
    2,
    '["Python", "Git/GitHub"]'::jsonb,
    '["FastAPI", "PostgreSQL", "Docker"]'::jsonb,
    '레거시 모놀리식 시스템의 API 마이크로서비스 전환 및 비동기 처리 도입을 목표로 하고 있음.'
),
(
    'TEST_MANUAL',
    '이데옵',
    '클라우드운영팀',
    '선임 (대리)',
    '데브옵스 엔지니어',
    4,
    '["Docker", "Linux"]'::jsonb,
    '["Kubernetes", "AWS Cloud", "CI/CD Pipeline"]'::jsonb,
    '온프레미스 인프라를 AWS EKS 기반 클라우드 환경으로 이전하고 배포 파이프라인 자동화 추진.'
),
(
    'TEST_MANUAL',
    '박데이터',
    '데이터인텔리전스팀',
    '책임연구원 (과장)',
    '데이터 엔지니어',
    7,
    '["Python", "SQL 기본"]'::jsonb,
    '["SQL Tuning", "Data Modeling", "PostgreSQL"]'::jsonb,
    '실시간 데이터 파이프라인 확장 및 대용량 배치 쿼리의 병목 해소를 위한 튜닝 역량 강화 필요.'
),
(
    'TEST_AUTO',
    '정신입',
    '솔루션사업부',
    '연구원 (신입)',
    '주니어 풀스택 개발자',
    1,
    '["Python 기초", "HTML/CSS"]'::jsonb,
    '["FastAPI", "PostgreSQL", "Git/GitHub"]'::jsonb,
    '현업 실무 프로젝트 조기 투입을 위한 표준 웹 백엔드 스택 단기 습득 희망.'
),
(
    'TEST_AUTO',
    '최아키',
    '차세대아키텍처팀',
    '수석연구원 (차장)',
    '시스템 아키텍트',
    11,
    '["Java", "Spring", "Oracle"]'::jsonb,
    '["AWS Cloud", "Kubernetes", "FastAPI"]'::jsonb,
    '전사 차세대 클라우드 네이티브 전환을 주도하기 위한 최신 MSA 및 오케스트레이션 아키텍처 학습.'
);