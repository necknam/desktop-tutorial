"""
5단계 프롬프트 자가진화(Optimizer), 8대 가드레일 평가 및 롤백 검증 스크립트
파일 경로: test_prompt_optimizer.py
역할: 최저 선택률 에이전트 감지 -> Reflection -> LLM-as-a-Judge 평가 -> 신규 버전(v2) 승격 -> 원클릭 롤백(v1 복원)
"""

import sys
import os
import logging

# 프로젝트 루트 sys.path 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.prompt_manager import prompt_manager
from core.self_evolving_optimizer import self_evolving_optimizer
from database.db_client import db_client

logger = logging.getLogger("TEST_PROMPT_OPTIMIZER")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def test_self_evolving_lifecycle_and_rollback() -> None:
    logger.info("=== [테스트 1] 프롬프트 자가진화 라이프사이클 (Reflection -> Judge -> 승격) 검증 ===")

    # 1. 대상 에이전트 초기 상태 확인 (현재 v1 확인)
    target_agent = "agent_fasttrack"
    initial_prompt = prompt_manager.get_active_prompt(target_agent)
    assert initial_prompt is not None, f"{target_agent}의 초기 활성 프롬프트가 존재해야 합니다."
    initial_version = initial_prompt["version_num"]
    initial_vid = initial_prompt["prompt_version_id"]
    logger.info(f"초기 상태: {target_agent} 활성 버전 = v{initial_version} (ID: {initial_vid})\n")

    # 2. 자가진화 엔진 강제 구동 (force_run=True)
    logger.info("자가진화 엔진 구동 시작 (Reflection 취약점 분석 및 후보 프롬프트 생성)...")
    opt_result = self_evolving_optimizer.evolve_target_agent(
        target_agent_id=target_agent,
        selection_rate=0.0,
        cumulative_selections=10,
        threshold=10
    )

    logger.info(f"자가진화 실행 상태: {opt_result['status']}")
    logger.info(f"LLM-as-a-Judge 총점: {opt_result['total_score']} / 10.0 (합격 여부: {opt_result['passed']})")
    logger.info(f"8대 루브릭 채점표: {opt_result['evaluation_scores']}")
    logger.info(f"Reflection 분석 요약:\n{opt_result['reflection_report']}\n")
    logger.info(f"생성된 후보 프롬프트:\n{opt_result['candidate_prompt']}\n")

    assert opt_result["passed"] is True, "8대 가드레일 심사를 통과해야 합니다."
    assert opt_result["status"] == "EVAL_PASSED"

    # 3. 신규 버전(v2) 정상 승격 확인
    updated_prompt = prompt_manager.get_active_prompt(target_agent)
    assert updated_prompt is not None
    assert updated_prompt["version_num"] > initial_version, "버전 번호가 증가해야 합니다."
    assert updated_prompt["is_active"] is True
    assert updated_prompt["parent_version_id"] == initial_vid, "부모 버전 ID가 기존 v1으로 연결되어야 합니다."
    logger.info(f"-> [승격 확인 완료] {target_agent} 새 활성 버전 = v{updated_prompt['version_num']} (부모 ID: {updated_prompt['parent_version_id']})\n")

    # 4. 프롬프트 롤백(Rollback) 기능 검증 (v2 -> v1 복원)
    logger.info("=== [테스트 2] 무중단 프롬프트 원클릭 롤백 검증 (v2 -> v1 복구) ===")
    rollback_res = prompt_manager.rollback(target_agent)
    logger.info(f"롤백 결과: {rollback_res}")
    assert rollback_res["success"] is True

    # 5. 롤백 후 활성 버전 재확인 (v1으로 완벽 복원 확인)
    restored_prompt = prompt_manager.get_active_prompt(target_agent)
    assert restored_prompt is not None
    assert restored_prompt["version_num"] == initial_version, "초기 버전(v1)으로 복구되어야 합니다."
    assert restored_prompt["is_active"] is True
    logger.info(f"-> [롤백 확인 완료] {target_agent} 현재 활성 버전 = v{restored_prompt['version_num']} (성공적으로 복원됨)\n")

    # 6. DB 이력 테이블 영속화 확인
    history = prompt_manager.get_history(target_agent)
    logger.info(f"{target_agent}의 전체 버전 이력 수: {len(history)}건")
    assert len(history) >= 2, "v1과 승격되었던 v2 레코드가 모두 영구 보존되어 있어야 합니다."

    logger.info("=== [성공] 5단계 프롬프트 자가진화, 8대 가드레일 심사 및 롤백 엔진 검증 완료 ===")


if __name__ == "__main__":
    test_self_evolving_lifecycle_and_rollback()