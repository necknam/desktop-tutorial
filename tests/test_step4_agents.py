import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.assessment_agent import assessment_agent
from src.core.roadmap_agent import multi_agent_orchestrator
from src.core.selection_service import selection_service


def test_assessment_trigger_detection():
    """역량 진단 에이전트의 로드맵 생성 트리거 감지 로직 검증"""
    assert assessment_agent.is_generation_trigger("계획 생성해줘") is True, "트리거 키워드 감지 실패"
    assert assessment_agent.is_generation_trigger("3안 제안 부탁합니다") is True, "트리거 키워드 감지 실패"
    assert assessment_agent.is_generation_trigger("현재 아키텍처 고민이 있습니다") is False, "비트리거 문장 오감지"
    print("[PASS] 4-1. 역량 진단 에이전트 트리거 감지 정상")


def test_orchestrator_initialization():
    """3대 전략 에이전트 오케스트레이터의 에이전트 목록 확인"""
    agent_ids = [agent.agent_id for agent in multi_agent_orchestrator.agents]
    expected = ["agent_practical", "agent_certified", "agent_fasttrack"]
    assert set(agent_ids) == set(expected), f"오케스트레이터 에이전트 구성 불일치: {agent_ids}"
    print("[PASS] 4-2. 3대 전략 에이전트(실무/자격/단기) 정상 등록")


def test_selection_service_validation():
    """사용자 선택 서비스의 유효하지 않은 user_type 방어 검증"""
    try:
        selection_service.create_roadmap_session(
            user_type="INVALID_TYPE",
            raw_user_prompt="test",
            user_profile={},
            conversation_history=[],
            retrieved_context={}
        )
        assert False, "잘못된 user_type 전달 시 ValueError가 발생해야 합니다."
    except ValueError:
        print("[PASS] 4-3. 세션 생성 시 user_type 유효성 검증 예외 정상 처리")


if __name__ == "__main__":
    print("=== [Step 4] 에이전트 및 서비스 파이프라인 검증 시작 ===")
    test_assessment_trigger_detection()
    test_orchestrator_initialization()
    test_selection_service_validation()
    print("=== [Step 4] 검증 완료 ===\n")