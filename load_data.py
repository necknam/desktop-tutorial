"""
프로젝트 데이터 일괄 적재 CLI 진입점
실행: python load_data.py
역할: 표준 스킬 벡터 생성 및 공식 API 실데이터(강좌, 자격증, 도서, 직무) 일괄 적재
"""

import sys
import logging
from src.db.db_client import db_client
from reset_skills import reset_and_seed_skill_tags
from src.core.real_data_ingestor import real_data_ingestor

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("LOAD_DATA")


def main():
    logger.info("==================================================")
    logger.info("      GrowPath AI 전체 데이터 일괄 적재 시작      ")
    logger.info("==================================================")

    # 1. DB 연결 상태 확인
    health = db_client.check_health()
    if not health.get("connected"):
        logger.error(f"데이터베이스 연결 실패: {health.get('message')}")
        logger.error(".env 파일의 SUPABASE_URL 및 SUPABASE_KEY를 확인하세요.")
        sys.exit(1)

    # 2. 표준 스킬 태그 초기화 및 1536차원 임베딩 생성
    logger.info("\n[1/2] 표준 스킬 임베딩 생성 및 skill_tags 적재 중...")
    try:
        reset_and_seed_skill_tags()
    except Exception as e:
        logger.error(f"스킬 임베딩 적재 실패: {e}")
        sys.exit(1)

    # 3. 공식 API 실존 강좌/자격증/도서/직무 수집 및 매핑 연계
    logger.info("\n[2/2] 공식 API 및 실존 공공데이터 전체 적재 파이프라인 가동...")
    try:
        result = real_data_ingestor.execute_full_pipeline()
        if result.get("success"):
            logger.info("--------------------------------------------------")
            logger.info(f"✔ 공식 강좌 수집: {result.get('courses_count')}건")
            logger.info(f"✔ 실존 자격증 적재: {result.get('certs_count')}건")
            logger.info(f"✔ 전문 도서(KDC 004) 적재: {result.get('books_count')}건")
            logger.info(f"✔ NCS 표준 직무 적재: {result.get('jobs_count')}건")
            logger.info(f"✔ 스킬 M:N 매핑 연계: {'성공' if result.get('mapping_success') else '실패'}")
            logger.info("--------------------------------------------------")
            logger.info("🎉 모든 데이터 적재가 성공적으로 완료되었습니다!")
        else:
            logger.error(f"파이프라인 실행 중 오류 발생: {result.get('message')}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"데이터 적재 중 예외 발생: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()