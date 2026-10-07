import json
import logging
from typing import Any, Dict, List
from core.llm_adapter import llm_adapter

logger = logging.getLogger("ASSESSMENT_AGENT")


class AssessmentAgent:
    """
    역량 진단·평가 에이전트 (Assessment Agent)
    1. 사전 대화를 통한 프로젝트 요구사항 및 실무 계획 구체화
    2. 로드맵 착수 전 현재 실력 측정을 위한 '사전 진단 설문(Pre-Assessment)' 제작
    3. 로드맵 이수 후 실질적 역량 성장을 검증하기 위한 '사후 평가 설문(Post-Assessment)' 제작
    4. 로드맵 생성 명령 트리거 감지 및 오케스트레이터 연계
    """

    def __init__(self) -> None:
        self.trigger_keywords = [
            "계획 생성", "로드맵 생성", "플랜 생성", "3안 제안",
            "로드맵 만들어", "계획 짜줘", "로드맵 작성", "플랜 작성", "생성해줘"
        ]

    def is_generation_trigger(self, user_message: str) -> bool:
        """사용자 발화 내 로드맵 생성 트리거 키워드 감지"""
        if not user_message:
            return False
        clean_text = user_message.strip()
        return any(kw in clean_text for kw in self.trigger_keywords)

    def process_dialogue(
            self,
            user_message: str,
            user_profile: Dict[str, Any],
            conversation_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        사용자 발화 분석 및 티키타카 멘토링
        반환값: {"intent": "GENERATE_ROADMAP" | "CONSULTING", "reply_text": str}
        """
        if self.is_generation_trigger(user_message):
            logger.info("역량진단 에이전트: 로드맵 생성 트리거 감지")
            return {
                "intent": "GENERATE_ROADMAP",
                "reply_text": "지금까지 나눈 대화와 요구사항을 바탕으로 3대 전략 로드맵을 도출합니다.",
            }

        logger.info("역량진단 에이전트: 요구사항 구체화 및 진단 멘토링 수행")
        consulting_prompt = f"""당신은 부서원의 업무 역량 개발과 프로젝트 성공을 지원하는 시니어 테크 리드이자 역량 진단 전문가(Assessment Agent)입니다.

현재 부서원 프로필:
- 성명/직급: {user_profile.get('profile_name', '부서원')} ({user_profile.get('grade', '실무자')})
- 담당 직무: {user_profile.get('job_title', 'IT 개발')}
- 수행 프로젝트 과제: {user_profile.get('work_context_summary', '현업 프로젝트 완수')}
- 목표 스킬: {', '.join(user_profile.get('target_skills', []))}

[행동 지침]
1. 부서원의 기술적 고민과 현재 역량 수준을 파악하기 위해 실무 중심의 구체적인 질문과 피드백을 제시하세요.
2. 장황한 원론적 설명보다는 현업에서 자주 겪는 병목(동시성 이슈, DB 인덱싱, 배포 파이프라인 등)을 짚어주며 대화를 이끌어주세요.
3. 답변 마지막에는 언제든 준비가 되었을 때 **'계획 생성'**이라고 말씀하시거나 하단의 생성 버튼을 눌러 로드맵과 진단 설문을 확인하실 수 있다고 안내하세요."""

        chat_messages = [{"role": "system", "content": consulting_prompt}]
        for m in conversation_history[-6:]:
            chat_messages.append({"role": m["role"], "content": m["content"]})

        llm_reply = llm_adapter.generate(chat_messages, temperature=0.5)
        reply_text = (
            llm_reply.content
            if llm_reply.success
            else "프로젝트 관련 요구사항을 충분히 나누신 후 '계획 생성'을 요청해 주세요."
        )

        return {
            "intent": "CONSULTING",
            "reply_text": reply_text,
        }

    def generate_assessment_survey(
            self,
            survey_type: str,
            user_profile: Dict[str, Any],
            roadmap_summary: str = "",
    ) -> List[Dict[str, Any]]:
        """
        부서원의 현재 실력(Pre) 또는 이수 후 성장(Post)을 측정하기 위한 5문항 진단 설문 제작
        survey_type: 'PRE' (사전 역량 진단) | 'POST' (사후 성장 검증)
        """
        logger.info(f"역량진단 에이전트: {survey_type} 평가 설문 제작 시작")

        survey_desc = (
            "학습 시작 전 현재 실무 지식 수준과 기술적 준비도를 측정하는 사전 진단 설문"
            if survey_type.upper() == "PRE"
            else "로드맵 이수 후 실제 프로젝트 산출물 변화와 역량 향상도를 검증하는 사후 성장 측정 설문"
        )

        prompt = f"""당신은 IT 기술 교육 및 역량 평가 설계 전문가입니다.
아래 부서원의 프로필과 로드맵 요약을 기반으로, {survey_desc}을 작성하세요.

[대상 정보]
- 직무: {user_profile.get('job_title', 'IT 개발')}
- 당면 과제: {user_profile.get('work_context_summary', '')}
- 목표 스킬: {', '.join(user_profile.get('target_skills', []))}
- 로드맵 요약: {roadmap_summary}

[출력 요구사항]
1. 총 5개의 문항을 객관식(5점 리커트 척도) 및 실무 주관식 점검 문항으로 구성하세요.
2. 단순 만족도 조사가 아니라, 실제 코드베이스 설계, 트러블슈팅, 아키텍처 이해도를 묻는 구체적인 엔지니어링 질문이어야 합니다.
3. 반드시 아래 JSON 규격으로만 응답하고 마크다운 코드블록은 제외하세요.

[JSON 규격]
[
  {{
    "q_num": 1,
    "question": "문항 내용",
    "evaluation_metric": "측정하려는 역량 지표",
    "question_type": "SCALE_5"
  }},
  {{
    "q_num": 5,
    "question": "실무 적용 주관식 질문",
    "evaluation_metric": "실무 적용성",
    "question_type": "TEXT"
  }}
]
"""
        messages = [
            {"role": "system", "content": "오직 유효한 JSON 배열만 출력하세요."},
            {"role": "user", "content": prompt},
        ]
        resp = llm_adapter.generate(messages, temperature=0.3)

        raw = resp.content.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except Exception as exc:
            logger.warning(f"설문 JSON 파싱 실패, 기본 템플릿 반환: {exc}")
            target_skills = ", ".join(user_profile.get("target_skills", ["IT 기술"]))
            return [
                {"q_num": 1, "question": f"{target_skills} 기반의 설계 원리를 독립적으로 구현할 수 있는가?",
                 "evaluation_metric": "기초 설계 역량", "question_type": "SCALE_5"},
                {"q_num": 2, "question": "비동기 I/O 및 데이터베이스 커넥션 풀 최적화 기법을 프로젝트에 적용할 수 있는가?",
                 "evaluation_metric": "성능 최적화", "question_type": "SCALE_5"},
                {"q_num": 3, "question": "컨테이너 가상화 및 클라우드 배포 파이프라인 장애 시 트러블슈팅이 가능한가?", "evaluation_metric": "운영 안정성",
                 "question_type": "SCALE_5"},
                {"q_num": 4, "question": "테스트 주도 개발(TDD) 또는 정적 코드 검증을 빌드에 연계해 보았는가?", "evaluation_metric": "코드 품질",
                 "question_type": "SCALE_5"},
                {"q_num": 5, "question": "본 과정을 통해 현업 코드베이스에 가장 먼저 리팩토링하고 싶은 핵심 과제는 무엇인가?",
                 "evaluation_metric": "실무 적용 과제", "question_type": "TEXT"},
            ]


assessment_agent = AssessmentAgent()
__all__ = ["AssessmentAgent", "assessment_agent"]