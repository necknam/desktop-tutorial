import json
import logging
import urllib.parse
from typing import Any, Dict, List
import streamlit as st

# 코어 서비스 싱글톤 인스턴스 참조 (의존성 결합 제거)
from database.db_client import db_client
from core.llm_adapter import DISCONNECTED_STANDARD_MESSAGE, llm_adapter
from core.real_data_ingestor import real_data_ingestor
from core.recommendation_service import recommendation_service
from core.roadmap_agent import multi_agent_orchestrator
from core.selection_service import selection_service
import os


# Streamlit Cloud 배포 환경의 Secrets를 os.environ으로 자동 동기화
if hasattr(st, "secrets"):
    for key, val in st.secrets.items():
        if isinstance(val, str):
            os.environ[key] = val

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("HR_ROADMAP_APP")

# 1. 페이지 설정
st.set_page_config(
    page_title="부서원 프로젝트 성공 업무개발 로드맵",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 2. 커스텀 CSS
st.markdown(
    """
<style>
.main-title { font-size: 26px; font-weight: 700; color: #0F172A; margin-bottom: 4px; }
.sub-title { font-size: 14px; color: #475569; margin-bottom: 16px; }
.project-banner {
    background: linear-gradient(90deg, #F0FDF4 0%, #E0F2FE 100%);
    border-left: 5px solid #0EA5E9;
    padding: 14px 18px;
    border-radius: 8px;
    margin-bottom: 18px;
    font-size: 14px;
    color: #0369A1;
}
.strategy-card {
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 18px;
    background-color: #FFFFFF;
    box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    margin-bottom: 12px;
    height: 100%;
}
.badge-practical { background-color: #DBEAFE; color: #1E40AF; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.badge-certified { background-color: #DCFCE7; color: #166534; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.badge-fasttrack { background-color: #FEF3C7; color: #92400E; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.milestone-container {
    border-left: 4px solid #3882F6;
    padding: 16px 20px;
    margin-bottom: 18px;
    background-color: #F8FAFC;
    border-radius: 0 8px 8px 0;
}
.related-book-tag {
    display: inline-block;
    background-color: #FEF9C3;
    color: #854D0E;
    font-size: 11.5px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    margin-top: 4px;
    border: 1px solid #FDE047;
}
</style>
""",
    unsafe_allow_html=True,
)


def resolve_course_safe_url(raw_url: str, title: str, platform: str) -> str:
    """UI 레벨 404 방지 안전 URL 획득 함수"""
    try:
        return recommendation_service.normalize_course_url(raw_url, title, platform)
    except Exception:
        clean_kw = urllib.parse.quote(str(title).split()[0] if title else "개발")
        p_upper = str(platform).upper()
        if "MS" in p_upper or "MICROSOFT" in p_upper:
            return f"https://learn.microsoft.com/ko-kr/training/browse/?terms={clean_kw}"
        return f"https://www.kmooc.kr/search?query={clean_kw}"


# 3. 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role": "assistant",
        "content": (
            "안녕하세요! 부서원님이 현재 맡으신 프로젝트를 성공적으로 완수할 수 있도록 돕는 "
            "업무 개발(CDP) 상담 챗봇입니다.\n\n현재 프로젝트 진행 상황, 겪고 계신 기술적 어려움 등을 "
            "편하게 말씀해 주세요. 대화를 통해 계획을 구체화한 후 **'계획 생성'**을 요청하시면 "
            "최적의 3대 전략 로드맵을 제안해 드립니다."
        ),
    }]
if "current_request_id" not in st.session_state:
    st.session_state.current_request_id = None
if "orchestration_results" not in st.session_state:
    st.session_state.orchestration_results = None
if "selected_agent_id" not in st.session_state:
    st.session_state.selected_agent_id = None
if "grounding_context" not in st.session_state:
    st.session_state.grounding_context = {}

# 4. 헤더 및 우측 상단 실데이터 DB 적재 액션
col_title, col_sync = st.columns([4, 1.3])
with col_title:
    st.markdown('<div class="main-title">부서원 프로젝트 성공 지원 업무 개발(CDP) 로드맵</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">대화로 요구사항을 구체화한 뒤 실무 프로젝트형 · 이론/자격증형 · 단기 패스트트랙형 3개 전략을 비교 채택합니다.</div>', unsafe_allow_html=True)

with col_sync:
    st.write("")
    if st.button("실데이터 DB 적재", type="primary", use_container_width=True, help="기존 가상 데이터를 비우고 실제 공식 API 및 공공데이터를 수집 적재합니다."):
        with st.status("실데이터 DB 적재 파이프라인 가동 중...", expanded=True) as status:
            st.write("기존 스킬 관련 테이블 초기화 (TRUNCATE/DELETE)...")
            st.write("MS Learn / K-MOOC 공식 강좌, 자격증, KDC 004 도서 수집...")
            ingest_result = real_data_ingestor.execute_full_pipeline()
            if ingest_result.get("success"):
                st.write(f"강좌 {ingest_result['courses_count']}건, 자격증 {ingest_result['certs_count']}건, 도서 {ingest_result['books_count']}건 적재 완료!")
                status.update(label="실데이터 DB 적재 완료!", state="complete", expanded=False)
                st.toast("실제 공공/공식 데이터가 DB에 성공적으로 적재되었습니다!", icon="✅")
                st.rerun()
            else:
                status.update(label="적재 실패", state="error", expanded=False)
                st.error(f"오류: {ingest_result.get('message')}")

# 5. 사이드바: 모의 프로필 및 시스템 헬스
with st.sidebar:
    st.header("프로젝트 및 업무 환경 설정")
    user_mode = st.radio(
        "데이터 수집 모드",
        options=["REAL_USER", "TEST_MANUAL"],
        format_func=lambda x: "실운영 모드 (REAL_USER)" if x == "REAL_USER" else "수동 테스트 모드 (TEST_MANUAL)",
    )
    st.markdown("---")
    st.subheader("부서원 인사 프로필")
    mock_profiles = db_client.fetch_mock_profiles()
    profile_options = {p["profile_name"]: p for p in mock_profiles}
    selected_preset_name = st.selectbox("테스트용 프로필 선택", options=["직접 입력"] + list(profile_options.keys()))

    if selected_preset_name != "직접 입력" and selected_preset_name in profile_options:
        preset = profile_options[selected_preset_name]
        default_name = preset.get("profile_name", "")
        default_dept = preset.get("department", "")
        default_grade = preset.get("grade", "")
        default_job = preset.get("job_title", "")
        default_years = int(preset.get("career_years", 3))
        default_curr_skills = ", ".join(preset.get("current_skills", []))
        default_target_skills = ", ".join(preset.get("target_skills", []))
        default_context = preset.get("work_context_summary", "")
    else:
        default_name = "김백엔"
        default_dept = "플랫폼개발1팀"
        default_grade = "선임연구원 (사원)"
        default_job = "백엔드 소프트웨어 엔지니어"
        default_years = 2
        default_curr_skills = "Python, Git/GitHub"
        default_target_skills = "FastAPI, PostgreSQL, Docker"
        default_context = "레거시 시스템의 API 마이크로서비스 전환 프로젝트 성공적 완수 및 비동기 처리 도입"

    emp_name = st.text_input("부서원 성명", value=default_name)
    emp_dept = st.text_input("소속 부서", value=default_dept)
    emp_grade = st.text_input("직급", value=default_grade)
    emp_job = st.text_input("담당 직무", value=default_job)
    emp_years = st.number_input("경력(연차)", min_value=0, max_value=40, value=default_years)
    emp_curr_skills = st.text_input("현재 보유 스킬", value=default_curr_skills)
    emp_target_skills = st.text_input("프로젝트 요구 스킬 (목표)", value=default_target_skills)
    emp_context = st.text_area("수행 중인 프로젝트 및 현업 과제", value=default_context, height=90)

    user_profile_payload = {
        "profile_name": emp_name,
        "department": emp_dept,
        "grade": emp_grade,
        "job_title": emp_job,
        "career_years": emp_years,
        "current_skills": [s.strip() for s in emp_curr_skills.split(",") if s.strip()],
        "target_skills": [s.strip() for s in emp_target_skills.split(",") if s.strip()],
        "work_context_summary": emp_context,
    }

# 6. 프로젝트 안내 배너
st.markdown(
    f"""
<div class="project-banner">
<strong>현재 집중 과제:</strong> {emp_name} {emp_grade}님의 <em>[{emp_context}]</em> 완수를 위한 맞춤형 상담을 진행합니다.
</div>
""",
    unsafe_allow_html=True,
)

# 7. 대화 히스토리 출력
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])


def run_roadmap_generation():
    """누적된 대화와 접지 컨텍스트를 기반으로 3대 전략 로드맵 병렬 생성"""
    with st.spinner("DB 벡터 시밀러리티 RAG 자원 인출 및 3대 전략 에이전트 병렬 생성을 진행하고 있습니다..."):
        grounding_context = recommendation_service.retrieve_grounding_context(
            target_skills=user_profile_payload["target_skills"],
            target_job_title=user_profile_payload["job_title"],
        )
        st.session_state.grounding_context = grounding_context

        last_user_text = st.session_state.messages[-1]["content"] if st.session_state.messages else "프로젝트 로드맵 요청"
        req_id = selection_service.create_roadmap_session(
            user_type=user_mode,
            raw_user_prompt=last_user_text,
            user_profile=user_profile_payload,
            conversation_history=st.session_state.messages,
            retrieved_context=grounding_context,
        )
        st.session_state.current_request_id = req_id

        orchestration = multi_agent_orchestrator.generate_all_roadmaps(
            user_context=user_profile_payload,
            conversation_history=st.session_state.messages,
            retrieved_context=grounding_context,
        )

        gen_map = selection_service.record_agent_generations(
            request_id=req_id,
            orchestration_results=orchestration["results"],
        )
        st.session_state.orchestration_results = orchestration["results"]
        st.session_state.generation_id_map = gen_map
        st.session_state.selected_agent_id = None

        st.session_state.messages.append({
            "role": "assistant",
            "content": (
                f"{orchestration['total_latency_ms']} ms 만에 프로젝트 성공을 위한 3대 전략 로드맵 생성을 완료했습니다! "
                "Phase 1부터 Phase 3까지 모든 단계에 추천 자원이 배치되었으니 하단의 카드를 확인해 주세요."
            ),
        })


# 8. 사용자 대화 입력 및 티키타카 / 계획 생성 분기 처리
user_query = st.chat_input("프로젝트 고민을 편하게 말씀해 주세요 (로드맵 생성을 원하시면 '계획 생성' 입력)")

TRIGGER_KEYWORDS = [
    "계획 생성", "로드맵 생성", "플랜 생성", "3안 제안",
    "로드맵 만들어", "계획 짜줘", "로드맵 작성", "플랜 작성", "생성해줘"
]

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.write(user_query)

    is_trigger = any(kw in user_query for kw in TRIGGER_KEYWORDS)

    if is_trigger:
        with st.chat_message("assistant"):
            st.write("지금까지 나눈 대화와 요구사항을 바탕으로 3대 전략 로드맵을 도출합니다.")
        run_roadmap_generation()
        st.rerun()
    else:
        # 일반 티키타카 상담 모드 (LLM 피드백을 통한 요구사항 구체화)
        with st.chat_message("assistant"):
            with st.spinner("프로젝트 맥락을 분석하여 전문 피드백을 정리 중입니다..."):
                consulting_prompt = f"""당신은 부서원의 업무 역량 개발과 프로젝트 성공을 돕는 친절하고 전문적인 시니어 테크 리드입니다.
현재 부서원 프로필:
- 이름: {user_profile_payload['profile_name']} ({user_profile_payload['grade']})
- 직무: {user_profile_payload['job_title']}
- 수행 과제: {user_profile_payload['work_context_summary']}
- 목표 스킬: {user_profile_payload['target_skills']}

부서원의 질문이나 의견에 대해 전문적으로 피드백하고, 프로젝트 성공을 위해 추가로 고려할 사항(일정, 기술 우선순위, 테스트 환경 등)을 구체화하세요.
답변 마지막에는 언제든 '계획 생성'이라고 말씀하시거나 하단 생성 버튼을 누르면 3대 맞춤형 로드맵을 바로 제안해 드리겠다고 안내하세요."""

                chat_messages = [{"role": "system", "content": consulting_prompt}]
                for m in st.session_state.messages[-6:]:
                    chat_messages.append({"role": m["role"], "content": m["content"]})

                llm_reply = llm_adapter.generate(chat_messages, temperature=0.5)
                reply_text = (
                    llm_reply.content
                    if llm_reply.success
                    else "프로젝트 관련 요구사항을 충분히 나누신 후 '계획 생성'을 요청해 주세요."
                )
                st.write(reply_text)
                st.session_state.messages.append({"role": "assistant", "content": reply_text})
                st.rerun()

# 9. 원클릭 로드맵 생성 전용 바
st.markdown("<br>", unsafe_allow_html=True)
col_btn1, col_btn2 = st.columns([3, 1.2])
with col_btn1:
    st.caption("충분히 대화를 나누셨나요? 아래 버튼을 누르면 3대 전략 로드맵을 즉시 도출합니다.")
with col_btn2:
    if st.button("3대 전략 로드맵 생성하기", type="primary", use_container_width=True):
        run_roadmap_generation()
        st.rerun()

# 10. 3개 전략 대안 3단 비교 카드
if st.session_state.orchestration_results:
    st.markdown("---")
    st.markdown("### 3대 전략 비교 및 채택")
    results = st.session_state.orchestration_results
    col1, col2, col3 = st.columns(3)

    # 1안: 실무 프로젝트형
    with col1:
        st.markdown('<div class="strategy-card">', unsafe_allow_html=True)
        st.markdown('<span class="badge-practical">전략 1: 실무 프로젝트형</span>', unsafe_allow_html=True)
        res_p = results.get("agent_practical", {})
        if res_p.get("status") == "SUCCESS":
            cp = res_p.get("roadmap_content", {})
            st.subheader(cp.get("strategy_title", "실무 중심 프로젝트 완수형"))
            st.caption(f"소요 기간: {cp.get('total_duration_weeks', 8)}주 | 처리: {res_p.get('latency_ms', 0)}ms")
            st.write(f"**이 플랜을 추천하는 이유 & 요약:**\n{res_p.get('summary', '')}")
            st.info(f"**프로젝트 기술 목표:**\n{cp.get('project_goal', '프로젝트 실무 완수')}")
            if st.button("1안 실무 프로젝트형 채택", key="btn_choose_p", use_container_width=True):
                st.session_state.selected_agent_id = "agent_practical"
                selection_service.record_user_selection(
                    request_id=st.session_state.current_request_id,
                    chosen_generation_id=st.session_state.generation_id_map["agent_practical"],
                    chosen_agent_id="agent_practical",
                    user_type=user_mode,
                    selection_reason="현업 프로젝트의 즉각적인 코드베이스 구현 및 실무 과제 완수를 위해 채택함",
                )
                st.rerun()
        else:
            st.error(f"생성 실패: {res_p.get('summary', DISCONNECTED_STANDARD_MESSAGE)}")
        st.markdown('</div>', unsafe_allow_html=True)

    # 2안: 이론/공인자격형
    with col2:
        st.markdown('<div class="strategy-card">', unsafe_allow_html=True)
        st.markdown('<span class="badge-certified">전략 2: 이론/공인자격형</span>', unsafe_allow_html=True)
        res_c = results.get("agent_certified", {})
        if res_c.get("status") == "SUCCESS":
            cc = res_c.get("roadmap_content", {})
            st.subheader(cc.get("strategy_title", "공인 자격 및 이론 검증형"))
            st.caption(f"소요 기간: {cc.get('total_duration_weeks', 12)}주 | 처리: {res_c.get('latency_ms', 0)}ms")
            st.write(f"**이 플랜을 추천하는 이유 & 요약:**\n{res_c.get('summary', '')}")
            st.info(f"**프로젝트 기술 목표:**\n{cc.get('project_goal', '아키텍처 이론 및 검증')}")
            if st.button("2안 이론/자격증형 채택", key="btn_choose_c", use_container_width=True):
                st.session_state.selected_agent_id = "agent_certified"
                selection_service.record_user_selection(
                    request_id=st.session_state.current_request_id,
                    chosen_generation_id=st.session_state.generation_id_map["agent_certified"],
                    chosen_agent_id="agent_certified",
                    user_type=user_mode,
                    selection_reason="프로젝트 기술 부채 예방을 위한 표준 아키텍처 학습 및 공인 자격증 검증을 위해 채택함",
                )
                st.rerun()
        else:
            st.error(f"생성 실패: {res_c.get('summary', DISCONNECTED_STANDARD_MESSAGE)}")
        st.markdown('</div>', unsafe_allow_html=True)

    # 3안: 단기 패스트트랙형
    with col3:
        st.markdown('<div class="strategy-card">', unsafe_allow_html=True)
        st.markdown('<span class="badge-fasttrack">전략 3: 단기 패스트트랙형</span>', unsafe_allow_html=True)
        res_f = results.get("agent_fasttrack", {})
        if res_f.get("status") == "SUCCESS":
            cf = res_f.get("roadmap_content", {})
            st.subheader(cf.get("strategy_title", "단기 집중 패스트트랙형"))
            st.caption(f"소요 기간: {cf.get('total_duration_weeks', 4)}주 | 처리: {res_f.get('latency_ms', 0)}ms")
            st.write(f"**이 플랜을 추천하는 이유 & 요약:**\n{res_f.get('summary', '')}")
            st.info(f"**프로젝트 기술 목표:**\n{cf.get('project_goal', '핵심 스택 초단기 습득')}")
            if st.button("3안 단기 패스트트랙형 채택", key="btn_choose_f", use_container_width=True):
                st.session_state.selected_agent_id = "agent_fasttrack"
                selection_service.record_user_selection(
                    request_id=st.session_state.current_request_id,
                    chosen_generation_id=st.session_state.generation_id_map["agent_fasttrack"],
                    chosen_agent_id="agent_fasttrack",
                    user_type=user_mode,
                    selection_reason="프로젝트 런칭 일정 준수를 위해 학습 시간을 최소화하고 현업에 즉시 투입하기 위해 채택함",
                )
                st.rerun()
        else:
            st.error(f"생성 실패: {res_f.get('summary', DISCONNECTED_STANDARD_MESSAGE)}")
        st.markdown('</div>', unsafe_allow_html=True)

    # 11. 최종 채택된 로드맵의 마크다운 표(Table) 요약 및 세부 구체화 뷰
    if st.session_state.selected_agent_id and st.session_state.orchestration_results:
        chosen_id = st.session_state.selected_agent_id
        chosen_data = st.session_state.orchestration_results[chosen_id]
        chosen_content = chosen_data.get("roadmap_content", {})

        agent_names = {
            "agent_practical": "1안: 실무 프로젝트형",
            "agent_certified": "2안: 이론/공인자격형",
            "agent_fasttrack": "3안: 단기 패스트트랙형",
        }

        grounding_courses_map = {c["course_id"]: c for c in st.session_state.grounding_context.get("courses", [])}
        grounding_books_map = {b["isbn"]: b for b in st.session_state.grounding_context.get("books", [])}

        st.markdown("---")
        st.success(f"**{agent_names.get(chosen_id, chosen_id)}** 로드맵이 채택되었습니다!")
        st.markdown(f"## [프로젝트 성공 지원 종합 리포트] {chosen_content.get('strategy_title', '업무 개발 로드맵')}")
        st.markdown(f"**대상 부서원:** {emp_name} {emp_grade} ({emp_dept}) | **목표 직무:** {emp_job} | **총 소요기간:** {chosen_content.get('total_duration_weeks', 8)}주")
        st.info(f"**★ 프로젝트 기술 목표:** {chosen_content.get('project_goal', '현업 프로젝트 성공')}\n\n**이 플랜을 따라가야 하는 이유 & 로드맵 요약:**\n{chosen_data.get('summary', '')}")

        milestones = chosen_content.get("milestones", [])

        # 1. 마크다운 종합 일정표 (Table)
        st.markdown("### [한눈에 보는 로드맵 종합 실행 일정표]")
        table_rows = [
            "| 단계 | 소요 기간 | 집중 달성 목표 | 핵심 실무 액션 | 연계 추천 자원 |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for ms in milestones:
            p_name = ms.get("phase_name", f"Phase {ms.get('phase', 1)}")
            dur = ms.get("duration", "4주")
            goal = ms.get("focus_goal", "").replace("\n", " ")
            first_act = (ms.get("key_actions", ["실무 과제 수행"])[0]).replace("\n", " ")

            res_items = []
            for rc in ms.get("recommended_courses", []):
                res_items.append(f"강좌: {rc.get('title', '')[:14]}...")
            for rb in ms.get("recommended_books", []):
                res_items.append(f"도서: {rb.get('title', '')[:14]}...")
            for rz in ms.get("recommended_certifications", []):
                res_items.append(f"자격: {rz.get('title', '')}")

            res_str = "<br>".join(res_items) if res_items else "현업 프로젝트 실습"
            table_rows.append(f"| **{p_name}** | {dur} | {goal} | {first_act} | {res_str} |")

        st.markdown("\n".join(table_rows), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # 2. 단계별 세부 실행 플랜 (아코디언 뷰)
        st.markdown("### 단계별 세부 실행 플랜 및 검증 (Detail View)")
        for ms in milestones:
            phase_title = f"Phase {ms.get('phase', 1)}: {ms.get('phase_name', '학습 단계')} ({ms.get('duration', '4주')})"
            with st.expander(f"{phase_title} 세부 플랜 확인하기", expanded=True):
                st.markdown(
                    f"""
<div class="milestone-container">
<h4 style="color: #1E3A8A; margin-bottom: 6px;">{phase_title}</h4>
<p><strong>집중 달성 목표:</strong> {ms.get('focus_goal', '')}</p>
<p style="color: #0369A1; margin-bottom: 0;"><strong>프로젝트 기여 효과:</strong> {ms.get('project_impact', '')}</p>
</div>
""",
                    unsafe_allow_html=True,
                )

                actions = ms.get("key_actions", [])
                if actions:
                    st.markdown("##### 현업 실무 실행 과제 (Action Items)")
                    for act in actions:
                        st.markdown(f"- [x] **{act}**")
                    st.markdown("<br>", unsafe_allow_html=True)

                st.markdown("##### 연계 추천 실존 자원")
                c_col, b_col, z_col = st.columns(3)

                # (1) 강좌 컬럼: 404 방지 안전 바로가기 링크 100% 보장
                with c_col:
                    courses = ms.get("recommended_courses", [])
                    st.markdown("**실존 온라인 강좌**")
                    if courses:
                        for c in courses:
                            cid = c.get("course_id", "")
                            ctitle = c.get("title", "")
                            cplatform = c.get("platform", "온라인")
                            matched = grounding_courses_map.get(cid, {})
                            target_url = matched.get("url") or c.get("url")
                            safe_url = resolve_course_safe_url(target_url, ctitle, cplatform)

                            with st.container():
                                st.markdown(f"**`{cid}`** {ctitle}")
                                st.caption(f"플랫폼: {cplatform}")
                                st.link_button("강좌 바로가기 ↗", safe_url, use_container_width=True)
                    else:
                        st.caption("해당 단계 지정 강좌 없음")

                # (2) 도서 컬럼: (관련 추천 도서) 배지 및 안내
                with b_col:
                    books = ms.get("recommended_books", [])
                    st.markdown("**실존 전문 도서 (KDC 004)**")
                    if books:
                        for b in books:
                            bisbn = b.get("isbn", "")
                            btitle = b.get("title", "")
                            bauthor = b.get("author", "전문가")
                            is_related = b.get("is_related", False)

                            if bisbn in grounding_books_map:
                                is_related = grounding_books_map[bisbn].get("is_related", is_related)

                            with st.container():
                                st.markdown(f"- **{btitle}**\n  *저자: {bauthor} (ISBN: {bisbn})*")
                                if is_related:
                                    st.markdown('<span class="related-book-tag">(관련 추천 도서)</span>', unsafe_allow_html=True)
                                    st.caption("완전한 스킬 태그 일치는 아니지만, 컴퓨터과학 유사도를 바탕으로 추천된 참고 도서입니다.")
                    else:
                        st.caption("해당 단계 추천 도서 없음")

                # (3) 자격증 컬럼
                with z_col:
                    certs = ms.get("recommended_certifications", [])
                    st.markdown("**실존 공인 자격증**")
                    if certs:
                        for z in certs:
                            st.markdown(f"- **{z.get('title', '')}**\n  *시행: {z.get('provider', '공인기관')}*")
                    else:
                        st.caption("해당 단계 자격증 없음")

        st.markdown("---")
        st.markdown("### [업무 개발 및 프로젝트 성과 검증 방안]")
        st.markdown("""
프로젝트 산출물의 실질적 변화와 역량 향상을 점검하기 위한 3단계 정성 평가 프레임워크입니다:
1. **산출물 전후 비교 (Artifact Delta)**: 로드맵 시작 전 부서원의 기존 코드와 이수 후 커밋/PR된 신규 아키텍처(MSA, 비동기 분산 트랜잭션 등) 간의 코드 리팩토링 차이 및 설계 완성도를 측정합니다.
2. **동료 및 협업 설문조사 (Peer Survey)**: 함께 프로젝트를 수행하는 동료 엔지니어 및 유관 조직을 대상으로 협업 생산성 향상 수준을 5점 척도로 측정합니다.
3. **팀 내 지식 공유 세미나 퀴즈**: 부서원이 습득한 기술 스택을 팀 내 세미나로 공유하고 간이 퀴즈를 진행하여 전사적 기술 전파 효과를 검증합니다.
""")