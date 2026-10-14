# =====================================================================
# FILE: src/core/assessment_service.py
# =====================================================================

import logging
from typing import Any, Dict, List, Optional

from src.db.db_client import db_client, DatabaseClient
from src.common.assessment_schema import validate_questions, validate_responses, summarize_assessment

logger = logging.getLogger("ASSESSMENT_SERVICE")


class AssessmentService:
    """
    1~2단계 프로젝트 계획, 대화 로그, 3/6단계 5점 자기보고 진단 전담 서비스.
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
                "skill_id": q_info.get("skill_id"),
                "skill": skill_name,
                "skill_name": skill_name,
                "question": q_info.get("question", ""),
                "options": q_info.get("options", []),
                "selected_option": r.get("selected_option", ""),
                "score": r.get("score"),
                "assessment_type": assessment_type
            })

        return {
            "questions": questions,
            "responses": joined_responses
        }

    def save_assessment_questions(self, session_id: str, assessment_type: str, questions: List[Dict[str, Any]]) -> bool:
        validate_questions(questions, require_ids=False)
        catalog = {s["skill_id"]: s["name"] for s in self.db.list_skill_tags()}
        if any(catalog.get(q["skill_id"]) != q["skill"] for q in questions):
            raise ValueError("DB 스킬 ID와 이름이 일치하지 않습니다.")
        expected = {s["skill_id"] for s in self.project_skills(session_id)}
        if {q["skill_id"] for q in questions} != expected:
            raise ValueError("설문 스킬이 프로젝트의 DB 스킬 목록과 일치하지 않습니다.")
        if assessment_type == "POST":
            pre = self.db.get_assessment_questions(session_id, "PRE")
            validate_questions(pre)
            keys = ("sequence_no", "skill_id", "question", "options")
            if [[q[k] for k in keys] for q in pre] != [[q[k] for k in keys] for q in questions]:
                raise ValueError("사후 설문은 사전 설문과 동일한 문항이어야 합니다.")
        return self.db.save_assessment_questions(session_id, assessment_type, questions)

    def save_assessment_responses(self, session_id: str, assessment_type: str, responses: List[Dict[str, Any]]) -> bool:
        questions = self.db.get_assessment_questions(session_id, assessment_type)
        validate_responses(questions, responses)
        return self.db.save_assessment_responses(session_id, assessment_type, responses)

    def project_skills(self, session_id):
        plan = self.get_project_plan(session_id)
        names = plan.get("tech_stack") if plan else None
        return self.resolve_skill_names(names)

    def resolve_skill_names(self, names):
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise ValueError("저장된 프로젝트의 DB 스킬 목록이 없습니다.")
        catalog = {s["name"]: s for s in self.db.list_skill_tags()}
        unknown = [name for name in names if name not in catalog]
        if unknown:
            raise ValueError("DB에 없는 프로젝트 스킬입니다: " + ", ".join(unknown))
        return [catalog[name] for name in dict.fromkeys(names)]

    def save_pre_assessment_with_skills(self, session_id, skill_names, questions):
        """Repair an unanswered legacy assessment after an explicit DB tag selection."""
        skills = self.resolve_skill_names(skill_names)
        validate_questions(questions, require_ids=False)
        if {q["skill_id"] for q in questions} != {s["skill_id"] for s in skills}:
            raise ValueError("선택한 DB 스킬과 설문 문항이 일치하지 않습니다.")
        with self.db.transaction() as conn:
            bound = DatabaseClient(conn)
            # This checks stage, canonical names, and absence of responses under a session lock.
            bound.save_assessment_questions(session_id, "PRE", questions)
            bound.save_project_plan(session_id, {"tech_stack": [s["name"] for s in skills]})
        return True

    def get_assessment_summary(self, session_id, assessment_type="PRE"):
        data = self.get_assessment(session_id, assessment_type)
        result = summarize_assessment(data["questions"], data["responses"])
        result.update(session_id=session_id, assessment_type=assessment_type)
        return result

    def get_assessment_report(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.get_assessment_report(session_id)
        return row.get("report_content") if row else None

    def save_assessment_report(self, session_id: str, report_content: Dict[str, Any]) -> bool:
        return self.db.save_assessment_report(session_id, report_content)


assessment_service = AssessmentService()
__all__ = ["AssessmentService", "assessment_service"]