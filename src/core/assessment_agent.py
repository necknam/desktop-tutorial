# =====================================================================
# FILE: src/core/assessment_agent.py
# =====================================================================

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.core.llm_adapter import llm_adapter
from src.core.assessment_service import assessment_service
from src.core.selection_service import selection_service

logger = logging.getLogger("ASSESSMENT_AGENT")


class AssessmentAgent:
    """
    DB 세션 기반 7단계 역량 진단 및 성장 비교 전문 에이전트.
    """

    @staticmethod
    def is_generation_trigger(text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        patterns = [
            r"계획\s*생성",
            r"3\s*개?\s*안\s*(제안|생성)",
            r"로드맵\s*(도출|생성|제안)",
            r"확정.*(진행|생성)",
        ]
        return any(re.search(p, text.strip()) for p in patterns)

    def generate_draft_plan(
        self,
        project_name: str,
        user_profile: Dict[str, Any],
        initial_prompt: str,
        uploaded_doc_text: Optional[str] = None
    ) -> Dict[str, Any]:
        file_ctx = f"\n[참조 기술 문서]\n{uploaded_doc_text[:1800]}\n" if uploaded_doc_text else ""
        prompt = f"""당신은 수석 엔터프라이즈 시스템 아키텍트입니다.
엔지니어의 프로젝트 요구사항을 분석하여 1단계 프로젝트 초안 계획(Draft Plan)을 마크다운으로 작성하세요.

[프로젝트명]: {project_name}
[엔지니어 직무]: {user_profile.get('job_title', '개발자')} ({user_profile.get('grade', '선임')})
[목표 기술 스택]: {', '.join(user_profile.get('target_skills', []))}
[초기 과제 요구사항]: {initial_prompt}
{file_ctx}

[작성 항목]
1. Core Engineering Objective (핵심 엔지니어링 목표 2~3줄)
2. Architectural Scope & Target Stack (아키텍처 범위 및 구체 기술 스택)
3. Preliminary Phase Milestones (Phase 1, 2, 3 단계별 마일스톤)
4. Critical Technical Bottlenecks (해결해야 할 3대 기술 병목 이슈)

마크다운 형식으로만 군더더기 없이 출력하세요."""

        resp = llm_adapter.generate([{"role": "user", "content": prompt}], temperature=0.2)
        raw_draft = resp.content.strip() if resp.success else "기본 초안 계획 생성 실패"

        return {
            "draft_plan": raw_draft,
            "project_goal": f"{project_name} 완수를 위한 아키텍처 구축",
            "project_scope": ", ".join(user_profile.get("target_skills", [])),
            "tech_stack": user_profile.get("target_skills", []),
            "milestones": ["Phase 1: 기반 설계", "Phase 2: 핵심 비동기 구현", "Phase 3: CI/CD 배포"],
            "key_issues": ["DB 커넥션 풀 고갈", "이벤트 루프 블로킹 지연"],
            "deliverables": ["비동기 API 서비스", "컨테이너 배포 파이프라인"],
            "validation_criteria": ["통합 부하 테스트 TPS 2배 달성"]
        }

    def process_dialogue(self, session_id: str, user_message: str) -> str:
        plan = assessment_service.get_project_plan(session_id)
        draft_plan = plan.get("draft_plan", "") if plan else ""
        history = assessment_service.get_plan_conversations(session_id)

        chat_messages = [
            {
                "role": "system",
                "content": f"""당신은 수석 테크니컬 리드입니다.
초안 계획({draft_plan[:350]}...)을 바탕으로 엔지니어의 질문에 기술적 트레이드오프(성능, 격리수준, 캐싱, 장애격리)를 중심으로 4문장 이내로 답변하세요.
절대로 문서 전체를 반복 재출력하지 마세요."""
            }
        ]
        for h in history[-6:]:
            chat_messages.append({"role": h["role"], "content": h["content"]})
        chat_messages.append({"role": "user", "content": user_message})

        resp = llm_adapter.generate(chat_messages, temperature=0.3)
        return resp.content if resp.success else "세부 기술 사양을 구체화해 주시겠습니까?"

    def finalize_refined_plan(self, session_id: str) -> Dict[str, Any]:
        plan = assessment_service.get_project_plan(session_id)
        draft_plan = plan.get("draft_plan", "") if plan else ""
        conversations = assessment_service.get_plan_conversations(session_id)
        conv_text = "\n".join([f"{c['role'].upper()}: {c['content']}" for c in conversations])

        prompt = f"""당신은 엔터프라이즈 리드 아키텍트입니다.
1단계 초안과 2단계 기술 협의 대화 전체를 통합하여 정규화된 최종 프로젝트 계획 데이터를 JSON으로 생성하세요.

[1단계 초안]
{draft_plan}

[2단계 대화 로그]
{conv_text if conv_text else "별도 협의 없음"}

[출력 JSON 규격]
{{
  "refined_plan": "확정 계획 마크다운 전문",
  "project_goal": "정제된 최종 목표 1~2줄",
  "project_scope": "확정 기술 범위",
  "tech_stack": ["기술1", "기술2", "기술3"],
  "milestones": [
    {{"phase": 1, "name": "기반 구축", "goal": "아키텍처 셋업"}},
    {{"phase": 2, "name": "핵심 구현", "goal": "비즈니스 로직 완성"}},
    {{"phase": 3, "name": "안정화", "goal": "검증 및 배포"}}
  ],
  "key_issues": ["이슈1", "이슈2"],
  "deliverables": ["산출물1", "산출물2"],
  "validation_criteria": ["검증기준1", "검증기준2"]
}}"""

        resp = llm_adapter.generate([{"role": "user", "content": prompt}], temperature=0.2)
        parsed = self._extract_json_object(resp.content)
        if not parsed:
            parsed = {
                "refined_plan": draft_plan,
                "project_goal": plan.get("project_goal", ""),
                "project_scope": plan.get("project_scope", ""),
                "tech_stack": plan.get("tech_stack", []),
                "milestones": plan.get("milestones", []),
                "key_issues": plan.get("key_issues", []),
                "deliverables": plan.get("deliverables", []),
                "validation_criteria": plan.get("validation_criteria", [])
            }
        return parsed

    def generate_pre_assessment(self, session_id: str) -> List[Dict[str, Any]]:
        """[Stage 3] 확정 계획 기반 5대 핵심 기술 축 BARS 사전 평가 문항 생성"""
        plan = assessment_service.get_project_plan(session_id)
        tech_stack = plan.get("tech_stack", []) if plan else ["FastAPI", "PostgreSQL", "Docker", "Redis", "CI/CD"]
        refined_plan = plan.get("refined_plan", "") if plan else ""

        skills = tech_stack[:5] if len(tech_stack) >= 3 else (tech_stack + ["Docker", "CI/CD"])[:5]

        prompt = f"""프로젝트 계획서를 기반으로 엔지니어의 초기 실무 역량을 진단하는 5개 BARS 사전 평가 문항을 작성하세요.
반드시 아래 5개 기술 스택에 대해 각 1문항씩 생성하세요:
[평가 기술 스택]: {', '.join(skills)}

[계획서 요약]: {refined_plan[:1000]}

[규칙]
1. 각 문항의 "skill" 필드는 위 목록의 명칭과 정확히 일치해야 합니다.
2. 각 문항은 4개의 비라벨링 행동 지표 옵션(옵션 0: 초급 ~ 옵션 3: 전문가)을 가집니다.
3. 옵션 텍스트 안에 점수, 레벨 번호를 절대로 표기하지 마세요.
4. JSON Array 형식만 반환하세요.

[출력 형식]
[
  {{
    "skill": "{skills[0]}",
    "question": "{skills[0]} 실무 구현 및 성능 최적화 경험 수준은 어떠합니까?",
    "options": [
      "기본 개념은 이해하나 프로덕션 구현 경험 없음",
      "보일러플레이트와 레퍼런스를 참고하여 기본 기능 구현 가능",
      "독립적으로 프로덕션 레벨의 비즈니스 로직과 예외 처리를 설계/구현 가능",
      "대규모 트래픽 병목을 진단하고 아키텍처 최적화 및 튜닝을 리드 가능"
    ],
    "sequence_no": 1
  }}
]"""

        resp = llm_adapter.generate([{"role": "user", "content": prompt}], temperature=0.2)
        parsed = self._extract_json_array(resp.content)

        if not parsed or len(parsed) < 3:
            parsed = self._fallback_pre_questions(skills)

        # 역량 축 명칭 강제 보정
        for idx, item in enumerate(parsed):
            if idx < len(skills):
                item["skill"] = skills[idx]
            item["sequence_no"] = idx + 1

        return parsed

    def generate_post_assessment(self, session_id: str) -> List[Dict[str, Any]]:
        """[Stage 6] 사전 평가 역량 축과 1:1로 정확히 일치하는 동형 사후 평가 문항 생성"""
        pre_data = assessment_service.get_assessment(session_id, "PRE")
        pre_questions = pre_data.get("questions", [])
        selected_info = selection_service.get_selected_roadmap(session_id)
        roadmap_title = selected_info.get("generation", {}).get("strategy_title", "기술 로드맵") if selected_info else "기술 로드맵"

        target_skills = [q.get("skill", q.get("skill_name")) for q in pre_questions]
        if not target_skills:
            target_skills = ["FastAPI", "PostgreSQL", "Docker", "Redis", "CI/CD"]

        prompt = f"""엔지니어가 로드맵({roadmap_title})을 완수한 후 실무 역량이 얼마나 향상되었는지 검증하는 사후 평가 문항을 생성하세요.
[사전 평가 기술 축]: {json.dumps(target_skills, ensure_ascii=False)}

[작성 규칙]
1. 문항 수는 사전 평가와 동일하게 {len(target_skills)}개로 구성합니다.
2. 각 문항의 "skill" 필드는 반드시 사전 평가 기술 축({', '.join(target_skills)})과 토시 하나 틀리지 않고 100% 일치해야 합니다!
3. 문항 1부터 {len(target_skills)}까지 각 기술을 로드맵 수행 후 실제 코드베이스에 어떻게 적용했는지 묻는 4지선다 행동 지표(BEHAVIORAL_4)로 작성하세요.
   - 옵션 0: 개념은 학습했으나 여전히 지원이 필요함 (1점 수준)
   - 옵션 1: 기존 패턴을 모방하여 단독 구현에 성공함 (2점 수준)
   - 옵션 2: 프로덕션 기능으로 주도적 개발 및 테스트를 완결함 (3점 수준)
   - 옵션 3: 아키텍처 설계를 리드하고 팀 코드 리뷰 및 성능 최적화를 달성함 (4점 수준)
4. JSON Array 형식만 출력하세요."""

        resp = llm_adapter.generate([{"role": "user", "content": prompt}], temperature=0.2)
        parsed = self._extract_json_array(resp.content)

        if not parsed or len(parsed) < 3:
            parsed = self._fallback_post_questions(target_skills)

        # 사전 평가와 1:1 완벽 일치 강제 바인딩 (키 불일치 원천 차단)
        for idx, item in enumerate(parsed):
            if idx < len(target_skills):
                item["skill"] = target_skills[idx]
            item["sequence_no"] = idx + 1
            item["question_type"] = "BEHAVIORAL_4"

        # 마지막에 정성적 산출물 서술형 문항 1건 추가
        parsed.append({
            "skill": "최종 아키텍처 성과물",
            "question": "본 로드맵을 수행하며 실제 머지한 PR 번호, 비동기 전환 성능 개선 수치, 또는 핵심 기술 산출물을 구체적으로 기술하세요.",
            "options": [],
            "sequence_no": len(parsed) + 1,
            "question_type": "TEXT"
        })

        return parsed

    def generate_growth_report(self, session_id: str) -> Dict[str, Any]:
        """[Stage 7] 사전 및 사후 응답을 다중 안전 매칭하여 실질적 성장 델타(Delta) 산출"""
        pre_data = assessment_service.get_assessment(session_id, "PRE")
        post_data = assessment_service.get_assessment(session_id, "POST")
        selected_info = selection_service.get_selected_roadmap(session_id)

        pre_responses = pre_data.get("responses", [])
        post_responses = post_data.get("responses", [])

        # 서술형 텍스트 산출물 추출
        qualitative_text = "프로덕션 코드 기여 및 테스트 완결"
        for pr in post_responses:
            if pr.get("skill") == "최종 아키텍처 성과물" or not pr.get("options"):
                if pr.get("selected_option") and pr.get("selected_option") != "기여 완료":
                    qualitative_text = pr.get("selected_option")

        # 4단계 안전 결합 매칭 알고리즘
        skill_comparisons = []
        used_post_indices = set()

        for idx, pre_item in enumerate(pre_responses):
            pre_skill = str(pre_item.get("skill", pre_item.get("skill_name", ""))).strip()
            pre_score = int(pre_item.get("score", 1))
            pre_stmt = str(pre_item.get("selected_option", ""))

            matched_post = None

            # 1단계: 정확한 스킬명 일치
            for p_idx, post_item in enumerate(post_responses):
                if p_idx in used_post_indices:
                    continue
                post_skill = str(post_item.get("skill", post_item.get("skill_name", ""))).strip()
                if post_skill.lower() == pre_skill.lower() and post_skill != "최종 아키텍처 성과물":
                    matched_post = post_item
                    used_post_indices.add(p_idx)
                    break

            # 2단계: 부분 문자열 포함 일치
            if not matched_post:
                for p_idx, post_item in enumerate(post_responses):
                    if p_idx in used_post_indices:
                        continue
                    post_skill = str(post_item.get("skill", post_item.get("skill_name", ""))).strip()
                    if post_skill != "최종 아키텍처 성과물" and (pre_skill.lower() in post_skill.lower() or post_skill.lower() in pre_skill.lower()):
                        matched_post = post_item
                        used_post_indices.add(p_idx)
                        break

            # 3단계: 순서 인덱스 폴백 매칭
            if not matched_post and idx < len(post_responses):
                cand = post_responses[idx]
                if cand.get("skill") != "최종 아키텍처 성과물" and idx not in used_post_indices:
                    matched_post = cand
                    used_post_indices.add(idx)

            # 점수 및 성장 델타 계산
            if matched_post:
                post_score = int(matched_post.get("score", pre_score))
                post_stmt = str(matched_post.get("selected_option", ""))
            else:
                post_score = pre_score
                post_stmt = pre_stmt

            growth_delta = post_score - pre_score

            skill_comparisons.append({
                "skill": pre_skill,
                "pre_level": pre_score,
                "post_level": post_score,
                "growth": growth_delta,
                "pre_statement": pre_stmt,
                "post_statement": post_stmt
            })

        # LLM 정성 평가 합성
        prompt = f"""엔지니어의 사전/사후 직무 역량 성장 데이터를 분석하여 최종 인사이트 보고서를 작성하세요.
[스킬별 성장 지표]: {json.dumps(skill_comparisons, ensure_ascii=False)}
[엔지니어 서술 성과]: {qualitative_text}
[수행 로드맵]: {selected_info.get('generation', {}).get('strategy_title', '맞춤형 기술 로드맵') if selected_info else ''}

[출력 JSON 규격]
{{
  "overall_summary": "엔지니어의 성장 추이에 대한 종합 평가 narrative (3문단 구성)",
  "strengths": ["실질적으로 크게 성장한 핵심 역량 1", "핵심 역량 2"],
  "improvement_areas": ["성장폭이 정체되었거나 추가 학습이 필요한 리스크 영역 1"],
  "final_evaluation": "로드맵 완수 및 역량 획득 최종 인증 소견"
}}"""

        resp = llm_adapter.generate([{"role": "user", "content": prompt}], temperature=0.2)
        report_json = self._extract_json_object(resp.content)
        if not report_json:
            report_json = {
                "overall_summary": "로드맵을 성공적으로 수행하여 주요 백엔드 아키텍처 기술 축 전반에서 뚜렷한 역량 향상을 달성했습니다.",
                "strengths": [f"{item['skill']} 역량 향상 (+{item['growth']}단계)" for item in skill_comparisons if item['growth'] > 0] or ["실무 개발 능력 전반 향상"],
                "improvement_areas": [f"{item['skill']} 심화 숙련도 보완 필요" for item in skill_comparisons if item['growth'] <= 0] or ["고가용성 분산 환경 지속 학습 권장"],
                "final_evaluation": "목표 프로젝트 독립 수행 가능 수준 도달 인증"
            }

        report_json["skill_comparison"] = skill_comparisons
        report_json["qualitative_artifact"] = qualitative_text
        return report_json

    @staticmethod
    def _extract_json_array(text: str) -> List[Dict[str, Any]]:
        raw = text.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        try:
            val = json.loads(raw.strip())
            return val if isinstance(val, list) else []
        except Exception:
            return []

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        raw = text.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        try:
            val = json.loads(raw.strip())
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _fallback_pre_questions(skills: List[str]) -> List[Dict[str, Any]]:
        target = skills[:5] if skills else ["FastAPI", "PostgreSQL", "Docker", "Redis", "CI/CD"]
        return [
            {
                "skill": sk,
                "question": f"본 프로젝트 맥락에서 {sk} 실무 구현 및 최적화 경험은 어느 수준입니까?",
                "options": [
                    f"{sk} 기본 개념은 이해하나 실무 프로젝트 경험 없음",
                    f"기존 레퍼런스를 참고하여 표준 기능 구현 가능",
                    f"독립적으로 프로덕션 로직을 설계하고 예외 처리를 완결할 수 있음",
                    f"고가용성 구조를 설계하고 성능 최적화를 주도할 수 있음"
                ],
                "sequence_no": idx
            }
            for idx, sk in enumerate(target, 1)
        ]

    @staticmethod
    def _fallback_post_questions(skills: List[str]) -> List[Dict[str, Any]]:
        return [
            {
                "skill": sk,
                "question": f"로드맵 수행 후 실제 코드베이스에서 {sk}를 어떻게 적용했습니까?",
                "options": [
                    f"개념은 숙지했으나 실무 코드 작성 시 동료의 지원을 받음",
                    f"기존 패턴을 응용하여 할당된 서브루틴을 단독 구현함",
                    f"프로덕션 엔드포인트를 주도적으로 개발하고 테스트를 통과시킴",
                    f"아키텍처 패턴을 수립하고 동료 코드 리뷰 및 성능 최적화를 리드함"
                ],
                "sequence_no": idx,
                "question_type": "BEHAVIORAL_4"
            }
            for idx, sk in enumerate(skills, 1)
        ]


assessment_agent = AssessmentAgent()
__all__ = ["AssessmentAgent", "assessment_agent"]