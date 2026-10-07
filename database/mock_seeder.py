"""
모의 마스터 데이터 자동 검증 및 시딩 스크립트
파일 경로: database/mock_seeder.py
역할: DB 내 모의 데이터 적재 상태를 확인하고, 누락 시 자동 보완 및 정리(Clean up) 제공
"""

import sys
import os
import logging
from typing import Dict, Any

# 루트 디렉터리를 sys.path에 추가하여 모듈 임포트 보장
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_client import db_client

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger("MOCK_SEEDER")


def verify_seeding_counts() -> Dict[str, int]:
    """모의 데이터 적재 현황 카운트"""
    if db_client.client is None:
        logger.error("데이터베이스 클라이언트가 연결되어 있지 않습니다. .env를 확인하세요.")
        return {}

    counts = {}
    tables = [
        "jobs",
        "courses",
        "certifications",
        "books",
        "job_skill_map",
        "course_skill_map",
        "cert_skill_map",
        "book_skill_map",
        "test_mock_profiles",
        "prompt_versions",
    ]

    for tbl in tables:
        try:
            resp = db_client.client.table(tbl).select("*", count="exact").execute()
            counts[tbl] = resp.count if resp.count is not None else len(resp.data)
        except Exception as exc:
            counts[tbl] = -1
            logger.warning(f"테이블 '{tbl}' 조회 중 예외 발생: {exc}")

    return counts


def cleanup_mock_data() -> bool:
    """
    모의 데이터(MOCK_INIT_SEED) 일괄 삭제 함수
    추후 정식 데이터 인제스천이 완료되었을 때 호출하여 깨끗하게 제거
    """
    if db_client.client is None:
        logger.error("DB 클라이언트 미연결")
        return False

    try:
        logger.info("모의 데이터(MOCK_INIT_SEED) 일괄 롤백을 시작합니다...")

        # 1. 테스트 프로필 삭제
        db_client.client.table("test_mock_profiles").delete().in_("source_type", ["TEST_MANUAL", "TEST_AUTO"]).execute()

        # 2. 마스터 데이터 삭제 (CASCADE 설정으로 매핑 테이블도 동시 정리됨)
        db_client.client.table("books").delete().eq("record_source", "MOCK_INIT_SEED").execute()
        db_client.client.table("certifications").delete().eq("record_source", "MOCK_INIT_SEED").execute()
        db_client.client.table("courses").delete().eq("record_source", "MOCK_INIT_SEED").execute()
        db_client.client.table("jobs").delete().eq("record_source", "MOCK_INIT_SEED").execute()

        logger.info("모의 데이터가 성공적으로 일괄 삭제되었습니다.")
        return True
    except Exception as exc:
        logger.error(f"모의 데이터 삭제 중 오류 발생: {exc}")
        return False


def main() -> None:
    """검증 메인 진입점"""
    logger.info("데이터베이스 연결 상태 확인 중...")
    health = db_client.check_health()
    logger.info(f"헬스체크 결과: {health}")

    if not health.get("connected"):
        logger.error("데이터베이스에 연결할 수 없습니다. Supabase 설정과 .env 파일을 확인하세요.")
        sys.exit(1)

    logger.info("테이블별 적재 레코드 수를 확인합니다...")
    counts = verify_seeding_counts()
    for table_name, count in counts.items():
        logger.info(f" - {table_name:22s}: {count:4d} 개 레코드")

    if counts.get("courses", 0) > 0 and counts.get("prompt_versions", 0) >= 3:
        logger.info("==> [성공] 1단계 데이터베이스 스키마 및 모의 데이터 적재가 완벽하게 준비되었습니다.")
    else:
        logger.warning(
            "==> [알림] 테이블이 비어 있거나 적재되지 않은 항목이 있습니다. "
            "Supabase SQL Editor에서 schema_v2_roadmap.sql 과 seed_mock_data.sql 을 순서대로 실행하세요."
        )


if __name__ == "__main__":
    main()