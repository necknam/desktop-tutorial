import json
import time
import logging
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.llm_adapter import llm_adapter, DISCONNECTED_STANDARD_MESSAGE
from database.db_client import db_client

logger = logging.getLogger("ROADMAP_AGENT")


def find_best_matching_resource(context_text: str, candidates: List[Dict[str, Any]], key_fields: List[str]) -> Dict[
    str, Any]:
    """마일스톤 텍스트와 후보 자원들 간의 키워드 적합도를 계산하여 최적 자원 1건 반환"""
    if not candidates:
        return {}

    ctx_lower = context_text.lower()
    best_item = candidates[0]
    best_score = -1

    for item in candidates:
        score = 0
        target_text = " ".join([str(item.get(f, "")) for f in key_fields]).lower()

        # 주요 기술 스택 일치 가중치 부여
        for kw in ["aws", "azure", "docker", "kubernetes", "k8s", "sql", "python", "fastapi", "linux", "security",
                   "test"]:
            if kw in ctx_lower and kw in target_text:
                score += 5

        # 일반 단어 일치 점수
        words = [w for w in ctx_lower.split() if len(w) > 1]
        for w in words:
            if w in target_text:
                score += 1

        if score > best_score:
            best_score = score
            best_item = item

    return best_item


class BaseRoadmapAgent:
    """전략 에이전트 기본 클래스"""

    def __init__(self, agent_id: str, default_prompt: str) -> None:
        self.agent_id = agent_id
        self.default_prompt = default_prompt

    def get_system_prompt(self) -> str:
        active_prompts = db_client.get_active_prompts()
        if self.agent_id in active_prompts:
            return active_prompts[self.agent_id].get("system_prompt", self.default_prompt)
        return self.default_prompt

    def build_user_instruction(
            self,
            user_context: Dict[str, Any],
            conversation_history: List[Dict[str, Any]],
            retrieved_context: Dict[str, Any],
    ) -> str:
        history_text = "\n".join([f"- {h.get('role', 'user')}: {h.get('content', '')}" for h in conversation_history])

        prompt = f"""[부서원 프로필 및 프로젝트 배경]
- 성명/직급: {user_context.get('profile_name', '부서원')} ({user_context.get('grade', '실무자')})
- 소속 부서: {user_context.get('department', '미지정')}
- 담당 직무: {user_context.get('job_title', 'IT 개발')}
- 수행 프로젝트 맥락: {user_context.get('work_context_summary', '현업 프로젝트 완수')}
- 현재 보유 스킬: {user_context.get('current_skills', [])}
- 프로젝트 요구 스킬: {user_context.get('target_skills', [])}

[대화 맥락]
{history_text if history_text else "이전 대화 없음"}

[DB 검색 결과 (사실 근거 실존 자원 풀)]
- 이용 가능 강좌: {json.dumps(retrieved_context.get('courses', []), ensure_ascii=False)}
- 이용 가능 자격증: {json.dumps(retrieved_context.get('certifications', []), ensure_ascii=False)}
- 이용 가능 도서: {json.dumps(retrieved_context.get('books', []), ensure_ascii=False)}

[핵심 설계 원칙 및 엄격한 가드레일]
1. [필수 정합성] 각 마일스톤(Phase 1, 2, 3)의 목표 및 액션 플랜과 '추천 자원(강좌, 도서, 자격증)'은 기술 스택이 100% 일치해야 합니다.
   - 예: 단계 목표가 AWS/클라우드 설계라면 자격증은 반드시 AWS SAA 자격증을 매핑하고, SQL 단계라면 SQLD를 매핑하세요.
   - DB 검색 결과에 없는 외부 벤더를 임의로 엮지 마시고, 제공된 [DB 검색 결과] 내의 실존 강좌 풀을 중심으로 계획을 수립하세요.
2. 모든 단계에 recommended_courses, recommended_books, recommended_certifications를 최소 1건 이상 반드시 포함하세요.
3. summary 필드에는 부서원이 왜 이 플랜을 따라가야 하는지 핵심 이유와 진행 방향 요약(2~3줄)을 기술하세요.
4. 강좌 추천 시 반드시 위 목록에 제공된 실제 url을 누락 없이 포함하세요.
5. 출력은 마크다운 코드블록 없이 오직 유효한 단일 JSON 객체여야 합니다.

[출력 JSON 규격]
{{
  "strategy_id": "{self.agent_id}",
  "strategy_title": "전략 타이틀",
  "total_duration_weeks": 8,
  "summary": "핵심 이유와 요약",
  "project_goal": "구체적 기술 목표",
  "milestones": [
    {{
      "phase": 1,
      "phase_name": "Phase 1: ...",
      "duration": "2주",
      "focus_goal": "...",
      "project_impact": "...",
      "key_actions": ["..."],
      "recommended_courses": [{{"course_id": "...", "title": "...", "platform": "...", "url": "..."}}],
      "recommended_certifications": [{{"cert_id": 201, "title": "...", "provider": "..."}}],
      "recommended_books": [{{"isbn": "...", "title": "...", "author": "...", "is_related": true}}]
    }}
  ]
}}
"""
        return prompt

    def generate(
            self,
            user_context: Dict[str, Any],
            conversation_history: List[Dict[str, Any]],
            retrieved_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        t0 = time.monotonic()
        system_prompt = self.get_system_prompt()
        user_prompt = self.build_user_instruction(user_context, conversation_history, retrieved_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        llm_resp = llm_adapter.generate(messages, temperature=0.3)
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
            summary = parsed_content.get("summary", f"{self.agent_id} 로드맵 생성 완료")

            pool_courses = retrieved_context.get("courses", [])
            pool_books = retrieved_context.get("books", [])
            pool_certs = retrieved_context.get("certifications", [])
            milestones = parsed_content.get("milestones", [])

            # [핵심 개편] 마일스톤별 시맨틱 적합도 기반 스마트 자원 재배정 (단순 순번 폐지)
            for ms in milestones:
                ms_context = f"{ms.get('phase_name', '')} {ms.get('focus_goal', '')} {' '.join(ms.get('key_actions', []))}"

                # 1. 자격증 정합성 보정 (액션 플랜의 키워드와 가장 일치하는 자격증 강제 매칭)
                if not ms.get("recommended_certifications") or any(
                        "aws" in ms_context.lower() and "sql" in str(c.get("title", "")).lower() for c in
                        ms.get("recommended_certifications", [])):
                    best_cert = find_best_matching_resource(ms_context, pool_certs, ["title", "test_subjects"])
                    if best_cert:
                        ms["recommended_certifications"] = [best_cert]

                # 2. 강좌 정합성 보정
                if not ms.get("recommended_courses"):
                    best_course = find_best_matching_resource(ms_context, pool_courses, ["title", "category_name"])
                    if best_course:
                        ms["recommended_courses"] = [best_course]

                # 3. 도서 정합성 보정
                if not ms.get("recommended_books"):
                    best_book = find_best_matching_resource(ms_context, pool_books, ["title"])
                    if best_book:
                        ms["recommended_books"] = [best_book]

        except Exception as exc:
            logger.warning(f"[{self.agent_id}] JSON 파싱 실패, 텍스트 폴백 적용: {exc}")
            parsed_content = {"raw_output": raw_text}
            summary = raw_text[:120] + "..."

        return {
            "agent_id": self.agent_id,
            "status": "SUCCESS",
            "provider": llm_resp.provider,
            "model": llm_resp.model,
            "latency_ms": latency,
            "summary": summary,
            "roadmap_content": parsed_content,
        }


class PracticalProjectAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="agent_practical",
            default_prompt="당신은 실무 프로젝트 성공 중심의 업무 개발 로드맵 전문가입니다. 각 단계별 실무 과제와 자원이 정확히 일치하도록 로드맵을 설계하세요.",
        )


class CertifiedTheoryAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="agent_certified",
            default_prompt="당신은 공인 자격 검증 및 표준 이론 기반 업무 개발 로드맵 전문가입니다. 각 단계별 학습 주제와 공인 자격증이 일치하도록 로드맵을 설계하세요.",
        )


class FastTrackAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="agent_fasttrack",
            default_prompt="당신은 단기 패스트트랙 중심 업무 개발 로드맵 전문가입니다. 최소 시간으로 현업에 투입될 수 있도록 핵심 자원을 단계별로 매칭하세요.",
        )


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
                ): agent.agent_id
                for agent in self.agents
            }

            for future in as_completed(future_to_agent):
                agent_id = future_to_agent[future]
                try:
                    res = future.result()
                    results[agent_id] = res
                except Exception as exc:
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