# =====================================================================
# FILE: src/core/roadmap_agent.py
# =====================================================================

import json
import time
import logging
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.core.llm_adapter import llm_adapter, DISCONNECTED_STANDARD_MESSAGE

try:
    from src.db.db_client import db_client
except ImportError:
    from db.db_client import db_client

logger = logging.getLogger("ROADMAP_AGENT")


def find_best_matching_resource(context_text: str, candidates: List[Dict[str, Any]], key_fields: List[str]) -> Dict[str, Any]:
    """마일스톤 문맥과 RAG 검색 자원 간의 키워드 적합도를 계산하여 최적 실존 자원 1건 반환"""
    if not candidates:
        return {}

    ctx_lower = context_text.lower()
    best_item = candidates[0]
    best_score = -1

    for item in candidates:
        score = 0
        target_text = " ".join([str(item.get(f, "")) for f in key_fields]).lower()

        for kw in ["fastapi", "python", "postgresql", "sql", "docker", "kubernetes", "k8s", "redis", "aws", "azure", "ci/cd", "test", "security"]:
            if kw in ctx_lower and kw in target_text:
                score += 8

        words = [w for w in ctx_lower.split() if len(w) > 1]
        for w in words:
            if w in target_text:
                score += 1

        if score > best_score:
            best_score = score
            best_item = item

    return best_item


class BaseRoadmapAgent:
    """DB 프롬프트 기반 전략 로드맵 에이전트 기본 클래스"""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    def get_system_prompt(self) -> str:
        """
        [DB 기반 프롬프트 로딩]
        DB의 prompt_versions 테이블에서 agent_id에 매핑된 활성 시스템 프롬프트를 조회합니다.
        DB 연결 불가 시에만 최소한의 안전 폴백 문자열을 반환합니다.
        """
        try:
            if hasattr(db_client, "get_active_prompts"):
                active_prompts = db_client.get_active_prompts() or {}
                if isinstance(active_prompts, dict) and self.agent_id in active_prompts:
                    prompt_text = active_prompts[self.agent_id].get("system_prompt")
                    if prompt_text and prompt_text.strip():
                        return prompt_text.strip()
        except Exception as exc:
            logger.warning(f"[{self.agent_id}] DB 프롬프트 조회 중 예외 발생: {exc}")

        return f"당신은 엔터프라이즈 기술 로드맵 전문 설계 에이전트({self.agent_id})입니다. 컨텍스트의 RAG 실존 자원을 반영하여 유효한 JSON 객체로 로드맵을 작성하세요."

    def build_runtime_data_context(
        self,
        user_context: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        retrieved_context: Dict[str, Any],
        hidden_assessment_responses: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        [순수 런타임 데이터 바인딩]
        파이썬 코드에 프롬프트 지침이나 규칙을 넣지 않고, 오직 실행 시점의 동적 데이터만을 직렬화하여 전달합니다.
        """
        history_text = "\n".join([f"- {h.get('role', 'user')}: {h.get('content', '')}" for h in conversation_history])

        hidden_context_str = "사전 진단 미실시 (기본 균형 난이도 적용)"
        if hidden_assessment_responses:
            hidden_context_str = json.dumps(hidden_assessment_responses, ensure_ascii=False, indent=2)

        return f"""[엔지니어 프로필 및 현업 프로젝트 배경]
- 성명/직급: {user_context.get('profile_name', '엔지니어')} ({user_context.get('grade', '실무자')})
- 소속 부서: {user_context.get('department', '플랫폼개발팀')}
- 담당 직무: {user_context.get('job_title', '백엔드 엔지니어')}
- 과제 개요: {user_context.get('work_context_summary', '현업 프로젝트 완수')}
- 보유 스킬: {user_context.get('current_skills', [])}
- 목표 요구 스킬: {user_context.get('target_skills', [])}

[사용자 3단계 사전 역량 진단 결과 (Hidden Context)]
{hidden_context_str}

[RAG 시스템 검색 결과 (DB 실존 사실 기반 자원 풀)]
- 실존 온라인 강좌: {json.dumps(retrieved_context.get('courses', []), ensure_ascii=False)}
- 실존 공인 자격증: {json.dumps(retrieved_context.get('certifications', []), ensure_ascii=False)}
- 실존 전문 도서: {json.dumps(retrieved_context.get('books', []), ensure_ascii=False)}

[대화 합의 이력]
{history_text if history_text else "이전 합의 대화 없음"}

위 런타임 데이터를 귀하의 시스템 프롬프트 지침에 따라 분석하여, 규정된 JSON 포맷으로 로드맵을 생성하세요.
"""

    def generate(
        self,
        user_context: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        retrieved_context: Dict[str, Any],
        hidden_assessment_responses: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        t0 = time.monotonic()
        system_prompt = self.get_system_prompt()
        user_message_content = self.build_runtime_data_context(
            user_context=user_context,
            conversation_history=conversation_history,
            retrieved_context=retrieved_context,
            hidden_assessment_responses=hidden_assessment_responses,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message_content},
        ]
        llm_resp = llm_adapter.generate(messages, temperature=0.25)
        latency = int((time.monotonic() - t0) * 1000)

        if not llm_resp.success:
            return {
                "agent_id": self.agent_id,
                "status": "FAILED",
                "provider": llm_resp.provider,
                "model": llm_resp.model,
                "latency_ms": latency,
                "summary": DISCONNECTED_STANDARD_MESSAGE,
                "roadmap_content": {"error": DISCONNECTED_STANDARD_MESSAGE},
            }

        raw_text = llm_resp.content.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        try:
            parsed_content = json.loads(raw_text)
            parsed_content["strategy_id"] = self.agent_id
            summary = parsed_content.get("summary", f"{self.agent_id} 로드맵 수립 완료")

            pool_courses = retrieved_context.get("courses", [])
            pool_books = retrieved_context.get("books", [])
            pool_certs = retrieved_context.get("certifications", [])
            milestones = parsed_content.get("milestones", [])

            # RAG 실존 자원 엄격 매핑 검증 및 보정
            for ms in milestones:
                ms_context = f"{ms.get('phase_name', '')} {ms.get('focus_goal', '')} {' '.join(ms.get('key_actions', []))}"

                if not ms.get("recommended_certifications") or any("http" in str(c.get("title", "")) for c in ms.get("recommended_certifications", [])):
                    best_cert = find_best_matching_resource(ms_context, pool_certs, ["title", "test_subjects"])
                    if best_cert:
                        ms["recommended_certifications"] = [best_cert]

                if not ms.get("recommended_courses") or not any(c.get("url") for c in ms.get("recommended_courses", [])):
                    best_course = find_best_matching_resource(ms_context, pool_courses, ["title", "category_name"])
                    if best_course:
                        ms["recommended_courses"] = [best_course]

                if not ms.get("recommended_books") or not any(b.get("title") for b in ms.get("recommended_books", [])):
                    best_book = find_best_matching_resource(ms_context, pool_books, ["title"])
                    if best_book:
                        ms["recommended_books"] = [best_book]

            # 네모 카드 요약 순서 플로우 자동 보정
            if not parsed_content.get("roadmap_flow") and milestones:
                flow = []
                for idx, m in enumerate(milestones, 1):
                    flow.append({
                        "step": idx,
                        "title": m.get("phase_name", f"Phase {idx}"),
                        "duration": m.get("duration", f"{idx*2}주차"),
                        "focus": m.get("focus_goal", "")[:32]
                    })
                parsed_content["roadmap_flow"] = flow

        except Exception as exc:
            logger.warning(f"[{self.agent_id}] JSON 파싱 실패, 텍스트 폴백 적용: {exc}")
            parsed_content = {"raw_output": raw_text}
            summary = raw_text[:140] + "..."

        return {
            "agent_id": self.agent_id,
            "status": "SUCCESS",
            "provider": llm_resp.provider,
            "model": llm_resp.model,
            "latency_ms": latency,
            "summary": summary,
            "roadmap_content": parsed_content,
        }


# 에이전트 클래스: 프롬프트 하드코딩 없이 식별자(agent_id)만 등록
class PracticalProjectAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(agent_id="agent_practical")


class CertifiedTheoryAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(agent_id="agent_certified")


class FastTrackAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(agent_id="agent_fasttrack")


class MultiAgentOrchestrator:
    def __init__(self) -> None:
        self.agents = [
            PracticalProjectAgent(),
            CertifiedTheoryAgent(),
            FastTrackAgent(),
        ]

    def generate_all_roadmaps(
        self,
        user_context: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        retrieved_context: Dict[str, Any],
        hidden_assessment_responses: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        t_start = time.monotonic()
        results: Dict[str, Any] = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_agent = {
                executor.submit(
                    agent.generate,
                    user_context,
                    conversation_history,
                    retrieved_context,
                    hidden_assessment_responses,
                ): agent.agent_id
                for agent in self.agents
            }

            for future in as_completed(future_to_agent):
                agent_id = future_to_agent[future]
                try:
                    res = future.result()
                    results[agent_id] = res
                except Exception as exc:
                    logger.error(f"에이전트 [{agent_id}] 실행 실패: {exc}")
                    results[agent_id] = {
                        "agent_id": agent_id,
                        "status": "FAILED",
                        "provider": "unknown",
                        "model": "unknown",
                        "latency_ms": int((time.monotonic() - t_start) * 1000),
                        "summary": f"에이전트 실행 실패: {str(exc)}",
                        "roadmap_content": {"error": str(exc)},
                    }

        total_latency = int((time.monotonic() - t_start) * 1000)
        return {
            "total_latency_ms": total_latency,
            "results": results,
        }


multi_agent_orchestrator = MultiAgentOrchestrator()
__all__ = ["MultiAgentOrchestrator", "multi_agent_orchestrator"]