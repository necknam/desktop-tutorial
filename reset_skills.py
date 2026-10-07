import logging
from database.db_client import db_client
from core.embedding_service import embedding_service
from core.real_data_ingestor import real_data_ingestor

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("RESET_SKILLS")

STANDARD_SKILLS = [
    {"skill_id": 101, "name": "Python", "category": "Programming"},
    {"skill_id": 102, "name": "FastAPI", "category": "Backend/API"},
    {"skill_id": 103, "name": "PostgreSQL", "category": "Database"},
    {"skill_id": 104, "name": "Docker", "category": "DevOps/Container"},
    {"skill_id": 105, "name": "Kubernetes", "category": "Cloud/Orchestration"},
    {"skill_id": 106, "name": "Cloud Architecture", "category": "Cloud/Infra"},
    {"skill_id": 107, "name": "Git/GitHub", "category": "Collaboration/VCS"},
    {"skill_id": 108, "name": "CI/CD Pipeline", "category": "DevOps/Automation"},
    {"skill_id": 109, "name": "Database Tuning & SQL", "category": "Database/Performance"},
    {"skill_id": 110, "name": "Software Quality & Testing", "category": "Software Engineering"},
]

def reset_and_seed_skill_tags():
    client = db_client.client
    if client is None:
        logger.error("Supabase DB 클라이언트가 연결되어 있지 않습니다.")
        return

    logger.info("1. 기존 스킬 매핑 및 skill_tags 테이블 초기화 시작...")
    client.table("course_skill_map").delete().neq("skill_id", -9999).execute()
    client.table("cert_skill_map").delete().neq("skill_id", -9999).execute()
    client.table("book_skill_map").delete().neq("skill_id", -9999).execute()
    client.table("job_skill_map").delete().neq("skill_id", -9999).execute()
    client.table("skill_tags").delete().neq("skill_id", -9999).execute()
    logger.info("   -> 더미 스킬 및 매핑 테이블 삭제 완료.")

    logger.info("2. 표준 스킬 태그 1536차원 OpenAI 임베딩 생성 및 적재...")
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

    logger.info("=== 스킬 테이블 초기화 및 고유 임베딩 재적재 완료! ===")

if __name__ == "__main__":
    reset_and_seed_skill_tags()