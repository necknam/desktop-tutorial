"""
데이터베이스 연동 클라이언트 모듈
파일 경로: database/db_client.py
역할: Supabase 연결 관리, DB 접지 검색, 로깅, 프롬프트 버전 관리/평가/롤백 영속화
"""

import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

logger = logging.getLogger("HR_ROADMAP_DB")


class DatabaseClient:
    """Supabase 기반 데이터베이스 싱글톤 클라이언트"""
    _instance: Optional["DatabaseClient"] = None

    def __new__(cls) -> "DatabaseClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self.supabase_url: Optional[str] = os.getenv("SUPABASE_URL")
        self.supabase_key: Optional[str] = (
            os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
        )
        self.client = None

        if self.supabase_url and self.supabase_key:
            try:
                from supabase import create_client, Client
                self.client: Optional[Client] = create_client(self.supabase_url, self.supabase_key)
                logger.info("Supabase 클라이언트가 정상적으로 초기화되었습니다.")
            except ImportError:
                logger.warning("supabase-py 패키지가 설치되지 않았습니다. pip install supabase 를 실행하세요.")
            except Exception as exc:
                logger.error(f"Supabase 클라이언트 초기화 중 예외 발생: {exc}")
        else:
            logger.warning("환경변수에 SUPABASE_URL 또는 SUPABASE_KEY가 설정되어 있지 않습니다.")

        self._initialized = True

    def check_health(self) -> Dict[str, Any]:
        """데이터베이스 연결 상태 확인 및 헬스체크"""
        if self.client is None:
            return {
                "status": "UNHEALTHY",
                "message": "Supabase 클라이언트가 구성되지 않았습니다. 환경변수를 확인하세요.",
                "connected": False
            }
        try:
            resp = self.client.table("prompt_versions").select("agent_id, version_num, is_active").limit(3).execute()
            return {
                "status": "HEALTHY",
                "message": "데이터베이스 연결이 정상입니다.",
                "connected": True,
                "active_prompts_sample_count": len(resp.data) if resp.data else 0
            }
        except Exception as exc:
            logger.error(f"데이터베이스 헬스체크 실패: {exc}")
            return {
                "status": "UNHEALTHY",
                "message": f"연결 쿼리 실행 실패: {str(exc)}",
                "connected": False
            }

    def get_active_prompts(self) -> Dict[str, Dict[str, Any]]:
        """3개 에이전트의 현재 활성(Active) 프롬프트 로드"""
        if self.client is None:
            logger.warning("DB 비연결 상태: 기본 내장 프롬프트를 폴백으로 사용합니다.")
            return {}
        try:
            resp = self.client.table("prompt_versions").select("*").eq("is_active", True).execute()
            prompts_map = {}
            if resp.data:
                for row in resp.data:
                    prompts_map[row["agent_id"]] = row
            return prompts_map
        except Exception as exc:
            logger.error(f"활성 프롬프트 조회 중 오류: {exc}")
            return {}

    def fetch_mock_profiles(self, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """테스트용 모의 인사 프로필 목록 조회"""
        if self.client is None:
            return []
        try:
            query = self.client.table("test_mock_profiles").select("*")
            if source_type:
                query = query.eq("source_type", source_type)
            resp = query.order("career_years", desc=False).execute()
            return resp.data or []
        except Exception as exc:
            logger.error(f"모의 프로필 조회 실패: {exc}")
            return []

    def get_recommended_entities_by_skills(self, skill_names: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """사용자 요구 스킬명 기반 DB 실존 엔티티 다단계 인출"""
        result = {"courses": [], "certifications": [], "books": []}
        if self.client is None:
            return result

        try:
            skill_ids: List[int] = []
            if skill_names:
                skill_resp = self.client.table("skill_tags").select("skill_id, name").in_("name", skill_names).execute()
                skill_ids = [row["skill_id"] for row in (skill_resp.data or [])]

                if not skill_ids:
                    for sname in skill_names:
                        s_clean = sname.strip()
                        if not s_clean:
                            continue
                        ilike_resp = self.client.table("skill_tags").select("skill_id, name").ilike("name", f"%{s_clean}%").execute()
                        for row in (ilike_resp.data or []):
                            if row["skill_id"] not in skill_ids:
                                skill_ids.append(row["skill_id"])

            if skill_ids:
                cm_resp = self.client.table("course_skill_map").select("course_id").in_("skill_id", skill_ids).execute()
                c_ids = list(set(r["course_id"] for r in (cm_resp.data or [])))
                if c_ids:
                    c_data = self.client.table("courses").select("*").in_("course_id", c_ids).execute()
                    result["courses"] = c_data.data or []

                zm_resp = self.client.table("cert_skill_map").select("cert_id").in_("skill_id", skill_ids).execute()
                z_ids = list(set(r["cert_id"] for r in (zm_resp.data or [])))
                if z_ids:
                    z_data = self.client.table("certifications").select("*").in_("cert_id", z_ids).execute()
                    result["certifications"] = z_data.data or []

                bm_resp = self.client.table("book_skill_map").select("isbn").in_("skill_id", skill_ids).execute()
                b_ids = list(set(r["isbn"] for r in (bm_resp.data or [])))
                if b_ids:
                    b_data = self.client.table("books").select("*").in_("isbn", b_ids).execute()
                    result["books"] = b_data.data or []

            if not result["courses"] and skill_names:
                for sname in skill_names:
                    s_clean = sname.strip()
                    if not s_clean:
                        continue
                    direct_c = self.client.table("courses").select("*").ilike("title", f"%{s_clean}%").limit(3).execute()
                    for item in (direct_c.data or []):
                        if not any(c["course_id"] == item["course_id"] for c in result["courses"]):
                            result["courses"].append(item)

            if not result["courses"]:
                fallback_c = self.client.table("courses").select("*").limit(3).execute()
                result["courses"] = fallback_c.data or []

            if not result["certifications"]:
                fallback_z = self.client.table("certifications").select("*").limit(2).execute()
                result["certifications"] = fallback_z.data or []

            if not result["books"]:
                fallback_b = self.client.table("books").select("*").limit(2).execute()
                result["books"] = fallback_b.data or []

            return result
        except Exception as exc:
            logger.error(f"스킬 기반 권고 엔티티 조회 실패: {exc}")
            return result

    def log_roadmap_request(
        self,
        user_type: str,
        raw_prompt: str,
        conversation_history: List[Dict[str, Any]],
        user_requirements: Dict[str, Any],
        retrieved_context: Dict[str, Any],
        employee_id: Optional[str] = None
    ) -> Optional[str]:
        """로드맵 요청 기록 (roadmap_requests)"""
        if self.client is None:
            return None
        try:
            payload = {
                "user_type": user_type,
                "raw_user_prompt": raw_prompt,
                "conversation_history": conversation_history,
                "user_requirements": user_requirements,
                "retrieved_context": retrieved_context,
                "employee_id": employee_id
            }
            resp = self.client.table("roadmap_requests").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]["request_id"]
            return None
        except Exception as exc:
            logger.error(f"로드맵 요청 로깅 실패: {exc}")
            return None

    def log_agent_generation(
        self,
        request_id: str,
        agent_id: str,
        prompt_version_id: Optional[str],
        provider: str,
        model: str,
        roadmap_content: Dict[str, Any],
        summary: str,
        latency_ms: int,
        status: str = "SUCCESS"
    ) -> Optional[str]:
        """에이전트별 생성 결과 기록 (agent_generations)"""
        if self.client is None:
            return None
        try:
            payload = {
                "request_id": request_id,
                "agent_id": agent_id,
                "prompt_version_id": prompt_version_id,
                "provider": provider,
                "model": model,
                "roadmap_content": roadmap_content,
                "summary": summary,
                "latency_ms": latency_ms,
                "status": status
            }
            resp = self.client.table("agent_generations").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]["generation_id"]
            return None
        except Exception as exc:
            logger.error(f"에이전트 생성 결과 로깅 실패: {exc}")
            return None

    def log_agent_selection(
        self,
        request_id: str,
        chosen_generation_id: str,
        chosen_agent_id: str,
        user_type: str,
        selection_reason: Optional[str] = None
    ) -> Optional[str]:
        """사용자 로드맵 선택 결과 기록 (agent_selections)"""
        if self.client is None:
            return None
        try:
            payload = {
                "request_id": request_id,
                "chosen_generation_id": chosen_generation_id,
                "chosen_agent_id": chosen_agent_id,
                "user_type": user_type,
                "selection_reason": selection_reason
            }
            resp = self.client.table("agent_selections").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]["selection_id"]
            return None
        except Exception as exc:
            logger.error(f"에이전트 선택 로깅 실패: {exc}")
            return None

    def get_selection_statistics(self, user_type: Optional[str] = "REAL_USER") -> Dict[str, Any]:
        """에이전트별 선택 수 및 선택률 집계 통계"""
        default_stats = {
            "total_requests": 0,
            "total_selections": 0,
            "agent_counts": {
                "agent_practical": 0,
                "agent_certified": 0,
                "agent_fasttrack": 0
            },
            "selection_rates": {
                "agent_practical": 0.0,
                "agent_certified": 0.0,
                "agent_fasttrack": 0.0
            },
            "lowest_agent_id": "agent_practical"
        }
        if self.client is None:
            return default_stats

        try:
            query = self.client.table("agent_selections").select("chosen_agent_id, user_type")
            if user_type:
                query = query.eq("user_type", user_type)
            sel_resp = query.execute()
            selections = sel_resp.data or []

            total_selections = len(selections)
            counts = {
                "agent_practical": 0,
                "agent_certified": 0,
                "agent_fasttrack": 0
            }

            for row in selections:
                aid = row.get("chosen_agent_id")
                if aid in counts:
                    counts[aid] += 1

            rates = {}
            for aid, cnt in counts.items():
                rates[aid] = round(cnt / total_selections, 4) if total_selections > 0 else 0.0

            lowest_agent = min(counts.keys(), key=lambda k: counts[k])

            req_query = self.client.table("roadmap_requests").select("request_id", count="exact")
            if user_type:
                req_query = req_query.eq("user_type", user_type)
            req_resp = req_query.execute()
            total_requests = req_resp.count if req_resp.count is not None else total_selections

            return {
                "user_type_filter": user_type or "ALL",
                "total_requests": total_requests,
                "total_selections": total_selections,
                "agent_counts": counts,
                "selection_rates": rates,
                "lowest_agent_id": lowest_agent
            }
        except Exception as exc:
            logger.error(f"선택 통계 집계 중 오류 발생: {exc}")
            return default_stats

    def check_prompt_update_trigger(
        self,
        threshold: int = 10,
        user_type: str = "REAL_USER"
    ) -> Dict[str, Any]:
        """자가진화 프롬프트 최적화 트리거 도달 여부 판정"""
        stats = self.get_selection_statistics(user_type=user_type)
        total_sel = stats.get("total_selections", 0)

        should_trigger = (total_sel >= threshold) and (total_sel % threshold == 0)

        return {
            "threshold": threshold,
            "current_real_selections": total_sel,
            "should_trigger": should_trigger,
            "lowest_agent_id": stats.get("lowest_agent_id"),
            "selection_rates": stats.get("selection_rates"),
            "agent_counts": stats.get("agent_counts")
        }

    # ====================================================================
    # 5단계: 프롬프트 버전 관리, 평가 및 롤백 전용 영속화 메서드
    # ====================================================================

    def get_prompt_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """에이전트의 전체 프롬프트 버전 이력 조회"""
        if self.client is None:
            return []
        try:
            resp = self.client.table("prompt_versions").select("*").eq("agent_id", agent_id).order("version_num", desc=True).execute()
            return resp.data or []
        except Exception as exc:
            logger.error(f"[{agent_id}] 프롬프트 이력 조회 실패: {exc}")
            return []

    def get_active_prompt_for_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """특정 에이전트의 현재 활성(Active) 프롬프트 레코드 조회"""
        if self.client is None:
            return None
        try:
            resp = self.client.table("prompt_versions").select("*").eq("agent_id", agent_id).eq("is_active", True).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]
            return None
        except Exception as exc:
            logger.error(f"[{agent_id}] 활성 프롬프트 단건 조회 실패: {exc}")
            return None

    def create_prompt_version(
        self,
        agent_id: str,
        version_num: int,
        system_prompt: str,
        change_reason: str,
        parent_version_id: Optional[str] = None,
        is_active: bool = False
    ) -> Optional[Dict[str, Any]]:
        """신규 프롬프트 버전 레코드 등록"""
        if self.client is None:
            return None
        try:
            payload = {
                "agent_id": agent_id,
                "version_num": version_num,
                "system_prompt": system_prompt,
                "change_reason": change_reason,
                "parent_version_id": parent_version_id,
                "is_active": is_active
            }
            resp = self.client.table("prompt_versions").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]
            return None
        except Exception as exc:
            logger.error(f"[{agent_id}] 프롬프트 버전 등록 실패 (v{version_num}): {exc}")
            return None

    def activate_prompt_version(self, prompt_version_id: str, agent_id: str) -> bool:
        """
        프롬프트 버전 원자적 활성화 (부분 유니크 인덱스 uq_active_agent_prompt 보장)
        1. 기존 활성 버전을 찾아 비활성화 (is_active = false)
        2. 대상 버전을 활성화 (is_active = true)
        """
        if self.client is None:
            return False
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # 1. 기존 활성 프롬프트 비활성화
            self.client.table("prompt_versions").update({
                "is_active": False,
                "deactivated_at": now_iso
            }).eq("agent_id", agent_id).eq("is_active", True).execute()

            # 2. 지정된 프롬프트 버전 활성화
            up_resp = self.client.table("prompt_versions").update({
                "is_active": True,
                "deactivated_at": None
            }).eq("prompt_version_id", prompt_version_id).execute()

            return bool(up_resp.data and len(up_resp.data) > 0)
        except Exception as exc:
            logger.error(f"[{agent_id}] 프롬프트 버전 활성화 실패 ({prompt_version_id}): {exc}")
            return False

    def rollback_prompt_version(self, agent_id: str) -> Dict[str, Any]:
        """
        현재 활성 프롬프트의 부모(Parent) 버전으로 즉각 롤백
        """
        current_active = self.get_active_prompt_for_agent(agent_id)
        if not current_active:
            return {"success": False, "message": f"{agent_id}의 현재 활성 프롬프트를 찾을 수 없습니다."}

        parent_vid = current_active.get("parent_version_id")
        if not parent_vid:
            return {"success": False, "message": f"{agent_id} v{current_active.get('version_num')} 은 최초 버전이므로 롤백할 부모가 없습니다."}

        # 부모 버전 정보 조회
        p_resp = self.client.table("prompt_versions").select("*").eq("prompt_version_id", parent_vid).execute()
        if not p_resp.data or len(p_resp.data) == 0:
            return {"success": False, "message": f"부모 버전 ID({parent_vid}) 레코드가 DB에 존재하지 않습니다."}

        parent_record = p_resp.data[0]

        # 활성화 스위칭
        ok = self.activate_prompt_version(prompt_version_id=parent_vid, agent_id=agent_id)
        if ok:
            logger.info(f"[{agent_id}] 롤백 완료: v{current_active.get('version_num')} -> v{parent_record.get('version_num')}")
            return {
                "success": True,
                "rolled_back_from": current_active.get("version_num"),
                "restored_version": parent_record.get("version_num"),
                "active_prompt_version_id": parent_vid
            }
        return {"success": False, "message": "롤백 트랜잭션 실행 중 오류 발생"}

    def log_prompt_evaluation(
        self,
        prompt_version_id: str,
        evaluator_model: str,
        rubric_scores: Dict[str, Any],
        total_score: float,
        passed: bool,
        eval_report: str
    ) -> Optional[str]:
        """프롬프트 가드레일 평가 결과 기록 (prompt_evaluations)"""
        if self.client is None:
            return None
        try:
            payload = {
                "prompt_version_id": prompt_version_id,
                "evaluator_model": evaluator_model,
                "rubric_scores": rubric_scores,
                "total_score": total_score,
                "passed": passed,
                "eval_report": eval_report
            }
            resp = self.client.table("prompt_evaluations").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]["evaluation_id"]
            return None
        except Exception as exc:
            logger.error(f"프롬프트 평가 기록 실패: {exc}")
            return None

    def log_prompt_optimization(
        self,
        target_agent_id: str,
        trigger_threshold: int,
        cumulative_real_selections: int,
        agent_selection_rate: float,
        old_prompt_version_id: str,
        candidate_prompt_text: str,
        evaluation_id: Optional[str],
        status: str
    ) -> Optional[str]:
        """프롬프트 자가진화 실행 이력 기록 (prompt_optimizations)"""
        if self.client is None:
            return None
        try:
            payload = {
                "target_agent_id": target_agent_id,
                "trigger_threshold": trigger_threshold,
                "cumulative_real_selections": cumulative_real_selections,
                "agent_selection_rate": agent_selection_rate,
                "old_prompt_version_id": old_prompt_version_id,
                "candidate_prompt_text": candidate_prompt_text,
                "evaluation_id": evaluation_id,
                "status": status
            }
            resp = self.client.table("prompt_optimizations").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                return resp.data[0]["optimization_id"]
            return None
        except Exception as exc:
            logger.error(f"프롬프트 자가진화 이력 기록 실패: {exc}")
            return None


# 모듈 단위 편의 인스턴스
db_client = DatabaseClient()