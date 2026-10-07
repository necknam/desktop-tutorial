"""
3단계 멀티에이전트 병렬 실행 및 DB 접지 검증 스크립트
파일 경로: test_multi_agent.py
역할: 모의 프로필 조회 -> DB 접지 검색 -> ThreadPoolExecutor 병렬 실행 -> 결과 출력 및 검증
"""

import sys
import os
import json
import logging

# 프로젝트 루트 sys.path 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.db_client import db_client
from core.recommendation_service import recommendation_service
from core.roadmap_agent import multi_agent_orchestrator

logger = logging.getLogger("TEST_MULTI_AGENT")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def test_parallel_multi_agent_generation() -> None:
    logger.info("=== [테스트] 멀티에이전트 병렬 로드맵 생성 및 접지 검증 시작 ===")

    # 1. Step 1에서 적재한 모의 프로필 로드
    profiles = db_client.fetch_mock_profiles(source_type="TEST_MANUAL")
    if not profiles:
        logger.error("테스트용 프로필이 없습니다. Step 1 시딩 상태를 확인하세요.")
        return

    test_user = profiles[0]  # 김백엔 (선임연구원, 목표: FastAPI, PostgreSQL, Docker)
    logger.info(f"테스트 대상자: {test_user['profile_name']} ({test_user['grade']})")
    logger.info(f"소속 부서 / 직무: {test_user['department']} / {test_user['job_title']}")
    logger.info(f"목표 역량: {test_user['target_skills']}\n")

    # 2. DB 접지(Grounding) 데이터 인출
    grounding_context = recommendation_service.retrieve_grounding_context(
        target_skills=test_user["target_skills"],
        target_job_title=test_user["job_title"]
    )
    logger.info(f"DB 인출 강좌 수: {len(grounding_context['courses'])} 건")
    logger.info(f"DB 인출 자격증 수: {len(grounding_context['certifications'])} 건")
    logger.info(f"DB 인출 도서 수: {len(grounding_context['books'])} 건\n")

    assert len(grounding_context["courses"]) > 0, "DB 접지 강좌가 최소 1건 이상 인출되어야 합니다."

    # 3. ThreadPoolExecutor 기반 3개 전략 에이전트 병렬 호출
    conversation_history = [
        {"role": "user", "content": "올해 안에 마이크로서비스 백엔드 엔지니어로 업무 개발을 추진하고 싶습니다."}
    ]

    orchestration_result = multi_agent_orchestrator.generate_all_roadmaps(
        user_context=test_user,
        conversation_history=conversation_history,
        retrieved_context=grounding_context
    )

    total_latency = orchestration_result["total_latency_ms"]
    results = orchestration_result["results"]

    logger.info(f"\n>>> 3개 에이전트 병렬 실행 총 소요시간: {total_latency} ms")
    assert "agent_practical" in results
    assert "agent_certified" in results
    assert "agent_fasttrack" in results

    # 4. 개별 에이전트 결과 출력 및 차별성 확인
    individual_latencies = []
    db_course_ids = {str(c["course_id"]) for c in grounding_context["courses"]}

    for agent_id, res in results.items():
        individual_latencies.append(res.get("latency_ms", 0))
        logger.info(f"\n------------------------------------------------------------")
        logger.info(f"[{agent_id}] 상태: {res['status']} | 공급자: {res['provider']} ({res['model']}) | 지연시간: {res['latency_ms']} ms")
        logger.info(f"[요약 리포트]: {res['summary']}")

        roadmap = res.get("roadmap_content", {})
        milestones = roadmap.get("milestones", [])
        logger.info(f"[마일스톤 수]: {len(milestones)} 단계")

        # 추천된 강좌가 실제 DB 컨텍스트에 존재하는지 검증 (무환각 체크)
        for ms in milestones:
            for rec_course in ms.get("recommended_courses", []):
                cid = str(rec_course.get("course_id") if isinstance(rec_course, dict) else rec_course)
                assert cid in db_course_ids, f"환각 발생: DB에 없는 강좌 ID {cid}가 생성되었습니다!"

    # 5. 동시성 효율성 검증 (병렬 총 소요시간이 3개 에이전트 지연시간의 단순 합보다 확연히 작아야 함)
    sum_latencies = sum(individual_latencies)
    logger.info(f"\n단순 순차 합산 예상 시간: {sum_latencies} ms vs 실제 병렬 실행 시간: {total_latency} ms")
    assert total_latency < sum_latencies, "병렬 실행이 정상 작동하여 순차 합산보다 빨라야 합니다."

    logger.info("\n=== [성공] 3단계 ThreadPoolExecutor 멀티에이전트 및 DB 접지 검증 완료 ===")


if __name__ == "__main__":
    test_parallel_multi_agent_generation()