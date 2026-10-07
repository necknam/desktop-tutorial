"""
에이전트 선택 및 세션 트랜잭션 관리 서비스
파일 경로: core/selection_service.py
역할: 로드맵 세션 생성, 에이전트 3종 생성 결과 일괄 기록, 사용자 최종 선택 영속화
"""

import logging
from typing import Dict, Any, List, Optional
from database.db_client import db_client

logger = logging.getLogger("SELECTION_SERVICE")


class SelectionService:
    """로드맵 요청-생성-선택 트랜잭션을 총괄하는 비즈니스 계층 서비스"""

    def __init__(self) -> None:
        self.db = db_client

    def create_roadmap_session(
        self,
        user_type: str,
        raw_user_prompt: str,
        user_profile: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        retrieved_context: Dict[str, Any]
    ) -> Optional[str]:
        """
        1. 신규 로드맵 요청 세션 생성 (roadmap_requests 테이블)
        """
        if user_type not in ("REAL_USER", "TEST_MANUAL", "TEST_AUTO"):
            raise ValueError(f"유효하지 않은 user_type 입니다: {user_type}")

        employee_id = user_profile.get("employee_id") or user_profile.get("profile_id")
        user_requirements = {
            "department": user_profile.get("department", ""),
            "grade": user_profile.get("grade", ""),
            "job_title": user_profile.get("job_title", ""),
            "career_years": user_profile.get("career_years", 0),
            "target_skills": user_profile.get("target_skills", []),
            "current_skills": user_profile.get("current_skills", [])
        }

        request_id = self.db.log_roadmap_request(
            user_type=user_type,
            raw_prompt=raw_user_prompt,
            conversation_history=conversation_history,
            user_requirements=user_requirements,
            retrieved_context=retrieved_context,
            employee_id=str(employee_id) if employee_id else None
        )

        if request_id:
            logger.info(f"로드맵 세션 생성 성공 (request_id: {request_id}, user_type: {user_type})")
        else:
            logger.error("로드맵 세션 생성 실패")

        return request_id

    def record_agent_generations(
        self,
        request_id: str,
        orchestration_results: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        2. 3개 에이전트의 생성 결과 영속화 (agent_generations 테이블)
        - 반환값: {agent_id: generation_id} 매핑 딕셔너리
        """
        generation_id_map: Dict[str, str] = {}
        active_prompts = self.db.get_active_prompts()

        for agent_id, res in orchestration_results.items():
            prompt_version_id = None
            if agent_id in active_prompts:
                prompt_version_id = active_prompts[agent_id].get("prompt_version_id")

            gid = self.db.log_agent_generation(
                request_id=request_id,
                agent_id=agent_id,
                prompt_version_id=prompt_version_id,
                provider=res.get("provider", "unknown"),
                model=res.get("model", "unknown"),
                roadmap_content=res.get("roadmap_content", {}),
                summary=res.get("summary", ""),
                latency_ms=res.get("latency_ms", 0),
                status=res.get("status", "SUCCESS")
            )

            if gid:
                generation_id_map[agent_id] = gid
                logger.info(f"[{agent_id}] 생성 결과 DB 저장 완료 (generation_id: {gid})")
            else:
                logger.warning(f"[{agent_id}] 생성 결과 DB 저장 실패")

        return generation_id_map

    def record_user_selection(
        self,
        request_id: str,
        chosen_generation_id: str,
        chosen_agent_id: str,
        user_type: str,
        selection_reason: Optional[str] = None
    ) -> Optional[str]:
        """
        3. 사용자가 선택한 로드맵 결과 영속화 (agent_selections 테이블)
        """
        selection_id = self.db.log_agent_selection(
            request_id=request_id,
            chosen_generation_id=chosen_generation_id,
            chosen_agent_id=chosen_agent_id,
            user_type=user_type,
            selection_reason=selection_reason
        )

        if selection_id:
            logger.info(
                f"[선택 기록 완료] request: {request_id} -> "
                f"선택 Agent: {chosen_agent_id} (selection_id: {selection_id})"
            )
        else:
            logger.error(f"[선택 기록 실패] request: {request_id}")

        return selection_id

    def get_dashboard_summary(self, user_type: Optional[str] = "REAL_USER") -> Dict[str, Any]:
        """
        에이전트 선택률 현황 대시보드 데이터 인출
        """
        stats = self.db.get_selection_statistics(user_type=user_type)
        trigger_check = self.db.check_prompt_update_trigger(threshold=10, user_type=user_type or "REAL_USER")

        return {
            "statistics": stats,
            "trigger_status": trigger_check
        }


# 편의용 글로벌 싱글톤 인스턴스
selection_service = SelectionService()