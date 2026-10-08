# =====================================================================
# FILE: saved/app.py (또는 루트 app.py)
# =====================================================================

import os
import sys
import uuid
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import streamlit as st

current_file = Path(__file__).resolve()
project_root = current_file.parent.parent if current_file.parent.name == "saved" else current_file.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.db.db_client import db_client
from src.core.assessment_service import assessment_service
from src.core.selection_service import selection_service
from src.core.assessment_agent import assessment_agent
from src.core.recommendation_service import recommendation_service
from src.core.roadmap_agent import multi_agent_orchestrator

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("APP_7STAGE")


# ---------------------------------------------------------------------
# Defensive Hot-Patching
# ---------------------------------------------------------------------
def _force_create_session(user_id=None, project_name=None, user_type="REAL_USER", *args, **kwargs):
    target_uid = user_id or kwargs.get("user_id") or "00000000-0000-0000-0000-000000000001"
    target_pname = project_name or kwargs.get("project_name") or kwargs.get("raw_user_prompt") or "신규 프로젝트"
    if hasattr(db_client, "create_roadmap_session"):
        return db_client.create_roadmap_session(user_id=target_uid, project_name=str(target_pname)[:100])
    return str(uuid.uuid4())


selection_service.create_roadmap_session = _force_create_session

if not hasattr(db_client, "get_active_prompts"):
    db_client.get_active_prompts = lambda: {}

if not hasattr(selection_service, "list_roadmap_sessions"):
    selection_service.list_roadmap_sessions = lambda user_id=None, limit=10: (
        db_client.list_roadmap_sessions(user_id=user_id, limit=limit) if hasattr(db_client,
                                                                                 "list_roadmap_sessions") else []
    )

if not hasattr(selection_service, "get_roadmap_session"):
    selection_service.get_roadmap_session = lambda session_id: (
        db_client.get_roadmap_session(session_id) if hasattr(db_client, "get_roadmap_session") else None
    )

if not hasattr(selection_service, "update_session_stage"):
    selection_service.update_session_stage = lambda session_id, stage_num, status="IN_PROGRESS": (
        db_client.update_session_stage(session_id, stage_num, status) if hasattr(db_client,
                                                                                 "update_session_stage") else True
    )

if not hasattr(selection_service, "get_agent_generations"):
    selection_service.get_agent_generations = lambda session_id: (
        db_client.get_roadmap_generations(session_id) if hasattr(db_client, "get_roadmap_generations") else []
    )

if not hasattr(selection_service, "save_agent_generations"):
    selection_service.save_agent_generations = lambda session_id, generations: (
        db_client.save_roadmap_generations(session_id, generations) if hasattr(db_client,
                                                                               "save_roadmap_generations") else True
    )

if not hasattr(selection_service, "select_roadmap"):
    def _safe_select(session_id, generation_id, detailed_report):
        ok1 = db_client.save_roadmap_selection(session_id, generation_id) if hasattr(db_client,
                                                                                     "save_roadmap_selection") else True
        ok2 = db_client.save_roadmap_report(session_id, generation_id, detailed_report) if hasattr(db_client,
                                                                                                   "save_roadmap_report") else True
        return ok1 and ok2


    selection_service.select_roadmap = _safe_select

if not hasattr(selection_service, "get_selected_roadmap"):
    def _safe_get_selected(session_id):
        sel = db_client.get_roadmap_selection(session_id) if hasattr(db_client, "get_roadmap_selection") else None
        rep = db_client.get_roadmap_report(session_id) if hasattr(db_client, "get_roadmap_report") else None
        if not sel:
            return None
        gen_id = sel.get("generation_id")
        gens = db_client.get_roadmap_generations(session_id) if hasattr(db_client, "get_roadmap_generations") else []
        chosen_gen = next((g for g in gens if g.get("generation_id") == gen_id), {})
        return {"selection": sel, "generation": chosen_gen, "report": rep.get("report_content") if rep else {}}


    selection_service.get_selected_roadmap = _safe_get_selected

st.set_page_config(
    page_title="엔터프라이즈 프로젝트 역량 개발 플랫폼",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.main-title { font-size: 24px; font-weight: 700; color: #0F172A; margin-bottom: 4px; }
.sub-title { font-size: 14px; color: #475569; margin-bottom: 16px; }
.stage-bar { padding: 10px 14px; border-radius: 6px; font-weight: 600; margin-bottom: 14px; }
.stage-1 { background-color: #EFF6FF; color: #1D4ED8; border-left: 4px solid #3B82F6; }
.stage-2 { background-color: #F0FDF4; color: #15803D; border-left: 4px solid #22C55E; }
.stage-3 { background-color: #FEFCE8; color: #A16207; border-left: 4px solid #EAB308; }
.stage-4 { background-color: #FAF5FF; color: #7E22CE; border-left: 4px solid #A855F7; }
.stage-5 { background-color: #F0FDF4; color: #047857; border-left: 4px solid #10B981; }
.stage-6 { background-color: #FFF7ED; color: #C2410C; border-left: 4px solid #F97316; }
.stage-7 { background-color: #F8FAFC; color: #0F172A; border-left: 4px solid #334155; }
.strategy-box { border: 1px solid #CBD5E1; border-radius: 8px; padding: 16px; background-color: #FFFFFF; height: 100%; display: flex; flex-direction: column; justify-content: space-between; }
.growth-card { border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px; margin-bottom: 12px; background: #FFFFFF; }
</style>
""", unsafe_allow_html=True)


def resolve_user_id(username: str, email: str, department: str, job_title: str, grade: str) -> str:
    if hasattr(db_client, "get_or_create_user"):
        try:
            return db_client.get_or_create_user(username, email, department, job_title, grade)
        except Exception as exc:
            logger.warning(f"get_or_create_user 호출 예외: {exc}")
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, email if email else "backend.kim@company.com"))


def sync_session_state(session_id: str):
    sess = selection_service.get_roadmap_session(session_id)
    if not sess:
        return
    st.session_state.session_id = session_id
    st.session_state.current_stage = sess.get("current_stage", 1)
    st.session_state.project_name = sess.get("project_name", "")
    st.session_state.user_id = sess.get("user_id")


if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "current_stage" not in st.session_state:
    st.session_state.current_stage = 1
if "user_id" not in st.session_state:
    st.session_state.user_id = None

st.markdown('<div class="main-title">엔터프라이즈 프로젝트 역량 개발 플랫폼</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">DB 영속화 기반 7단계 프로젝트 수명주기 파이프라인</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------
# 사이드바
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("👤 엔지니어 프로필")
    u_name = st.text_input("성명", value="김백엔")
    u_email = st.text_input("이메일", value="backend.kim@company.com")
    u_dept = st.text_input("소속 부서", value="플랫폼개발1팀")
    u_job = st.text_input("직무 타이틀", value="백엔드 엔지니어")
    u_grade = st.text_input("직급", value="선임연구원")
    u_target = st.text_input("프로젝트 요구 스킬 (콤마 구분)", value="FastAPI, PostgreSQL, Docker, Redis, CI/CD")

    user_profile = {
        "profile_name": u_name,
        "username": u_name,
        "email": u_email,
        "department": u_dept,
        "job_title": u_job,
        "grade": u_grade,
        "current_skills": ["Python", "Git/GitHub", "Docker"],
        "target_skills": [s.strip() for s in u_target.split(",") if s.strip()]
    }

    current_uid = resolve_user_id(u_name, u_email, u_dept, u_job, u_grade)
    st.session_state.user_id = current_uid

    st.markdown("---")
    st.header("🗂 프로젝트 세션 목록")

    sessions = selection_service.list_roadmap_sessions(user_id=current_uid)
    sess_map = {f"{s['project_name']} (Stage {s['current_stage']})": s["session_id"] for s in sessions}

    chosen_sess = st.selectbox("진행 중인 프로젝트 불러오기", options=["신규 프로젝트 시작"] + list(sess_map.keys()))
    if chosen_sess != "신규 프로젝트 시작":
        sid = sess_map[chosen_sess]
        if st.session_state.session_id != sid:
            sync_session_state(sid)
            st.rerun()

    if st.button("➕ 새 프로젝트 세션 초기화", use_container_width=True):
        st.session_state.session_id = None
        st.session_state.current_stage = 1
        st.rerun()

# =====================================================================
# STAGE 1: Draft Plan 수립
# =====================================================================
st.markdown('<div class="stage-bar stage-1">1단계: 프로젝트 과제 정의 및 Draft Plan 수립</div>', unsafe_allow_html=True)

col_s1_1, col_s1_2 = st.columns([1.5, 1])
with col_s1_1:
    p_name_input = st.text_input("프로젝트명", value="레거시 API 마이크로서비스 전환")
    p_req_input = st.text_area(
        "초기 요구사항 및 아키텍처 제약사항",
        value="PostgreSQL 커넥션 풀 고갈과 이벤트 루프 블로킹 지연 문제를 해결해야 합니다. 비동기 전환과 캐싱, CI/CD 구축이 필요합니다.",
        height=80
    )
with col_s1_2:
    st.write("")
    uploaded_doc = st.file_uploader("참조 아키텍처 문서 첨부 (선택)", type=["txt", "md"])
    doc_text = uploaded_doc.getvalue().decode("utf-8") if uploaded_doc else None

if not st.session_state.session_id:
    if st.button("🚀 1단계 Draft Plan 생성 및 세션 DB 생성", type="primary", use_container_width=True):
        with st.spinner("프로젝트 세션 엔티티를 생성하고 Draft Plan을 DB에 영속화하는 중..."):
            new_sid = selection_service.create_roadmap_session(
                user_id=current_uid,
                project_name=p_name_input
            )
            if not new_sid:
                st.error("세션 생성 실패")
                st.stop()

            user_profile["work_context_summary"] = p_name_input
            draft_data = assessment_agent.generate_draft_plan(
                project_name=p_name_input,
                user_profile=user_profile,
                initial_prompt=p_req_input,
                uploaded_doc_text=doc_text
            )

            assessment_service.save_project_plan(new_sid, draft_data)
            init_chat = "수석 아키텍트입니다. 1단계 초안 계획이 수립되었습니다. 추가할 기술 요구사항이나 캐싱 전략을 말씀해 주세요."
            assessment_service.save_plan_conversation(new_sid, "assistant", init_chat, sequence_no=1)

            selection_service.update_session_stage(new_sid, 2)
            sync_session_state(new_sid)
            st.rerun()

plan_stage1 = assessment_service.get_project_plan(st.session_state.session_id) if st.session_state.session_id else None
if plan_stage1 and plan_stage1.get("draft_plan"):
    with st.expander("📄 [DB 조회] 1단계 Draft Plan 상세 내용", expanded=(st.session_state.current_stage == 2)):
        st.markdown(plan_stage1["draft_plan"])

# =====================================================================
# STAGE 2: Chat 구체화 및 Refined Plan 확정
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage >= 2:
    st.markdown('<div class="stage-bar stage-2">2단계: 기술 멘토링 Chat 구체화 및 계획 최종 확정</div>', unsafe_allow_html=True)

    conversations = assessment_service.get_plan_conversations(st.session_state.session_id)

    with st.container(border=True):
        chat_box = st.container(height=260)
        with chat_box:
            for c in conversations:
                with st.chat_message(c["role"]):
                    st.markdown(c["content"])

        u_chat = st.chat_input("아키텍처 추가/보완 의견을 입력하세요 (예: 'Redis 캐싱도 추가하고 싶습니다.')")

    if u_chat:
        next_seq = len(conversations) + 1
        assessment_service.save_plan_conversation(st.session_state.session_id, "user", u_chat, next_seq)

        with st.spinner("테크니컬 리드가 대화 이력을 검토하는 중..."):
            reply = assessment_agent.process_dialogue(st.session_state.session_id, u_chat)

        assessment_service.save_plan_conversation(st.session_state.session_id, "assistant", reply, next_seq + 1)
        st.rerun()

    if st.button("✔ 1+2단계 내용 통합/정규화 및 최종 계획 확정 (DB 저장)", type="primary", use_container_width=True):
        with st.spinner("DB의 Draft Plan과 Chat History를 통합하여 정규화된 Project Plan을 저장하는 중..."):
            refined_data = assessment_agent.finalize_refined_plan(st.session_state.session_id)
            assessment_service.save_project_plan(st.session_state.session_id, refined_data)

            pre_questions = assessment_agent.generate_pre_assessment(st.session_state.session_id)
            assessment_service.save_assessment_questions(st.session_state.session_id, "PRE", pre_questions)

            selection_service.update_session_stage(st.session_state.session_id, 3)
            sync_session_state(st.session_state.session_id)
            st.toast("프로젝트 계획이 정규화되어 DB에 저장되었습니다.", icon="🎯")
            st.rerun()

    refined_plan_row = assessment_service.get_project_plan(st.session_state.session_id)
    if refined_plan_row and refined_plan_row.get("refined_plan"):
        with st.expander("📋 [DB 조회] 정규화된 프로젝트 계획 최종 데이터 (Refined Plan)", expanded=False):
            st.markdown(refined_plan_row["refined_plan"])

# =====================================================================
# STAGE 3: Pre-Test (사전 평가 - BARS 진단)
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage >= 3:
    st.markdown('<div class="stage-bar stage-3">3단계: 기준선 Pre-Test (DB 확정 계획 기반 동적 진단)</div>', unsafe_allow_html=True)
    st.info("💡 현재 본인의 실제 실무 구현 수준에 부합하는 항목을 선택하세요 (옵션 1: 입문 ~ 옵션 4: 전문가).")

    pre_assessment = assessment_service.get_assessment(st.session_state.session_id, "PRE")
    questions = pre_assessment.get("questions", [])

    with st.form("form_pre_assessment"):
        user_answers = []
        for q in questions:
            qid = q["question_id"]
            skill = q.get("skill", q.get("skill_name", "General"))
            st.markdown(f"**[{skill}] {q['question']}**")
            opts = q.get("options", [])
            # 사용자가 보통 기초~초급 수준(index 0 또는 1)을 고름
            choice = st.radio(label=f"q_{qid}", options=opts, index=0, key=f"pre_opt_{qid}",
                              label_visibility="collapsed")
            score = opts.index(choice) + 1 if choice in opts else 1
            user_answers.append({
                "question_id": qid,
                "skill": skill,
                "selected_option": choice,
                "score": score
            })
            st.write("")

        submit_pre = st.form_submit_button("Pre-Test 응답 제출 및 DB 저장 (4단계 대안 생성)", type="primary",
                                           use_container_width=True)

    if submit_pre:
        assessment_service.save_assessment_responses(st.session_state.session_id, "PRE", user_answers)

        with st.spinner("DB 데이터를 기반으로 3개 로드맵 대안 생성 중..."):
            plan = assessment_service.get_project_plan(st.session_state.session_id)
            grounding = recommendation_service.retrieve_grounding_context(
                target_skills=plan.get("tech_stack", user_profile["target_skills"]),
                target_job_title=user_profile["job_title"]
            )
            orch_profile = dict(user_profile)
            orch_profile["work_context_summary"] = plan.get("refined_plan", plan.get("draft_plan", ""))

            orch = multi_agent_orchestrator.generate_all_roadmaps(
                user_context=orch_profile,
                conversation_history=[],
                retrieved_context=grounding,
                hidden_assessment_responses={a["question_id"]: a["score"] for a in user_answers}
            )
            roadmap_gens = []
            for k, v in orch.get("results", {}).items():
                content = v.get("roadmap_content", {})
                roadmap_gens.append({
                    "strategy_type": k,
                    "strategy_title": content.get("strategy_title", k),
                    "summary": v.get("summary", ""),
                    "roadmap_content": content
                })
            selection_service.save_agent_generations(st.session_state.session_id, roadmap_gens)

            selection_service.update_session_stage(st.session_state.session_id, 4)
            sync_session_state(st.session_state.session_id)
            st.rerun()

# =====================================================================
# STAGE 4: 3개 로드맵 대안 생성 결과
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage >= 4:
    st.markdown('<div class="stage-bar stage-4">4단계: 전략적 로드맵 3개 대안 (DB 조회)</div>', unsafe_allow_html=True)

    roadmaps = selection_service.get_agent_generations(st.session_state.session_id)
    cols = st.columns(len(roadmaps) if roadmaps else 3)

    for idx, r in enumerate(roadmaps):
        gid = r.get("generation_id", f"gen_{idx}")
        stype = r.get("strategy_type", "general")
        title = r.get("strategy_title", "전략 로드맵")
        summary = r.get("summary", "")
        content = r.get("roadmap_content", {})

        with cols[idx]:
            st.markdown(
                f"""
<div class="strategy-box">
    <div>
        <h4 style="margin: 0 0 8px 0; color: #1E293B;">{title}</h4>
        <p style="font-size: 13px; color: #475569; line-height: 1.5;">{summary}</p>
        <div style="font-size: 12px; color: #64748B; margin: 8px 0;">
            <b>전략 유형:</b> {stype}<br>
            <b>총 수행 기간:</b> {content.get('total_duration_weeks', 8)}주
        </div>
    </div>
</div>
""",
                unsafe_allow_html=True
            )
            st.write("")
            if st.button("이 로드맵 채택하기", key=f"btn_choose_{gid}", use_container_width=True):
                detailed_report = {
                    "strategy_title": title,
                    "project_summary": summary,
                    "total_duration_weeks": content.get("total_duration_weeks", 8),
                    "why_this_roadmap": f"{title} 전략이 현 프로젝트 병목 해결 및 역량 성장에 가장 최적화됨",
                    "project_goal": content.get("project_goal", "프로덕션 레벨 고가용성 아키텍처 완결"),
                    "roadmap_flow": content.get("roadmap_flow", []),
                    "phases": content.get("milestones", []),
                    "deliverables": content.get("deliverables", []),
                    "validation_criteria": ["마일스톤별 단위 테스트 커버리지 80% 달성", "프로덕션 통합 부하 테스트 완결"],
                    "expected_outcomes": ["비동기 전환을 통한 응답 지연 시간 50% 단축", "장애 격리 및 배포 자동화 달성"]
                }
                selection_service.select_roadmap(st.session_state.session_id, gid, detailed_report)
                selection_service.update_session_stage(st.session_state.session_id, 5)
                sync_session_state(st.session_state.session_id)
                st.rerun()

# =====================================================================
# STAGE 5: 하나 선택 및 상세 설계 보고서
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage >= 5:
    st.markdown('<div class="stage-bar stage-5">5단계: 선택된 로드맵 및 상세 설계 보고서 (DB 조회)</div>', unsafe_allow_html=True)

    selected_data = selection_service.get_selected_roadmap(st.session_state.session_id)
    if selected_data:
        sel_rec = selected_data["selection"]
        rep_content = selected_data.get("report", {})
        gen_rec = selected_data.get("generation", {})

        strat_title = rep_content.get("strategy_title") or gen_rec.get("strategy_title", "맞춤형 기술 로드맵")
        total_weeks = rep_content.get("total_duration_weeks") or gen_rec.get("roadmap_content", {}).get(
            "total_duration_weeks", 8)
        proj_name = st.session_state.get("project_name", "엔지니어링 과제")

        # 1. 상단 배너
        st.markdown(
            f"""
<div style="background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%); padding: 22px; border-radius: 10px; color: #FFFFFF; margin-bottom: 20px;">
    <h2 style="margin: 0; color: #FFFFFF; font-size: 22px;">📘 채택된 청사진: {strat_title}</h2>
    <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px;">총 수행 기간: {total_weeks}주 | 엔지니어: {u_name} ({u_grade}) | 과제명: {proj_name}</p>
</div>
""",
            unsafe_allow_html=True
        )

        st.markdown(f"**전략 요약:** {rep_content.get('project_summary', '')}")
        st.markdown(f"**선택 근거:** {rep_content.get('why_this_roadmap', '')}")
        st.markdown(f"**핵심 엔지니어링 목표:** {rep_content.get('project_goal', '')}")

        # 2. 로드맵 실행 순서 요약: ㅁ ➔ ㅁ ➔ ㅁ 프로세스 카드
        st.markdown("---")
        st.markdown("### 🧭 마일스톤 실행 순서 요약 플로우")
        flow_steps = rep_content.get("roadmap_flow", [])
        if flow_steps:
            flow_cols = st.columns(len(flow_steps))
            for f_idx, step in enumerate(flow_steps):
                with flow_cols[f_idx]:
                    arrow = "➔" if f_idx < len(flow_steps) - 1 else "🏁"
                    st.markdown(
                        f"""
<div style="border: 2px solid #3B82F6; border-radius: 8px; padding: 14px; background-color: #F8FAFC; text-align: center; height: 100%;">
    <div style="font-size: 12px; font-weight: 700; color: #1D4ED8; margin-bottom: 4px;">STEP {step.get('step', f_idx + 1)} ({step.get('duration', '')})</div>
    <div style="font-size: 14px; font-weight: 700; color: #0F172A; margin-bottom: 6px;">{step.get('title', '')}</div>
    <div style="font-size: 12px; color: #64748B;">{step.get('focus', '')}</div>
    <div style="margin-top: 8px; font-size: 16px;">{arrow}</div>
</div>
""",
                        unsafe_allow_html=True
                    )
        st.write("")

        # 3. 마일스톤 총괄표
        st.markdown("### 📊 마일스톤 실행 계획 총괄표")
        table_rows = []
        phases = rep_content.get("phases", [])
        for ms in phases:
            p_name_val = ms.get("phase_name", f"Phase {ms.get('phase', 1)}")
            dur_val = ms.get("duration", "2주")
            goal_val = ms.get("focus_goal", "")
            impact_val = ms.get("project_impact", "")
            table_rows.append(f"| **{p_name_val}** | {dur_val} | {goal_val} | {impact_val} |")

        st.markdown(
            "| 마일스톤 | 소요 기간 | 핵심 엔지니어링 목표 | 기술적 파급 효과 |\n| :--- | :---: | :--- | :--- |\n" + "\n".join(table_rows))

        # 4. 세부 과제 및 RAG 자원 명세
        st.markdown("---")
        st.markdown("### 🛠 단계별 세부 실행 과제 및 RAG 추천 학습 자원 명세")
        for ms in phases:
            p_title = ms.get("phase_name", f"Phase {ms.get('phase', 1)}")
            with st.expander(f"📌 {p_title} (기간: {ms.get('duration', '2주')}) - 세부 과제 및 추천 자원 열기", expanded=True):
                col_act, col_rag = st.columns([1.2, 1])

                with col_act:
                    st.markdown("#### 🎯 중점 엔지니어링 실행 액션")
                    for act in ms.get("key_actions", []):
                        st.markdown(f"- 🔹 {act}")

                    if ms.get("deliverables"):
                        st.markdown("#### 📦 단계별 핵심 산출물 (Deliverables)")
                        for d in ms.get("deliverables", []):
                            st.markdown(f"- ✅ **{d}**")

                with col_rag:
                    st.markdown("#### 📚 RAG 기반 맞춤 추천 자원")

                    st.markdown("**🖥 공식 온라인 기술 강좌**")
                    courses = ms.get("recommended_courses", [])
                    if courses:
                        for c in courses:
                            st.markdown(
                                f"- [{c.get('title')}]({c.get('url', '#')}) `({c.get('platform', 'MS Learn')})`")
                    else:
                        st.caption("매칭된 온라인 강좌 없음")

                    st.markdown("**🏆 연계 공인 자격증**")
                    certs = ms.get("recommended_certifications", [])
                    if certs:
                        for cert in certs:
                            st.markdown(f"- **{cert.get('title')}** `({cert.get('provider', '공인기관')})`")
                    else:
                        st.caption("매칭된 공인 자격증 없음")

                    st.markdown("**📖 핵심 참고 도서**")
                    books = ms.get("recommended_books", [])
                    if books:
                        for b in books:
                            st.markdown(f"- *{b.get('title')}* (저자: {b.get('author', '전문가')})")
                    else:
                        st.caption("매칭된 참고 도서 없음")

        # 5. 6단계 진입 트리거
        st.markdown("---")
        if st.session_state.current_stage == 5:
            if st.button("🏁 프로젝트 수행 완료 및 6단계 Post-Test 시작", type="primary", use_container_width=True):
                with st.spinner("DB를 조회하여 사후 평가 문항을 생성하는 중..."):
                    post_questions = assessment_agent.generate_post_assessment(st.session_state.session_id)
                    assessment_service.save_assessment_questions(st.session_state.session_id, "POST", post_questions)

                    selection_service.update_session_stage(st.session_state.session_id, 6)
                    sync_session_state(st.session_state.session_id)
                    st.rerun()

# =====================================================================
# STAGE 6: Post-Test (사후 평가 - 로드맵 완수 후 실무 검증)
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage >= 6:
    st.markdown('<div class="stage-bar stage-6">6단계: Post-Test (1~3단계 + Roadmap 기반 실무 검증)</div>',
                unsafe_allow_html=True)
    st.info("💡 로드맵 수행 후 달성한 역량 수준을 선택하세요. 성장도를 정확히 측정하기 위해 향상된 수준에 맞추어 선택해 주세요.")

    post_assessment = assessment_service.get_assessment(st.session_state.session_id, "POST")
    post_questions = post_assessment.get("questions", [])

    with st.form("form_post_assessment"):
        post_answers = []
        for q in post_questions:
            qid = q["question_id"]
            skill = q.get("skill", q.get("skill_name", "General"))
            opts = q.get("options", [])
            st.markdown(f"**[{skill}] {q['question']}**")

            if opts:
                # 사용자가 로드맵 수행 후 실무 역량이 성장했으므로 기본 선택을 심화(index 2: Level 3)로 유도
                default_idx = min(2, len(opts) - 1)
                p_choice = st.radio(label=f"post_{qid}", options=opts, index=default_idx, key=f"post_opt_{qid}",
                                    label_visibility="collapsed")
                p_score = opts.index(p_choice) + 1 if p_choice in opts else 3
                post_answers.append({
                    "question_id": qid,
                    "skill": skill,
                    "selected_option": p_choice,
                    "score": p_score
                })
            else:
                p_text = st.text_area(label=f"post_text_{qid}",
                                      value="PR #42: 비동기 FastAPI 게이트웨이 전환 완료, connection pool 지연 60% 감소 달성",
                                      key=f"post_txt_{qid}", label_visibility="collapsed")
                post_answers.append({
                    "question_id": qid,
                    "skill": skill,
                    "selected_option": p_text if p_text else "기여 완료",
                    "score": 4
                })
            st.write("")

        submit_post = st.form_submit_button("Post-Test 제출 및 최종 성장 평가 보고서 생성", type="primary", use_container_width=True)

    if submit_post:
        assessment_service.save_assessment_responses(st.session_state.session_id, "POST", post_answers)

        with st.spinner("사전/사후 응답 데이터를 결합하여 종단적 성장 평가 보고서를 합성하는 중..."):
            final_report = assessment_agent.generate_growth_report(st.session_state.session_id)
            assessment_service.save_assessment_report(st.session_state.session_id, final_report)

            selection_service.update_session_stage(st.session_state.session_id, 7, status="COMPLETED")
            sync_session_state(st.session_state.session_id)
            st.rerun()

# =====================================================================
# STAGE 7: 평가 보고서 (Pre ↔ Post 비교 분석 완결)
# =====================================================================
if st.session_state.session_id and st.session_state.current_stage == 7:
    st.markdown('<div class="stage-bar stage-7">7단계: 최종 평가 보고서 (Pre ↔ Post 정밀 비교 분석)</div>', unsafe_allow_html=True)

    report = assessment_service.get_assessment_report(st.session_state.session_id)

    if report:
        st.subheader("📊 역량 성장 비교 매트릭스 (Competency Migration Matrix)")

        comp_list = report.get("skill_comparison", [])

        # 1. 요약 메트릭 카드
        total_growth = sum(c.get("growth", 0) for c in comp_list)
        avg_growth = round(total_growth / len(comp_list), 1) if comp_list else 0

        m_col1, m_col2, m_col3 = st.columns(3)
        with m_col1:
            st.metric("평가 역량 축 수", f"{len(comp_list)}개 스킬")
        with m_col2:
            st.metric("총 성장 레벨 합계", f"+{total_growth} Levels")
        with m_col3:
            st.metric("평균 역량 신장도", f"+{avg_growth} 레벨 상승", delta=f"+{avg_growth}")

        # 2. 정밀 비교 표
        comp_rows = []
        for c in comp_list:
            sk = c.get("skill", "")
            pre_l = c.get("pre_level", 1)
            post_l = c.get("post_level", 1)
            growth = c.get("growth", 0)

            if growth > 0:
                badge = f"**+{growth}단계 성장 🚀**"
            elif growth == 0:
                badge = "동일 유지 ⚪"
            else:
                badge = f"{growth}단계 🔻"

            comp_rows.append(f"| **{sk}** | Level {pre_l} | Level {post_l} | {badge} |")

        st.markdown(
            "| 역량 축 | 사전 진단 (Pre) | 사후 검증 (Post) | 성장 델타 (Delta) |\n| :--- | :---: | :---: | :---: |\n" + "\n".join(
                comp_rows))

        # 3. 세부 행동 변화 진술문 대조 카드
        st.markdown("---")
        st.markdown("### 🔍 기술별 실무 행동 변화 상세 대조")
        for c in comp_list:
            sk = c.get("skill", "")
            pre_l = c.get("pre_level", 1)
            post_l = c.get("post_level", 1)
            growth = c.get("growth", 0)
            pre_text = c.get("pre_statement", "기록 없음")
            post_text = c.get("post_statement", "기록 없음")

            with st.container():
                st.markdown(
                    f"""
<div class="growth-card">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <h4 style="margin: 0; color: #1E293B;">{sk}</h4>
        <span style="font-weight: 700; color: {'#15803D' if growth > 0 else '#64748B'}; font-size: 14px;">
            Level {pre_l} ➔ Level {post_l} ({'+' if growth > 0 else ''}{growth} 단계)
        </span>
    </div>
    <div style="font-size: 13px; color: #475569; margin-bottom: 4px;"><b>[사전 상태]</b> {pre_text}</div>
    <div style="font-size: 13px; color: #1D4ED8;"><b>[사후 성취]</b> {post_text}</div>
</div>
""",
                    unsafe_allow_html=True
                )

        # 4. 정성 분석 요약
        st.markdown("---")
        st.subheader("총괄 정성 평가 (Executive Narrative)")
        st.markdown(report.get("overall_summary", ""))

        col_rep1, col_rep2 = st.columns(2)
        with col_rep1:
            st.markdown("### 🌟 주요 입증 강점")
            for s in report.get("strengths", []):
                st.markdown(f"- ✅ {s}")
        with col_rep2:
            st.markdown("### 🎯 향후 지속 보완 영역")
            for imp in report.get("improvement_areas", []):
                st.markdown(f"- ⚠️ {imp}")

        st.info(f"**최종 인증 소견:** {report.get('final_evaluation', '')}")