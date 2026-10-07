-- ==============================================================================
-- [규칙 기반 자동 역매핑 엔진] 학과 교과목(curriculum) 텍스트 ↔ 36개 스킬 자동 바인딩
-- 설명: 학과 ID 하드코딩 없이, 교과목 설명문과 정규식 패턴을 동적 조인하여 매핑 생성
-- ==============================================================================

WITH skill_regex_rules AS (
    -- 36개 세분화 스킬별 정규식 탐지 규칙 정의
    SELECT 'SQL 기본 문법 & 조인' AS skill_name, '(\bsql\b.*기초|\bsql\b.*문법|\bselect\b.*문|조인|관계대수)' AS pattern UNION ALL
    SELECT 'SQL 쿼리 튜닝 & 인덱스 최적화', '(인덱스|튜닝|실행계획|쿼리.*최적화|\bsql\b.*최적화|\bsql\b.*질의)' UNION ALL
    SELECT 'RDBMS 데이터 모델링 & 정규화', '(데이터베이스|관계형|정규화|개체관계|e-r|데이터.*모델링)' UNION ALL
    SELECT 'NoSQL & 분산 데이터베이스', '(nosql|mongodb|redis|분산.*데이터베이스|비정형.*데이터)' UNION ALL
    SELECT 'ETL & 데이터 파이프라인 구축', '(파이프라인|etl|데이터.*수집|빅데이터.*시스템|데이터.*처리)' UNION ALL
    SELECT '데이터 웨어하우스 & OLAP', '(데이터.*웨어하우스|olap|대용량.*분석|데이터.*마트)' UNION ALL
    SELECT '탐색적 데이터 분석(EDA) & 특성공학', '(데이터.*분석|eda|통계.*분석|특성.*공학|데이터.*시각화)' UNION ALL
    SELECT '머신러닝 지도/비지도 모델링', '(머신러닝|기계학습|회귀|분류|군집화|앙상블)' UNION ALL
    SELECT '딥러닝 심층신경망(CNN/RNN) 아키텍처', '(딥러닝|신경망|심층학습|cnn|rnn|트랜스포머)' UNION ALL
    SELECT 'PyTorch/TensorFlow 딥러닝 구현', '(pytorch|tensorflow|파이토치|텐서플로우|딥러닝.*실습)' UNION ALL
    SELECT '자연어처리(NLP) & 거대언어모델(LLM)', '(자연어처리|\bnlp\b|언어모델|\bllm\b|텍스트마이닝)' UNION ALL
    SELECT 'RAG & 프롬프트 엔지니어링', '(rag|프롬프트|검색증강|생성형|임베딩)' UNION ALL
    SELECT '컴퓨터 비전 & 이미지 처리', '(컴퓨터비전|영상처리|이미지.*처리|객체인식)' UNION ALL
    SELECT 'MLOps & 모델 서빙 인프라', '(mlops|모델.*배포|모델.*모니터링|서빙)' UNION ALL
    SELECT '리눅스 시스템 & 쉘 스크립트', '(리눅스|linux|유닉스|쉘.*스크립트|커널)' UNION ALL
    SELECT 'TCP/IP & 컴퓨터 네트워크 기초', '(네트워크|tcp/ip|소켓|라우팅|통신.*프로토콜)' UNION ALL
    SELECT '클라우드 인프라 아키텍처 (AWS/GCP)', '(클라우드|aws|가상화.*인프라|클라우드.*컴퓨팅)' UNION ALL
    SELECT 'Docker 컨테이너 가상화', '(도커|docker|컨테이너|가상화.*기술)' UNION ALL
    SELECT 'Kubernetes 오케스트레이션', '(쿠버네티스|kubernetes|오케스트레이션|클러스터)' UNION ALL
    SELECT 'CI/CD 자동화 배포 파이프라인', '(ci/cd|지속적.*통합|배포.*자동화|파이프라인.*구축)' UNION ALL
    SELECT '클라우드 인프라 보안 & IAM', '(클라우드.*보안|iam|접근제어|인프라.*보안)' UNION ALL
    SELECT 'Python 심화 & 비동기 프로그래밍', '(파이썬|python|비동기|객체지향.*파이썬)' UNION ALL
    SELECT '자료구조 & 코딩테스트 알고리즘', '(자료구조|알고리즘|탐색|정렬|복잡도|그래프)' UNION ALL
    SELECT '객체지향 설계(OOP) & 디자인 패턴', '(객체지향|\boop\b|디자인패턴|소프트웨어공학|uml)' UNION ALL
    SELECT 'RESTful API 아키텍처 설계', '(rest|api|웹.*서비스|웹.*애플리케이션)' UNION ALL
    SELECT '대용량 트래픽 처리 & 캐싱(Redis)', '(캐싱|트래픽|로드밸런싱|분산처리)' UNION ALL
    SELECT '마이크로서비스 아키텍처(MSA)', '(msa|마이크로서비스|도메인.*주도)' UNION ALL
    SELECT '웹 프레임워크 실무 (FastAPI/Django)', '(웹.*프레임워크|django|fastapi|spring)' UNION ALL
    SELECT '운영체제 커널 & 프로세스 동기화', '(운영체제|프로세스|스레드|동기화|메모리.*관리)' UNION ALL
    SELECT '컴퓨터 구조 & 메모리 계층 최적화', '(컴퓨터구조|컴퓨터.*시스템|명령어|캐시.*메모리)' UNION ALL
    SELECT '시스템 프로그래밍 & 저수준 I/O', '(시스템.*프로그래밍|저수준|어셈블리|시스템.*호출)' UNION ALL
    SELECT '동시성 제어 & 멀티스레딩', '(동시성|멀티스레드|병렬.*처리|락.*제어)' UNION ALL
    SELECT '시스템 & 리눅스 커널 보안', '(시스템.*보안|악성코드|커널.*보안|취약점.*점검)' UNION ALL
    SELECT '웹 애플리케이션 취약점 진단', '(웹.*보안|xss|sql.*인젝션|취약점.*분석)' UNION ALL
    SELECT '네트워크 보안 & 침입탐지(IDS/IPS)', '(네트워크.*보안|방화벽|ids|ips|패킷.*분석)' UNION ALL
    SELECT '암호학 기초 & 데이터 거버넌스', '(암호|정보보호.*관리|데이터.*보안|보안.*거버넌스)'
)
-- 텍스트 매칭 조인을 통한 브릿지 매핑 일괄 생성 (임의 ID 지정 배제)
INSERT INTO major_skill_map (major_id, skill_id)
SELECT DISTINCT
    m.major_id,
    st.skill_id
FROM majors m
CROSS JOIN skill_regex_rules srr
JOIN skill_tags st ON st.name = srr.skill_name
WHERE (m.title || ' ' || COALESCE(m.curriculum, '')) ~* srr.pattern
ON CONFLICT (major_id, skill_id) DO NOTHING;
