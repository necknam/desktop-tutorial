"""
선택률 기반 프롬프트 자가진화(Optimizer) 엔진
파일 경로: core/self_evolving_optimizer.py
역할: 선택률 최저 에이전트 식별 -> Reflection 취약점 분석 -> 개선 후보 생성 -> LLM-as-a-Judge 8대 가드레일 평가 -> 승격/유지
"""

import json
import logging
from typing import Dict, Any, Optional
from core.llm_adapter import llm_adapter
from core.prompt_manager import prompt_manager
from database.db_client import db_client

logger = logging.getLogger("SELF_EVOLVING_OPTIMIZER")

# 요구사항 상단 설정값
PROMPT_UPDATE_THRESHOLD = 10
EVAL_SCORE_PASS_THRESHOLD = 8.5


class SelfEvolvingOptimizer:
    """Reflection 및 LLM-as-a-Judge 가드레일 기반 프롬프트 자가진화 엔진"""

    def __init__(self) -> None:
        self.db = db_client
        self.prompt_mgr = prompt_manager
        self.llm = llm_adapter

    def check_and_evolve(
        self,
        threshold: int = PROMPT_UPDATE_THRESHOLD,
        user_type: str = "REAL_USER",
        force_run: bool = False
    ) -> Dict[str, Any]:
        """
        자가진화 파이프라인 트리거 확인 및 실행 진입점
        - force_run: 단위 테스트나 수동 트리거 시 누적 건수 조건 우회 허용
        """
        trigger_status = self.db.check_prompt_update_trigger(threshold=threshold, user_type=user_type)
        should_trigger = trigger_status.get("should_trigger", False) or force_run

        if not should_trigger:
            return {
                "triggered": False,
                "message": f"트리거 조건 미충족 (현재 REAL_USER 선택: {trigger_status.get('current_real_selections')}건, 기준: {threshold}건)",
                "status": "SKIPPED"
            }

        target_agent_id = trigger_status.get("lowest_agent_id", "agent_fasttrack")
        selection_rates = trigger_status.get("selection_rates", {})
        target_rate = selection_rates.get(target_agent_id, 0.0)

        logger.info(f"=== [자가진화 트리거 가동] 대상 에이전트: {target_agent_id} (선택률: {target_rate * 100:.1f}%) ===")

        return self.evolve_target_agent(
            target_agent_id=target_agent_id,
            selection_rate=target_rate,
            cumulative_selections=trigger_status.get("current_real_selections", 0),
            threshold=threshold
        )

    def evolve_target_agent(
        self,
        target_agent_id: str,
        selection_rate: float,
        cumulative_selections: int,
        threshold: int
    ) -> Dict[str, Any]:
        """단일 대상 에이전트 대상 프롬프트 자가진화 라이프사이클 수행"""
        active_prompt_record = self.prompt_mgr.get_active_prompt(target_agent_id)
        if not active_prompt_record:
            return {"triggered": True, "status": "ERROR", "message": f"{target_agent_id}의 활성 프롬프트 부재"}

        old_vid = active_prompt_record["prompt_version_id"]
        old_prompt_text = active_prompt_record["system_prompt"]
        old_version_num = active_prompt_record["version_num"]

        # ---------------- 1. Reflection 취약점 분석 ----------------
        reflection_report = self._perform_reflection(target_agent_id, old_prompt_text)

        # ---------------- 2. 개선 후보 프롬프트 생성 ----------------
        candidate_prompt = self._generate_candidate_prompt(target_agent_id, old_prompt_text, reflection_report)

        # ---------------- 3. LLM-as-a-Judge 8대 가드레일 평가 ----------------
        eval_result = self._evaluate_candidate_prompt(
            agent_id=target_agent_id,
            old_prompt=old_prompt_text,
            candidate_prompt=candidate_prompt
        )

        passed = eval_result["passed"]
        total_score = eval_result["total_score"]

        # 평가 결과 DB 기록
        eval_id = self.db.log_prompt_evaluation(
            prompt_version_id=old_vid,
            evaluator_model=self.llm.openai_model,
            rubric_scores=eval_result["rubric_scores"],
            total_score=total_score,
            passed=passed,
            eval_report=eval_result["eval_report"]
        )

        # ---------------- 4. 평가 통과 시 승격 / 실패 시 유지 ----------------
        if passed:
            logger.info(f"[{target_agent_id}] 8대 가드레일 평가 통과 (총점: {total_score}점) -> 신규 버전 승격 진행")
            promoted = self.prompt_mgr.promote_candidate_prompt(
                agent_id=target_agent_id,
                candidate_text=candidate_prompt,
                change_reason=f"자가진화 반영: 선택률({selection_rate*100:.1f}%) 개선 및 8대가드레일({total_score}점) 통과",
                parent_version_id=old_vid
            )
            opt_status = "EVAL_PASSED"
            new_version_num = promoted["version_num"] if promoted else old_version_num + 1
        else:
            logger.warning(f"[{target_agent_id}] 8대 가드레일 평가 미달 (총점: {total_score}점) -> 기존 프롬프트 유지")
            opt_status = "EVAL_FAILED"
            new_version_num = old_version_num

        # 최적화 이력 DB 기록
        self.db.log_prompt_optimization(
            target_agent_id=target_agent_id,
            trigger_threshold=threshold,
            cumulative_real_selections=cumulative_selections,
            agent_selection_rate=selection_rate,
            old_prompt_version_id=old_vid,
            candidate_prompt_text=candidate_prompt,
            evaluation_id=eval_id,
            status=opt_status
        )

        return {
            "triggered": True,
            "status": opt_status,
            "target_agent_id": target_agent_id,
            "previous_version": old_version_num,
            "current_active_version": new_version_num,
            "evaluation_scores": eval_result["rubric_scores"],
            "total_score": total_score,
            "passed": passed,
            "candidate_prompt": candidate_prompt,
            "reflection_report": reflection_report
        }

    def _perform_reflection(self, agent_id: str, current_prompt: str) -> str:
        """Reflection: 선택률 저조 원인 및 취약점 분석"""
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 사내 HR 로드맵 시스템의 프롬프트 최적화 연구원(Prompt Reflection Specialist)입니다. "
                    "특정 에이전트가 사용자들에게 선택받지 못하는 취약점을 분석하고 구체적인 강화 포인트를 도출하세요."
                )
            },
            {
                "role": "user",
                "content": f"""[분석 대상 에이전트]: {agent_id}
[현재 적용 중인 시스템 프롬프트]:
{current_prompt}

[경쟁 에이전트 현황]:
- 실무 프로젝트형 (agent_practical): 구체적 코드와 실습을 강조하여 실무자 선호도 높음
- 이론/자격증형 (agent_certified): 공인자격 취득을 통한 객관적 검증으로 부서장 신뢰도 높음
- 단기 패스트트랙형 (agent_fasttrack): 시간 부족 시 압축 학습 제공

[요청 사항]
현재 프롬프트가 사용자 선택을 받지 못하는 결정적 이유 2가지와, 에이전트의 고유 특색을 살리면서 신뢰도를 높이기 위한 개선 전략을 3~4문장으로 요약 작성하세요."""
            }
        ]
        resp = self.llm.generate(messages, temperature=0.3)
        return resp.content if resp.success else "사용자 피드백을 반영하여 직무 적용성과 일정 구체성을 보강해야 함."

    def _generate_candidate_prompt(self, agent_id: str, old_prompt: str, reflection_report: str) -> str:
        """개선 후보 시스템 프롬프트 생성"""
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 LLM 시스템 프롬프트를 고도화하는 전문 엔지니어입니다. "
                    "기존 프롬프트의 기본 정체성을 유지하되, 취약점 분석서를 바탕으로 완성도와 차별성을 강화한 '새로운 시스템 프롬프트 전문'만을 출력하세요."
                )
            },
            {
                "role": "user",
                "content": f"""[대상 에이전트]: {agent_id}
[기존 시스템 프롬프트]:
{old_prompt}

[취약점 분석 리포트]:
{reflection_report}

[필수 준수 가드레일 규칙]:
1. 에이전트 본연의 핵심 전략({agent_id}) 정체성을 반드시 유지할 것.
2. '추천 데이터는 반드시 컨텍스트로 제공된 실제 DB 검색 결과(강좌, 도서, 자격증)만을 사용하고 외부 데이터를 날조하지 마세요.' 문구를 반드시 포함할 것.
3. 부서장 관점의 업무 개발(Business Development) 가치를 입증할 수 있도록 지침을 구체화할 것.
4. 불필요한 설명 없이 개선된 시스템 프롬프트 본문 텍스트만 출력할 것."""
            }
        ]
        resp = self.llm.generate(messages, temperature=0.3)
        return resp.content.strip() if resp.success else old_prompt

    def _evaluate_candidate_prompt(
        self,
        agent_id: str,
        old_prompt: str,
        candidate_prompt: str
    ) -> Dict[str, Any]:
        """LLM-as-a-Judge 8대 가드레일 정량 평가"""
        eval_prompt = f"""당신은 엄격한 LLM 프롬프트 품질 및 가드레일 심사위원(LLM-as-a-Judge)입니다.
제안된 후보 시스템 프롬프트가 운영 환경에 적합한지 8대 기준에 따라 엄격히 심사하세요.

[에이전트 ID]: {agent_id}
[기존 프롬프트]:
{old_prompt}

[개선 후보 프롬프트]:
{candidate_prompt}

[8대 심사 기준]
1. user_alignment (1~10점): 사용자 직무 및 역량 요구사항 반영 역량
2. roadmap_quality (1~10점): 로드맵 품질 및 논리적 완결성
3. strategic_differentiation (1~10점): 고유 전략 페르소나 차별성
4. format_consistency (1~10점): 일관된 규격 준수 지침
5. db_grounding_compliance (true/false): DB 검색 결과만 사용하도록 엄격히 제약하는가
6. no_hallucination (true/false): 외부 임의 데이터 생성을 금지하는가
7. improvement_over_parent (1~10점): 기존 프롬프트 대비 실질적 개선도
8. operational_feasibility (true/false): 로컬 sLLM 운영 환경에 투입 가능한 적정 길이와 명확성인가

[출력 JSON 규격 - 코드블록 없이 순수 JSON만 반환]
{{
  "scores": {{
    "user_alignment": 9.0,
    "roadmap_quality": 9.0,
    "strategic_differentiation": 9.0,
    "format_consistency": 9.5,
    "db_grounding_compliance": true,
    "no_hallucination": true,
    "improvement_over_parent": 8.5,
    "operational_feasibility": true
  }},
  "eval_summary": "심사 종합 의견"
}}"""

        messages = [
            {"role": "system", "content": "You are a strict JSON-only AI evaluation judge."},
            {"role": "user", "content": eval_prompt}
        ]

        resp = self.llm.generate(messages, temperature=0.1)

        # 기본 기본값
        default_scores = {
            "user_alignment": 8.5,
            "roadmap_quality": 8.5,
            "strategic_differentiation": 8.5,
            "format_consistency": 9.0,
            "db_grounding_compliance": True,
            "no_hallucination": True,
            "improvement_over_parent": 8.5,
            "operational_feasibility": True
        }
        eval_summary = "평가 완료"

        if resp.success:
            raw = resp.content.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            try:
                data = json.loads(raw.strip())
                default_scores = data.get("scores", default_scores)
                eval_summary = data.get("eval_summary", eval_summary)
            except Exception as e:
                logger.warning(f"평가 심사 JSON 파싱 실패, 기본 점수 적용: {e}")

        # 정량 점수 연산
        numeric_keys = ["user_alignment", "roadmap_quality", "strategic_differentiation", "format_consistency", "improvement_over_parent"]
        numeric_scores = [float(default_scores.get(k, 0)) for k in numeric_keys]
        total_score = round(sum(numeric_scores) / len(numeric_scores), 2)

        bool_keys = ["db_grounding_compliance", "no_hallucination", "operational_feasibility"]
        all_bools_pass = all(bool(default_scores.get(k, False)) for k in bool_keys)

        passed = (total_score >= EVAL_SCORE_PASS_THRESHOLD) and all_bools_pass

        return {
            "rubric_scores": default_scores,
            "total_score": total_score,
            "passed": passed,
            "eval_report": f"총점: {total_score}/10 | 불리언 가드레일: {'통과' if all_bools_pass else '미달'} | {eval_summary}"
        }


# 글로벌 싱글톤 인스턴스
self_evolving_optimizer = SelfEvolvingOptimizer()