# =====================================================================
# FILE: src/db/db_client.py
# =====================================================================

import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("DB_CLIENT")

try:
    from supabase import create_client, Client
except ImportError:
    Client = None
    create_client = None


class DatabaseClient:
    """
    Supabase 및 인메모리 폴백을 지원하는 데이터베이스 영속 계층 클라이언트.
    """

    def __init__(self) -> None:
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip()
        self.supabase_key = os.getenv("SUPABASE_KEY", "").strip()
        self.client: Optional[Any] = None

        self._mem: Dict[str, Any] = {
            "users": {},
            "roadmap_sessions": {},
            "project_plans": {},
            "plan_conversations": {},
            "assessment_questions": {},
            "assessment_responses": {},
            "roadmap_generations": {},
            "roadmap_selections": {},
            "roadmap_reports": {},
            "assessment_reports": {},
            "prompt_versions": {}
        }

        default_uid = "00000000-0000-0000-0000-000000000001"
        self._mem["users"][default_uid] = {
            "user_id": default_uid,
            "username": "김백엔",
            "email": "backend.kim@company.com",
            "department": "플랫폼개발1팀",
            "job_title": "백엔드 엔지니어",
            "grade": "선임연구원"
        }

        if self.supabase_url and self.supabase_key and create_client:
            try:
                self.client = create_client(self.supabase_url, self.supabase_key)
                logger.info("Supabase DB 연결 성공")
            except Exception as exc:
                logger.warning(f"Supabase 연결 실패 (인메모리 폴백 활성화): {exc}")
                self.client = None
        else:
            logger.info("Supabase 연결 미설정: 로컬 인메모리 모드로 동작합니다.")

    def check_health(self) -> Dict[str, Any]:
        if not self.client:
            return {"connected": False, "message": "로컬 인메모리 모드로 구동 중입니다."}
        try:
            res = self.client.table("users").select("user_id").limit(1).execute()
            return {"connected": True, "message": "DB 정상 연결됨"}
        except Exception as exc:
            return {"connected": False, "message": str(exc)}

    def fetch_mock_profiles(self) -> List[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("test_mock_profiles").select("*").execute()
                if res.data:
                    return res.data
            except Exception:
                pass

        return [
            {
                "profile_id": 1,
                "profile_name": "김백엔",
                "department": "플랫폼개발1팀",
                "grade": "선임연구원",
                "job_title": "백엔드 엔지니어",
                "career_years": 3,
                "current_skills": ["Python", "Git/GitHub", "Docker"],
                "target_skills": ["FastAPI", "PostgreSQL", "Docker", "Redis"],
                "work_context_summary": "레거시 API 마이크로서비스 전환 프로젝트"
            }
        ]

    # 0. users
    def get_or_create_user(
        self,
        username: str,
        email: str,
        department: str = "",
        job_title: str = "",
        grade: str = ""
    ) -> str:
        if self.client:
            try:
                res = self.client.table("users").select("user_id").eq("email", email).execute()
                if res.data and len(res.data) > 0:
                    return str(res.data[0]["user_id"])

                new_uid = str(uuid.uuid4())
                payload = {
                    "user_id": new_uid,
                    "username": username,
                    "email": email,
                    "department": department,
                    "job_title": job_title,
                    "grade": grade
                }
                ins = self.client.table("users").insert(payload).execute()
                if ins.data:
                    return str(ins.data[0]["user_id"])
            except Exception as exc:
                logger.warning(f"Supabase users 테이블 조회/생성 실패: {exc}")

        for uid, u in self._mem["users"].items():
            if u.get("email") == email:
                return uid

        new_uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, email if email else "default@company.com"))
        self._mem["users"][new_uid] = {
            "user_id": new_uid,
            "username": username,
            "email": email,
            "department": department,
            "job_title": job_title,
            "grade": grade
        }
        return new_uid

    # 1. roadmap_sessions
    def create_roadmap_session(
        self,
        user_id: Optional[Any] = None,
        project_name: Optional[str] = None,
        status: str = "DRAFT",
        current_stage: int = 1,
        *args,
        **kwargs
    ) -> Optional[str]:
        if isinstance(user_id, dict):
            prof = user_id
            target_uid = str(prof.get("employee_id") or "00000000-0000-0000-0000-000000000001")
            target_pname = project_name or prof.get("work_context_summary", "신규 프로젝트")[:100]
        else:
            target_uid = str(user_id or kwargs.get("user_id") or "00000000-0000-0000-0000-000000000001")
            target_pname = str(project_name or kwargs.get("project_name") or "신규 프로젝트")[:100]

        session_id = str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "user_id": target_uid,
            "project_name": target_pname,
            "status": status,
            "current_stage": current_stage,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("roadmap_sessions").insert(payload).execute()
                if res.data:
                    return str(res.data[0]["session_id"])
            except Exception as exc:
                logger.warning(f"Supabase roadmap_sessions 생성 실패: {exc}")

        self._mem["roadmap_sessions"][session_id] = payload
        return session_id

    def get_roadmap_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("roadmap_sessions").select("*").eq("session_id", session_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as exc:
                logger.warning(f"Supabase roadmap_sessions 조회 실패: {exc}")

        return self._mem["roadmap_sessions"].get(session_id)

    def list_roadmap_sessions(self, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        if self.client:
            try:
                q = self.client.table("roadmap_sessions").select("*").order("created_at", desc=True).limit(limit)
                if user_id:
                    q = q.eq("user_id", user_id)
                res = q.execute()
                if res.data:
                    return res.data
            except Exception as exc:
                logger.warning(f"Supabase 세션 목록 조회 실패: {exc}")

        items = list(self._mem["roadmap_sessions"].values())
        if user_id:
            items = [it for it in items if it.get("user_id") == user_id]
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return items[:limit]

    def update_session_stage(self, session_id: str, stage_num: int, status: str = "IN_PROGRESS") -> bool:
        upd = {
            "current_stage": stage_num,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if self.client:
            try:
                res = self.client.table("roadmap_sessions").update(upd).eq("session_id", session_id).execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 단계 갱신 실패: {exc}")

        if session_id in self._mem["roadmap_sessions"]:
            self._mem["roadmap_sessions"][session_id].update(upd)
            return True
        return False

    # 2. project_plans
    def save_project_plan(self, session_id: str, plan_data: Any, **kwargs) -> bool:
        if isinstance(plan_data, dict):
            p_dict = plan_data
        else:
            p_dict = {
                "project_plan_name": str(plan_data),
                "initial_prompt": kwargs.get("initial_prompt", ""),
                "draft_plan": kwargs.get("draft_plan", ""),
                "refined_plan": kwargs.get("refined_plan"),
                "uploaded_doc_text": kwargs.get("doc_text")
            }

        payload = {
            "session_id": session_id,
            "draft_plan": p_dict.get("draft_plan"),
            "refined_plan": p_dict.get("refined_plan"),
            "project_goal": p_dict.get("project_goal"),
            "project_scope": p_dict.get("project_scope"),
            "tech_stack": p_dict.get("tech_stack", []),
            "milestones": p_dict.get("milestones", []),
            "key_issues": p_dict.get("key_issues", []),
            "deliverables": p_dict.get("deliverables", []),
            "validation_criteria": p_dict.get("validation_criteria", []),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("project_plans").upsert(payload, on_conflict="session_id").execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase project_plans 저장 실패: {exc}")

        if session_id not in self._mem["project_plans"]:
            payload["plan_id"] = str(uuid.uuid4())
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        else:
            payload["plan_id"] = self._mem["project_plans"][session_id].get("plan_id", str(uuid.uuid4()))
        self._mem["project_plans"][session_id] = payload
        return True

    def get_project_plan(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("project_plans").select("*").eq("session_id", session_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as exc:
                logger.warning(f"Supabase project_plans 조회 실패: {exc}")

        return self._mem["project_plans"].get(session_id)

    # 3. plan_conversations
    def save_plan_conversation(self, session_id: str, role: str, content: str, sequence_no: int) -> bool:
        conv_id = str(uuid.uuid4())
        payload = {
            "conversation_id": conv_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "sequence_no": sequence_no,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("plan_conversations").insert(payload).execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 대화 로그 저장 실패: {exc}")

        if session_id not in self._mem["plan_conversations"]:
            self._mem["plan_conversations"][session_id] = []
        self._mem["plan_conversations"][session_id].append(payload)
        return True

    def get_plan_conversations(self, session_id: str) -> List[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("plan_conversations").select("*").eq("session_id", session_id).order("sequence_no").execute()
                if res.data is not None:
                    return res.data
            except Exception as exc:
                logger.warning(f"Supabase 대화 로그 조회 실패: {exc}")

        convs = self._mem["plan_conversations"].get(session_id, [])
        return sorted(convs, key=lambda x: x.get("sequence_no", 0))

    # 4. assessment_questions
    def save_assessment_questions(self, session_id: str, assessment_type: str, questions: List[Dict[str, Any]]) -> bool:
        rows = []
        for idx, q in enumerate(questions, 1):
            rows.append({
                "question_id": str(uuid.uuid4()),
                "session_id": session_id,
                "assessment_type": assessment_type,
                "skill": q.get("skill", q.get("skill_name", "General")),
                "question": q.get("question", ""),
                "options": q.get("options", []),
                "sequence_no": q.get("sequence_no", idx),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

        if self.client:
            try:
                self.client.table("assessment_questions").delete().eq("session_id", session_id).eq("assessment_type", assessment_type).execute()
                res = self.client.table("assessment_questions").insert(rows).execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 질문 저장 실패: {exc}")

        key = f"{session_id}_{assessment_type}"
        self._mem["assessment_questions"][key] = rows
        return True

    def get_assessment_questions(self, session_id: str, assessment_type: str) -> List[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("assessment_questions").select("*").eq("session_id", session_id).eq("assessment_type", assessment_type).order("sequence_no").execute()
                if res.data is not None:
                    return res.data
            except Exception as exc:
                logger.warning(f"Supabase 질문 조회 실패: {exc}")

        key = f"{session_id}_{assessment_type}"
        return self._mem["assessment_questions"].get(key, [])

    # 5. assessment_responses
    def save_assessment_responses(self, session_id: str, assessment_type: str, responses: List[Dict[str, Any]]) -> bool:
        rows = []
        for r in responses:
            rows.append({
                "response_id": str(uuid.uuid4()),
                "session_id": session_id,
                "question_id": r["question_id"],
                "assessment_type": assessment_type,
                "selected_option": str(r.get("selected_option", "")),
                "score": int(r.get("score", 1)),
                "answered_at": datetime.now(timezone.utc).isoformat()
            })

        if self.client:
            try:
                self.client.table("assessment_responses").delete().eq("session_id", session_id).eq("assessment_type", assessment_type).execute()
                res = self.client.table("assessment_responses").insert(rows).execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 응답 저장 실패: {exc}")

        key = f"{session_id}_{assessment_type}"
        self._mem["assessment_responses"][key] = rows
        return True

    def get_assessment_responses(self, session_id: str, assessment_type: str) -> List[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("assessment_responses").select("*").eq("session_id", session_id).eq("assessment_type", assessment_type).execute()
                if res.data is not None:
                    return res.data
            except Exception as exc:
                logger.warning(f"Supabase 응답 조회 실패: {exc}")

        key = f"{session_id}_{assessment_type}"
        return self._mem["assessment_responses"].get(key, [])

    # 6. roadmap_generations
    def save_roadmap_generations(self, session_id: str, generations: List[Dict[str, Any]]) -> bool:
        rows = []
        for idx, g in enumerate(generations, 1):
            rows.append({
                "generation_id": str(uuid.uuid4()),
                "session_id": session_id,
                "strategy_type": g.get("strategy_type", "practical"),
                "strategy_title": g.get("strategy_title", ""),
                "summary": g.get("summary", ""),
                "roadmap_content": g.get("roadmap_content", {}),
                "generation_order": idx,
                "created_at": datetime.now(timezone.utc).isoformat()
            })

        if self.client:
            try:
                self.client.table("roadmap_generations").delete().eq("session_id", session_id).execute()
                res = self.client.table("roadmap_generations").insert(rows).execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 3대 대안 저장 실패: {exc}")

        self._mem["roadmap_generations"][session_id] = rows
        return True

    def get_roadmap_generations(self, session_id: str) -> List[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("roadmap_generations").select("*").eq("session_id", session_id).order("generation_order").execute()
                if res.data is not None:
                    return res.data
            except Exception as exc:
                logger.warning(f"Supabase 3대 대안 조회 실패: {exc}")

        return self._mem["roadmap_generations"].get(session_id, [])

    # 7. roadmap_selections & 8. roadmap_reports
    def save_roadmap_selection(self, session_id: str, generation_id: str) -> bool:
        payload = {
            "session_id": session_id,
            "generation_id": generation_id,
            "selected_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("roadmap_selections").upsert(payload, on_conflict="session_id").execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 채택 저장 실패: {exc}")

        if session_id not in self._mem["roadmap_selections"]:
            payload["selection_id"] = str(uuid.uuid4())
        else:
            payload["selection_id"] = self._mem["roadmap_selections"][session_id].get("selection_id", str(uuid.uuid4()))
        self._mem["roadmap_selections"][session_id] = payload
        return True

    def get_roadmap_selection(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("roadmap_selections").select("*").eq("session_id", session_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as exc:
                logger.warning(f"Supabase 채택 조회 실패: {exc}")

        return self._mem["roadmap_selections"].get(session_id)

    def save_roadmap_report(self, session_id: str, generation_id: str, report_content: Dict[str, Any]) -> bool:
        payload = {
            "session_id": session_id,
            "generation_id": generation_id,
            "report_content": report_content,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("roadmap_reports").upsert(payload, on_conflict="session_id").execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 설계보고서 저장 실패: {exc}")

        if session_id not in self._mem["roadmap_reports"]:
            payload["report_id"] = str(uuid.uuid4())
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        else:
            payload["report_id"] = self._mem["roadmap_reports"][session_id].get("report_id", str(uuid.uuid4()))
        self._mem["roadmap_reports"][session_id] = payload
        return True

    def get_roadmap_report(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("roadmap_reports").select("*").eq("session_id", session_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as exc:
                logger.warning(f"Supabase 설계보고서 조회 실패: {exc}")

        return self._mem["roadmap_reports"].get(session_id)

    # 9. assessment_reports
    def save_assessment_report(self, session_id: str, report_content: Dict[str, Any], report_type: str = "PRE_POST") -> bool:
        payload = {
            "session_id": session_id,
            "report_type": report_type,
            "report_content": report_content,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        if self.client:
            try:
                res = self.client.table("assessment_reports").upsert(payload, on_conflict="session_id").execute()
                if res.data:
                    return True
            except Exception as exc:
                logger.warning(f"Supabase 종합보고서 저장 실패: {exc}")

        if session_id not in self._mem["assessment_reports"]:
            payload["report_id"] = str(uuid.uuid4())
        else:
            payload["report_id"] = self._mem["assessment_reports"][session_id].get("report_id", str(uuid.uuid4()))
        self._mem["assessment_reports"][session_id] = payload
        return True

    def get_assessment_report(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                res = self.client.table("assessment_reports").select("*").eq("session_id", session_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as exc:
                logger.warning(f"Supabase 종합보고서 조회 실패: {exc}")

        return self._mem["assessment_reports"].get(session_id)

    # =================================================================
    # MLOps 및 프롬프트 버전 관리 지원 메서드 (오류 해결 핵심)
    # =================================================================

    def get_active_prompts(self) -> Dict[str, Dict[str, Any]]:
        """활성 프롬프트 맵 조회"""
        if not self.client:
            return {}
        try:
            res = self.client.table("prompt_versions").select("*").eq("is_active", True).execute()
            return {row["agent_id"]: row for row in (res.data or [])}
        except Exception as exc:
            logger.warning(f"활성 프롬프트 조회 실패: {exc}")
            return {}

    def get_active_prompt_for_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """단일 에이전트 활성 프롬프트 조회"""
        if not self.client:
            return None
        try:
            res = self.client.table("prompt_versions").select("*").eq("agent_id", agent_id).eq("is_active", True).execute()
            return res.data[0] if res.data else None
        except Exception as exc:
            logger.warning(f"[{agent_id}] 활성 프롬프트 조회 실패: {exc}")
            return None

    def get_prompt_history(self, agent_id: str) -> List[Dict[str, Any]]:
        if not self.client:
            return []
        try:
            res = self.client.table("prompt_versions").select("*").eq("agent_id", agent_id).order("version_num", desc=True).execute()
            return res.data or []
        except Exception:
            return []

    def create_prompt_version(self, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.client:
            return None
        try:
            res = self.client.table("prompt_versions").insert(kwargs).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    def activate_prompt_version(self, prompt_version_id: str, agent_id: str) -> bool:
        if not self.client:
            return False
        try:
            self.client.table("prompt_versions").update({"is_active": False}).eq("agent_id", agent_id).execute()
            res = self.client.table("prompt_versions").update({"is_active": True}).eq("prompt_version_id", prompt_version_id).execute()
            return bool(res.data)
        except Exception:
            return False

    def rollback_prompt_version(self, agent_id: str) -> Dict[str, Any]:
        curr = self.get_active_prompt_for_agent(agent_id)
        if not curr or not curr.get("parent_version_id"):
            return {"success": False, "message": "부모 버전 부재"}
        pid = curr["parent_version_id"]
        ok = self.activate_prompt_version(pid, agent_id)
        return {"success": ok, "restored_parent_id": pid}

    def log_prompt_evaluation(self, **kwargs) -> Optional[str]:
        if not self.client:
            return None
        try:
            res = self.client.table("prompt_evaluations").insert(kwargs).execute()
            return res.data[0]["evaluation_id"] if res.data else None
        except Exception:
            return None

    def log_prompt_optimization(self, **kwargs) -> bool:
        if not self.client:
            return False
        try:
            res = self.client.table("prompt_optimizations").insert(kwargs).execute()
            return bool(res.data)
        except Exception:
            return False

    def get_selection_statistics(self, user_type: Optional[str] = "REAL_USER") -> Dict[str, Any]:
        return {"total_selections": 0, "counts": {}, "rates": {}}

    def check_prompt_update_trigger(self, threshold: int = 10, user_type: str = "REAL_USER") -> Dict[str, Any]:
        return {"should_trigger": False, "current_real_selections": 0, "lowest_agent_id": "agent_fasttrack", "selection_rates": {}}


db_client = DatabaseClient()
__all__ = ["DatabaseClient", "db_client"]