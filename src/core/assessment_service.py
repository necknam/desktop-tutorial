# =====================================================================
# FILE: src/core/assessment_service.py
# =====================================================================

import logging
from typing import Any, Dict, List, Optional

try:
    from src.db.db_client import db_client
except ImportError:
    from db.db_client import db_client

logger = logging.getLogger("ASSESSMENT_SERVICE")


class AssessmentService:
    """
    1~2단계 프로젝트 계획, 대화 로그, 3/6단계 BARS 역량 진단 전담 서비스.
    """

    def __init__(self) -> None:
        self.db = db_client

    def get_project_plan(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_project_plan(session_id)

    def save_project_plan(self, session_id: str, plan_data: Dict[str, Any]) -> bool:
        return self.db.save_project_plan(session_id, plan_data)

    def get_plan_conversations(self, session_id: str) -> List[Dict[str, Any]]:
        return self.db.get_plan_conversations(session_id)

    def save_plan_conversation(self, session_id: str, role: str, content: str, sequence_no: int) -> bool:
        return self.db.save_plan_conversation(session_id, role, content, sequence_no)

    def get_assessment(self, session_id: str, assessment_type: str) -> Dict[str, Any]:
        """질문과 응답을 안전하게 조인하여 skill, score, selected_option을 일원화 반환"""
        questions = self.db.get_assessment_questions(session_id, assessment_type)
        responses = self.db.get_assessment_responses(session_id, assessment_type)

        q_dict = {q["question_id"]: q for q in questions}
        joined_responses = []

        for r in responses:
            qid = r.get("question_id")
            q_info = q_dict.get(qid, {})
            skill_name = q_info.get("skill", q_info.get("skill_name", r.get("skill", "General")))

            joined_responses.append({
                "response_id": r.get("response_id"),
                "question_id": qid,
                "skill": skill_name,
                "skill_name": skill_name,
                "question": q_info.get("question", ""),
                "options": q_info.get("options", []),
                "selected_option": r.get("selected_option", ""),
                "score": int(r.get("score", 1)),
                "assessment_type": assessment_type
            })

        return {
            "questions": questions,
            "responses": joined_responses
        }

    def save_assessment_questions(self, session_id: str, assessment_type: str, questions: List[Dict[str, Any]]) -> bool:
        return self.db.save_assessment_questions(session_id, assessment_type, questions)

    def save_assessment_responses(self, session_id: str, assessment_type: str, responses: List[Dict[str, Any]]) -> bool:
        return self.db.save_assessment_responses(session_id, assessment_type, responses)

    def get_assessment_report(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.get_assessment_report(session_id)
        return row.get("report_content") if row else None

    def save_assessment_report(self, session_id: str, report_content: Dict[str, Any]) -> bool:
        return self.db.save_assessment_report(session_id, report_content)


assessment_service = AssessmentService()
__all__ = ["AssessmentService", "assessment_service"]