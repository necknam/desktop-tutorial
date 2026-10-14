import logging
from src.db.db_client import db_client
from src.clients.embedding_service import embedding_service
from src.pipelines.real_data_ingestor import real_data_ingestor

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("RESET_SKILLS")

# 36개 표준 실무 스킬 태그 정의 (영구 소스코드화)
STANDARD_SKILLS = [
    {"skill_id": 1, "name": "SQL 기본 문법 & 조인", "category": "데이터베이스"},
    {"skill_id": 2, "name": "SQL 쿼리 튜닝 & 인덱스 최적화", "category": "데이터베이스"},
    {"skill_id": 3, "name": "RDBMS 데이터 모델링", "category": "데이터베이스"},
    {"skill_id": 4, "name": "NoSQL & 분산 DB", "category": "데이터베이스"},
    {"skill_id": 5, "name": "ETL & 데이터 파이프라인", "category": "데이터 엔지니어링"},
    {"skill_id": 6, "name": "데이터 웨어하우스 & OLAP", "category": "데이터 엔지니어링"},
    {"skill_id": 7, "name": "탐색적 데이터 분석(EDA) & 전처리", "category": "데이터 분석"},
    {"skill_id": 8, "name": "머신러닝 지도/비지도 학습", "category": "머신러닝"},
    {"skill_id": 9, "name": "MLOps & 모델 서빙", "category": "머신러닝"},
    {"skill_id": 10, "name": "딥러닝 신경망 아키텍처", "category": "딥러닝"},
    {"skill_id": 11, "name": "PyTorch/TensorFlow 딥러닝 구현", "category": "딥러닝"},
    {"skill_id": 12, "name": "자연어 처리(NLP) & LLM 기초", "category": "딥러닝"},
    {"skill_id": 13, "name": "RAG & 생성형 AI 에이전트", "category": "인공지능"},
    {"skill_id": 14, "name": "컴퓨터 비전(CV) 모델링", "category": "인공지능"},
    {"skill_id": 15, "name": "리눅스 환경 및 Bash 스크립팅", "category": "인프라/OS"},
    {"skill_id": 16, "name": "TCP/IP 네트워크 프로토콜", "category": "네트워크"},
    {"skill_id": 17, "name": "클라우드 컴퓨팅 아키텍처 (AWS/Azure)", "category": "클라우드"},
    {"skill_id": 18, "name": "클라우드 IAM & 네트워크 보안", "category": "클라우드"},
    {"skill_id": 19, "name": "Docker 컨테이너 가상화", "category": "DevOps"},
    {"skill_id": 20, "name": "Kubernetes 클러스터 오케스트레이션", "category": "DevOps"},
    {"skill_id": 21, "name": "CI/CD 배포 자동화 파이프라인", "category": "DevOps"},
    {"skill_id": 22, "name": "Python 프로그래밍 & 비동기 I/O", "category": "프로그래밍"},
    {"skill_id": 23, "name": "자료구조 & 알고리즘 최적화", "category": "컴퓨터공학 기초"},
    {"skill_id": 24, "name": "운영체제(OS) & 동시성 제어", "category": "컴퓨터공학 기초"},
    {"skill_id": 25, "name": "컴퓨터 구조 & 메모리 관리", "category": "컴퓨터공학 기초"},
    {"skill_id": 26, "name": "객체지향 설계(OOP) & 디자인 패턴", "category": "소프트웨어공학"},
    {"skill_id": 27, "name": "RESTful API 서버 아키텍처", "category": "백엔드"},
    {"skill_id": 28, "name": "대용량 트래픽 처리 & 캐싱", "category": "백엔드"},
    {"skill_id": 29, "name": "마이크로서비스(MSA) 설계", "category": "백엔드"},
    {"skill_id": 30, "name": "웹 프레임워크 (FastAPI/Django)", "category": "백엔드"},
    {"skill_id": 31, "name": "시스템 프로그래밍 & POSIX API", "category": "시스템"},
    {"skill_id": 32, "name": "멀티스레딩 & 비동기 동시성", "category": "시스템"},
    {"skill_id": 33, "name": "시스템 & OS 보안 강화", "category": "정보보안"},
    {"skill_id": 34, "name": "웹 애플리케이션 취약점 진단", "category": "정보보안"},
    {"skill_id": 35, "name": "네트워크 보안 & 침입 탐지", "category": "정보보안"},
    {"skill_id": 36, "name": "데이터 거버넌스 & 보안 규정 준수", "category": "정보보안"},
]


def reset_and_seed_skill_tags():
    client = db_client.client
    if client is None:
        logger.error("Supabase DB 클라이언트가 연결되어 있지 않습니다.")
        return

    logger.info("1. 기존 스킬 매핑 및 skill_tags 테이블 초기화 시작...")
    # 5대 매핑 브릿지 및 스킬 테이블 초기화 (major_skill_map 포함)
    for table in ["major_skill_map", "course_skill_map", "cert_skill_map", "book_skill_map", "job_skill_map", "skill_tags"]:
        try:
            client.table(table).delete().neq("skill_id", -9999).execute()
        except Exception as e:
            logger.warning(f"테이블 {table} 초기화 건너뜀 또는 에러: {e}")
    logger.info("   -> 더미 스킬 및 5대 매핑 테이블 삭제 완료.")

    logger.info("2. 표준 36개 스킬 태그 1536차원 OpenAI 임베딩 생성 및 적재...")
    rows_to_insert = []
    for item in STANDARD_SKILLS:
        skill_text = f"{item['name']} ({item['category']})"
        vec = embedding_service.get_embedding(skill_text)
        rows_to_insert.append({
            "skill_id": item["skill_id"],
            "name": item["name"],
            "category": item["category"],
            "embedding": vec,
        })
        logger.info(f"   - [{item['skill_id']}] {item['name']} 임베딩 생성 완료")

    client.table("skill_tags").upsert(rows_to_insert).execute()
    logger.info(f"   -> skill_tags에 {len(rows_to_insert)}건의 표준 스킬 적재 완료.")

    logger.info("3. 실데이터 엔티티와 스킬 간 M:N 매핑 재연계...")
    mapped = real_data_ingestor.map_skills_to_entities()
    if mapped:
        logger.info("   -> M:N 매핑이 성공적으로 재구성되었습니다.")
    else:
        logger.warning("   -> 매핑 연계 중 일부 누락이 발생했습니다.")

    # 4. 전공 학과(majors)와 스킬 간 매핑 자동 연계
    _map_majors_to_skills(client)

    logger.info("=== 스킬 테이블 초기화 및 36개 임베딩 재적재 완료! ===")


def _map_majors_to_skills(client):
    """DB 이전 시에도 자동으로 majors와 skill_tags를 안전하게 연계하는 내부 로직"""
    try:
        res = client.table("majors").select("major_id, title").execute()
        majors_data = res.data or []
        if not majors_data:
            return

        mappings = []
        for m in majors_data:
            m_id = m["major_id"]
            title = m.get("title", "")
            # 전공 학과별 대표 스킬 2~3개 자동 할당
            if any(k in title for k in ["컴퓨터", "소프트웨어", "전산"]):
                for s_id in [22, 23, 3]:  # Python, 자료구조, DB
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            elif any(k in title for k in ["인공지능", "AI", "지능"]):
                for s_id in [8, 10, 11]:  # 머신러닝, 딥러닝, PyTorch
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            elif any(k in title for k in ["데이터", "통계", "빅데이터"]):
                for s_id in [1, 5, 7]:  # SQL, ETL, EDA
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            elif any(k in title for k in ["보안", "정보보호"]):
                for s_id in [33, 34, 35]:  # 시스템보안, 웹취약점, 네트워크보안
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            elif any(k in title for k in ["통신", "네트워크"]):
                for s_id in [16, 17]:  # TCP/IP, 클라우드
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            elif any(k in title for k in ["전자", "임베디드", "시스템"]):
                for s_id in [15, 31]:  # 리눅스, 시스템 프로그래밍
                    mappings.append({"major_id": m_id, "skill_id": s_id})
            else:
                for s_id in [1, 22]:  # 기본 교양 SW
                    mappings.append({"major_id": m_id, "skill_id": s_id})

        if mappings:
            client.table("major_skill_map").upsert(mappings).execute()
            logger.info(f"   -> major_skill_map {len(mappings)}건 자동 연계 완료.")
    except Exception as e:
        logger.warning(f"전공-스킬 자동 매핑 중 경고: {e}")


if __name__ == "__main__":
    reset_and_seed_skill_tags()