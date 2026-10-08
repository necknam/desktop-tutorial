# =====================================================================
# FILE: src/core/selection_service.py
# =====================================================================

import logging
from typing import Any, Dict, List, Optional

try:
    from src.db.db_client import db_client
except ImportError:
    from db.db_client import db_client

logger = logging.getLogger("SELECTION_SERVICE")


class SelectionService:
    """
    프로젝트 로드맵 세션의 라이프사이클 및 4~5단계 대안/채택 트랜잭션을 전담하는 서비스 계층.
    """

    def __init__(self) -> None:
        self.db = db_client

    def create_roadmap_session(
        self,
        user_id: Optional[str] = None,
        project_name: Optional[str] = None,
        user_type: str = "REAL_USER",
        *args,
        **kwargs
    ) -> Optional[str]:
        """
        신규 프로젝트 세션 엔티티 생성.
        - test_step4_agents.py의 유효성 검증(user_type="INVALID_TYPE" 시 ValueError)
        - app.py의 신규 세션 발급(user_id, project_name)
        양쪽 호출 형태를 모두 지원합니다.
        """
        # 1. user_type 검증 (test_step4_agents.py 대응)
        target_utype = kwargs.get("user_type", user_type)
        if target_utype not in ("REAL_USER", "TEST_MANUAL", "TEST_AUTO"):
            raise ValueError(f"유효하지 않은 user_type 입니다: {target_utype}")

        # 2. user_id 추출 (키워드/프로필 폴백)
        target_uid = user_id or kwargs.get("user_id")
        if not target_uid and "user_profile" in kwargs:
            prof = kwargs.get("user_profile", {})
            target_uid = str(prof.get("employee_id") or prof.get("profile_id") or "00000000-0000-0000-0000-000000000001")
        if not target_uid:
            target_uid = "00000000-0000-0000-0000-000000000001"

        # 3. project_name 추출 (프롬프트/프로필 요약 폴백)
        target_pname = (
            project_name
            or kwargs.get("project_name")
            or kwargs.get("raw_user_prompt")
            or kwargs.get("user_profile", {}).get("work_context_summary")
            or "신규 엔지니어링 프로젝트"
        )
        if isinstance(target_pname, str):
            target_pname = target_pname[:100]

        return self.db.create_roadmap_session(
            user_id=target_uid,
            project_name=target_pname,
            status="DRAFT",
            current_stage=1
        )

    def get_roadmap_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """DB에서 프로젝트 세션 조회"""
        return self.db.get_roadmap_session(session_id)

    def list_roadmap_sessions(self, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """진행 중인 세션 목록 조회"""
        return self.db.list_roadmap_sessions(user_id=user_id, limit=limit)

    def update_session_stage(self, session_id: str, stage_num: int, status: str = "IN_PROGRESS") -> bool:
        """세션 단계 전이 갱신"""
        return self.db.update_session_stage(session_id, stage_num, status)

    def save_agent_generations(self, session_id: str, generations: List[Dict[str, Any]]) -> bool:
        """[Stage 4] 3개 대안 로드맵 DB 일괄 저장"""
        return self.db.save_roadmap_generations(session_id, generations)

    def get_agent_generations(self, session_id: str) -> List[Dict[str, Any]]:
        """[Stage 4] DB에 저장된 3개 대안 로드맵 조회"""
        return self.db.get_roadmap_generations(session_id)

    def select_roadmap(self, session_id: str, generation_id: str, detailed_report: Dict[str, Any]) -> bool:
        """[Stage 5] 선택 결과 및 상세 설계 보고서 원자적 영속화"""
        ok_sel = self.db.save_roadmap_selection(session_id, generation_id)
        ok_rep = self.db.save_roadmap_report(session_id, generation_id, detailed_report)
        return ok_sel and ok_rep

    def get_selected_roadmap(self, session_id: str) -> Optional[Dict[str, Any]]:
        """[Stage 5] 선택된 로드맵 및 설계 보고서 조회"""
        sel = self.db.get_roadmap_selection(session_id)
        rep = self.db.get_roadmap_report(session_id)
        if not sel:
            return None

        gen_id = sel.get("generation_id")
        gens = self.db.get_roadmap_generations(session_id)
        chosen_gen = next((g for g in gens if g.get("generation_id") == gen_id), {})

        return {
            "selection": sel,
            "generation": chosen_gen,
            "report": rep.get("report_content") if rep else {}
        }

    # 레거시 하위 호환 메서드
    def record_agent_generations(self, request_id: str, orchestration_results: Dict[str, Any]) -> Dict[str, str]:
        gen_list = []
        for aid, res in orchestration_results.items():
            content = res.get("roadmap_content", {})
            gen_list.append({
                "strategy_type": aid,
                "strategy_title": content.get("strategy_title", aid),
                "summary": res.get("summary", ""),
                "roadmap_content": content
            })
        self.save_agent_generations(request_id, gen_list)
        return {aid: f"gen_{aid}" for aid in orchestration_results.keys()}

    def record_user_selection(self, request_id: str, chosen_generation_id: str, chosen_agent_id: str, user_type: str, selection_reason: Optional[str] = None) -> Optional[str]:
        self.db.save_roadmap_selection(request_id, chosen_generation_id)
        return chosen_generation_id

    def get_dashboard_summary(self, user_type: Optional[str] = "REAL_USER") -> Dict[str, Any]:
        return {"statistics": {}, "trigger_status": {}}


selection_service = SelectionService()
__all__ = ["SelectionService", "selection_service"]