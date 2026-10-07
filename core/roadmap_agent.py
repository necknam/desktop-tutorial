import json
import time
import logging
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.llm_adapter import llm_adapter, DISCONNECTED_STANDARD_MESSAGE
from database.db_client import db_client

logger = logging.getLogger("ROADMAP_AGENT")


class BaseRoadmapAgent:
    """전략 에이전트 추상 기본 클래스"""

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
- 수행 프로젝트 과제: {user_context.get('work_context_summary', '현업 프로젝트 완수')}
- 현재 보유 스킬: {user_context.get('current_skills', [])}
- 프로젝트 요구 스킬: {user_context.get('target_skills', [])}

[대화 히스토리]
{history_text if history_text else "이전 대화 없음"}

[DB 검색 사실 근거 자원 풀 (반드시 이 목록 안의 자원만 추천 가능)]
- 이용 가능 강좌: {json.dumps(retrieved_context.get('courses', []), ensure_ascii=False)}
- 이용 가능 자격증: {json.dumps(retrieved_context.get('certifications', []), ensure_ascii=False)}
- 이용 가능 도서: {json.dumps(retrieved_context.get('books', []), ensure_ascii=False)}

[핵심 설계 원칙 및 누락 방지 규칙]
1. 로드맵은 반드시 3단계(Phase 1, Phase 2, Phase 3)로 구성하세요.
2. [필수 누락 방지] Phase 1, Phase 2, Phase 3 각각의 단계마다 'recommended_courses', 'recommended_books', 'recommended_certifications'를 최소 1건 이상 반드시 포함하세요. 빈 배열([])을 반환하지 마세요.
3. summary 필드에는 부서원이 왜 이 플랜을 따라가야 하는지 핵심 이유와 플랜 전체 진행 방향 요약(2~3줄)을 명쾌하게 기술하세요.
4. 강좌 추천 시 제공된 실제 url을 누락 없이 포함하세요.
5. 출력은 마크다운 코드블록 없이 오직 유효한 단일 JSON 객체여야 합니다.

[출력 JSON 규격]
{{
  "strategy_id": "{self.agent_id}",
  "strategy_title": "전략 타이틀",
  "total_duration_weeks": 8,
  "summary": "부서원이 이 플랜을 따라가야 하는 핵심 이유와 전체 플랜 요약 (2~3줄)",
  "project_goal": "본 로드맵을 통해 달성할 프로젝트의 구체적 기술 목표",
  "milestones": [
    {{
      "phase": 1,
      "phase_name": "Phase 1: 기반 환경 구축 및 핵심 프로그래밍",
      "duration": "2주",
      "focus_goal": "기초 달성 목표",
      "project_impact": "프로젝트 기여 가치",
      "key_actions": ["실무 액션 1", "실무 액션 2"],
      "recommended_courses": [{{"course_id": "...", "title": "...", "platform": "...", "url": "..."}}],
      "recommended_certifications": [{{"cert_id": 201, "title": "...", "provider": "..."}}],
      "recommended_books": [{{"isbn": "...", "title": "...", "author": "...", "is_related": true}}]
    }},
    {{
      "phase": 2,
      "phase_name": "Phase 2: 비동기 API 및 데이터베이스 아키텍처 실무",
      "duration": "4주",
      "focus_goal": "심화 기술 구현 목표",
      "project_impact": "프로젝트 기여 가치",
      "key_actions": ["실무 액션 1", "실무 액션 2"],
      "recommended_courses": [{{"course_id": "...", "title": "...", "platform": "...", "url": "..."}}],
      "recommended_certifications": [{{"cert_id": 203, "title": "...", "provider": "..."}}],
      "recommended_books": [{{"isbn": "...", "title": "...", "author": "...", "is_related": true}}]
    }},
    {{
      "phase": 3,
      "phase_name": "Phase 3: 컨테이너 오케스트레이션 및 무중단 CI/CD 배포",
      "duration": "2주",
      "focus_goal": "운영 안정성 달성 목표",
      "project_impact": "프로젝트 기여 가치",
      "key_actions": ["실무 액션 1", "실무 액션 2"],
      "recommended_courses": [{{"course_id": "...", "title": "...", "platform": "...", "url": "..."}}],
      "recommended_certifications": [{{"cert_id": 202, "title": "...", "provider": "..."}}],
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
        """단일 에이전트 생성 및 마일스톤 밸런서를 통한 전 단계 자원 배정 보장"""
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

            # 런타임 마일스톤 밸런서 (Milestone Balancer)
            pool_courses = retrieved_context.get("courses", [])
            pool_books = retrieved_context.get("books", [])
            pool_certs = retrieved_context.get("certifications", [])
            milestones = parsed_content.get("milestones", [])

            if len(milestones) < 3:
                while len(milestones) < 3:
                    p_num = len(milestones) + 1
                    milestones.append({
                        "phase": p_num,
                        "phase_name": f"Phase {p_num}: 프로젝트 고도화 및 실무 적용",
                        "duration": "2주",
                        "focus_goal": "실무 산출물 검증 및 성능 최적화",
                        "project_impact": "프로젝트 품질 고도화",
                        "key_actions": ["단위 테스트 작성", "배포 파이프라인 연계"],
                        "recommended_courses": [],
                        "recommended_books": [],
                        "recommended_certifications": [],
                    })
                parsed_content["milestones"] = milestones

            for idx, ms in enumerate(milestones):
                if not ms.get("recommended_courses") and pool_courses:
                    ms["recommended_courses"] = [pool_courses[idx % len(pool_courses)]]
                if not ms.get("recommended_books") and pool_books:
                    ms["recommended_books"] = [pool_books[idx % len(pool_books)]]
                if not ms.get("recommended_certifications") and pool_certs:
                    ms["recommended_certifications"] = [pool_certs[idx % len(pool_certs)]]

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
            default_prompt=(
                "당신은 실무 프로젝트 성공 중심의 업무 개발 로드맵 전문가입니다. "
                "부서원이 현업 프로젝트를 직접 완수할 수 있도록 Phase 1부터 Phase 3까지 "
                "모든 단계에 핸즈온 실습 강좌와 도서를 빠짐없이 배정하세요."
            ),
        )


class CertifiedTheoryAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="agent_certified",
            default_prompt=(
                "당신은 공인 자격 검증 및 표준 이론 기반 업무 개발 로드맵 전문가입니다. "
                "프로젝트의 기술 부채를 예방하고 아키텍처 안정성을 확보하기 위해 "
                "Phase 1부터 Phase 3까지 모든 단계에 표준 원리 도서와 공인 자격증 검증 과정을 빠짐없이 배정하세요."
            ),
        )


class FastTrackAgent(BaseRoadmapAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="agent_fasttrack",
            default_prompt=(
                "당신은 단기 패스트트랙 중심 업무 개발 로드맵 전문가입니다. "
                "프로젝트 납기 일정에 맞추어 최소 학습 시간 투입으로 현업에 조기 투입될 수 있도록 "
                "Phase 1부터 Phase 3까지 모든 단계에 요약 강좌와 도서를 빠짐없이 배정하세요."
            ),
        )


class MultiAgentOrchestrator:
    """3개 전략 에이전트 병렬 오케스트레이터"""

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
        logger.info(f"3개 전략 에이전트 병렬 실행 시작 (대상자: {user_context.get('profile_name', '부서원')})")

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
                    logger.info(f"[{agent_id}] 생성 완료 ({res.get('latency_ms')} ms)")
                except Exception as exc:
                    logger.error(f"[{agent_id}] 실행 중 예외 발생: {exc}")
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
        logger.info(f"모든 에이전트 병렬 실행 완료 (총 소요 시간: {total_latency} ms)")
        return {
            "total_latency_ms": total_latency,
            "results": results,
        }


multi_agent_orchestrator = MultiAgentOrchestrator()
__all__ = ["MultiAgentOrchestrator", "multi_agent_orchestrator"]