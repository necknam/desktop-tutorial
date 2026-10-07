"""
4단계 에이전트 선택 로깅 및 통계 영속화 검증 스크립트
파일 경로: test_selection_logging.py
역할: 로드맵 세션 생성 -> 에이전트 결과 기록 -> 사용자 선택 로깅 -> 통계 집계 및 격리 검증
"""

import sys
import os
import logging

# 프로젝트 루트 sys.path 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.db_client import db_client
from core.selection_service import selection_service

logger = logging.getLogger("TEST_SELECTION_LOGGING")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def test_selection_logging_lifecycle() -> None:
    logger.info("=== [테스트 1] 로드맵 세션 생성 및 선택 로깅 전체 라이프사이클 검증 ===")

    # 1. 모의 사용자 프로필 로드
    profiles = db_client.fetch_mock_profiles(source_type="TEST_MANUAL")
    assert len(profiles) > 0, "모의 프로필 데이터가 필요합니다."
    test_user = profiles[0]

    # 2. 로드맵 세션 생성 (roadmap_requests)
    raw_prompt = "FastAPI 기반 MSA 전환을 위한 실무 로드맵을 제안해 주세요."
    conversation_history = [{"role": "user", "content": raw_prompt}]
    retrieved_context = {"courses": [{"course_id": "C_KMOOC_001", "title": "FastAPI 실무"}]}

    req_id = selection_service.create_roadmap_session(
        user_type="TEST_MANUAL",
        raw_user_prompt=raw_prompt,
        user_profile=test_user,
        conversation_history=conversation_history,
        retrieved_context=retrieved_context
    )

    assert req_id is not None, "roadmap_requests 레코드 삽입에 성공해야 합니다."
    logger.info(f"-> 1단계: 로드맵 요청 생성 검증 통과 (request_id: {req_id})\n")

    # 3. 3개 에이전트 생성 결과 기록 (agent_generations)
    mock_results = {
        "agent_practical": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "latency_ms": 2500,
            "status": "SUCCESS",
            "summary": "FastAPI 실무 중심 8주 프로젝트 로드맵",
            "roadmap_content": {"milestones": [{"phase": 1, "goal": "API 서버 구축"}]}
        },
        "agent_certified": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "latency_ms": 2800,
            "status": "SUCCESS",
            "summary": "정보처리기사 및 SQLD 취득 연계 로드맵",
            "roadmap_content": {"milestones": [{"phase": 1, "goal": "자격증 이론 학습"}]}
        },
        "agent_fasttrack": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "latency_ms": 2100,
            "status": "SUCCESS",
            "summary": "단기 매치업 4주 압축 로드맵",
            "roadmap_content": {"milestones": [{"phase": 1, "goal": "핵심 API 요약"}]}
        }
    }

    gid_map = selection_service.record_agent_generations(
        request_id=req_id,
        orchestration_results=mock_results
    )

    assert len(gid_map) == 3, "3개 에이전트 결과가 모두 DB에 기록되어야 합니다."
    assert "agent_practical" in gid_map
    assert "agent_certified" in gid_map
    assert "agent_fasttrack" in gid_map
    logger.info(f"-> 2단계: 3개 에이전트 생성 결과 기록 통과 (생성 ID: {gid_map})\n")

    # 4. 사용자 선택 기록 (agent_selections - 'agent_practical' 선택 모의)
    chosen_agent = "agent_practical"
    chosen_gid = gid_map[chosen_agent]

    sel_id = selection_service.record_user_selection(
        request_id=req_id,
        chosen_generation_id=chosen_gid,
        chosen_agent_id=chosen_agent,
        user_type="TEST_MANUAL",
        selection_reason="실무 프로젝트 중심의 즉각적인 코드베이스 구축이 가장 필요해서 채택함."
    )

    assert sel_id is not None, "agent_selections 레코드 삽입에 성공해야 합니다."
    logger.info(f"-> 3단계: 사용자 선택 기록 통과 (selection_id: {sel_id}, 채택 Agent: {chosen_agent})\n")


def test_statistics_and_data_isolation() -> None:
    logger.info("=== [테스트 2] 운영(REAL_USER) vs 테스트(TEST) 데이터 격리 및 통계 검증 ===")

    # 1. REAL_USER 모의 선택 1건 추가 주입
    req_id_real = selection_service.create_roadmap_session(
        user_type="REAL_USER",
        raw_user_prompt="클라우드 인프라 아키텍처 학습 로드맵",
        user_profile={"profile_name": "실운영사용자", "target_skills": ["AWS Cloud"]},
        conversation_history=[],
        retrieved_context={}
    )
    mock_results = {
        "agent_certified": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "latency_ms": 2300,
            "status": "SUCCESS",
            "summary": "AWS 공인 자격증 취득 중심 로드맵",
            "roadmap_content": {"milestones": []}
        }
    }
    gid_map = selection_service.record_agent_generations(req_id_real, mock_results)
    selection_service.record_user_selection(
        request_id=req_id_real,
        chosen_generation_id=gid_map["agent_certified"],
        chosen_agent_id="agent_certified",
        user_type="REAL_USER",
        selection_reason="AWS 자격증 취득이 시급함"
    )

    # 2. 통계 조회 (REAL_USER 필터 적용)
    real_stats = db_client.get_selection_statistics(user_type="REAL_USER")
    logger.info(f"REAL_USER 통계: {real_stats}")
    assert real_stats["user_type_filter"] == "REAL_USER"
    assert real_stats["agent_counts"]["agent_certified"] >= 1

    # 3. 통계 조회 (전체 ALL 필터 적용)
    all_stats = db_client.get_selection_statistics(user_type=None)
    logger.info(f"전체(테스트 포함) 통계: {all_stats}")
    assert all_stats["total_selections"] >= real_stats["total_selections"]

    # 4. 임계값(Threshold=10) 트리거 판정 확인
    trigger_info = db_client.check_prompt_update_trigger(threshold=10, user_type="REAL_USER")
    logger.info(f"임계값 트리거 판정: {trigger_info}")
    assert "should_trigger" in trigger_info
    assert "lowest_agent_id" in trigger_info

    logger.info("\n=== [성공] 4단계 선택 로깅 및 데이터 격리 통계 검증 통과 ===")


if __name__ == "__main__":
    test_selection_logging_lifecycle()
    test_statistics_and_data_isolation()