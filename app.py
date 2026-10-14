# =====================================================================
# FILE: saved/app.py (또는 루트 app.py)
# =====================================================================

import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import streamlit as st

current_file = Path(__file__).resolve()
project_root = current_file.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.db.db_client import db_client, DatabaseOperationError
from src.services.assessment_service import assessment_service
from src.common.assessment_schema import LIKERT_OPTIONS, validate_questions
from src.common.resource_grounding import build_selection_report, duration_label
from src.services.selection_service import selection_service
from src.agents.assessment_agent import assessment_agent
from src.services.recommendation_service import recommendation_service
from src.agents.roadmap_agent import multi_agent_orchestrator

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("APP_7STAGE")


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


def sync_session_state(session_id: str):
    sess = selection_service.get_roadmap_session(session_id)
    if not sess or sess.get("user_id") != st.session_state.user_id:
        raise ValueError("선택한 사용자의 프로젝트 세션이 아닙니다.")
    st.session_state.session_id = session_id
    st.session_state.current_stage = sess.get("current_stage", 1)
    st.session_state.project_name = sess.get("project_name", "")
    st.session_state.user_id = sess.get("user_id")
    st.session_state["pending_session_selection"] = session_id


try:
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
        st.header("👤 테스트 사용자")
        users = db_client.list_test_users()
        if not users:
            st.error("DB에 가상 사용자가 없습니다. python scripts/db.py demo-users 실행 후 다시 열어 주세요.")
            st.stop()
        by_id = {u["user_id"]: u for u in users}
        current_uid = st.selectbox("DB 사용자 선택", options=list(by_id), key="selected_db_user",
                                  format_func=lambda uid: f"{by_id[uid]['name']} (ID {uid})")
        if current_uid not in by_id:
            raise ValueError("등록된 테스트 사용자를 선택해 주세요.")
        if st.session_state.user_id != current_uid:
            st.session_state.session_id = None
            st.session_state.current_stage = 1
            st.session_state.project_name = ""
        st.session_state.user_id = current_uid
        profile = by_id[current_uid]
        u_name, u_email = profile["name"], profile.get("email") or ""
        u_dept, u_job, u_grade = (profile.get(k) or "" for k in ("department", "job_title", "grade"))
        st.write(f"{u_name} · {u_dept} · {u_job} · {u_grade}")
        tags = db_client.list_skill_tags()
        names = [tag["name"] for tag in tags]
        if not names:
            st.error("적재된 스킬 태그가 없습니다.")
            st.stop()
        current_skills = st.multiselect("보유 스킬 (DB 태그)", names, key=f"current_skills_{current_uid}")
        target_skills = st.multiselect("프로젝트 요구 스킬 (DB 태그)", names, key=f"target_skills_{current_uid}")
        if any(skill not in names for skill in current_skills + target_skills):
            raise ValueError("DB에 있는 스킬 태그만 선택할 수 있습니다.")
        user_profile = {
            "user_id": current_uid, "profile_name": u_name, "username": u_name, "email": u_email,
            "department": u_dept, "job_title": u_job, "grade": u_grade,
            "current_skills": current_skills, "target_skills": target_skills,
        }

        st.markdown("---")
        st.header("🗂 프로젝트 세션 목록")

        sessions = selection_service.list_roadmap_sessions(user_id=current_uid)
        sess_map = {f"{s['project_name']} (Stage {s['current_stage']}, {s['session_id'][:8]})": s["session_id"] for s in sessions}

        if st.session_state.pop("reset_session_picker", None) == current_uid:
            st.session_state[f"session_picker_{current_uid}"] = "신규 프로젝트 시작"
        pending_sid = st.session_state.pop("pending_session_selection", None)
        if pending_sid:
            pending_label = next((label for label, sid in sess_map.items() if sid == pending_sid), None)
            if pending_label:
                st.session_state[f"session_picker_{current_uid}"] = pending_label
        chosen_sess = st.selectbox("진행 중인 프로젝트 불러오기", options=["신규 프로젝트 시작"] + list(sess_map.keys()), key=f"session_picker_{current_uid}")
        if chosen_sess == "신규 프로젝트 시작":
            st.session_state.session_id = None
            st.session_state.current_stage = 1
        if chosen_sess != "신규 프로젝트 시작":
            sid = sess_map[chosen_sess]
            if st.session_state.session_id != sid:
                sync_session_state(sid)
                st.rerun()

        if st.button("➕ 새 프로젝트 세션 초기화", width="stretch"):
            st.session_state.session_id = None
            st.session_state["reset_session_picker"] = current_uid
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
        if st.button("🚀 1단계 Draft Plan 생성 및 세션 DB 생성", type="primary", width="stretch"):
            with st.spinner("프로젝트 세션 엔티티를 생성하고 Draft Plan을 DB에 영속화하는 중..."):
                if not user_profile["target_skills"]:
                    st.error("프로젝트 요구 스킬을 하나 이상 선택해 주세요.")
                    st.stop()
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

            u_chat = st.chat_input("아키텍처 추가/보완 의견을 입력하세요 (예: 'Redis 캐싱도 추가하고 싶습니다.')", disabled=st.session_state.current_stage != 2)

        if u_chat:
            next_seq = len(conversations) + 1
            assessment_service.save_plan_conversation(st.session_state.session_id, "user", u_chat, next_seq)

            with st.spinner("테크니컬 리드가 대화 이력을 검토하는 중..."):
                reply = assessment_agent.process_dialogue(st.session_state.session_id, u_chat)

            assessment_service.save_plan_conversation(st.session_state.session_id, "assistant", reply, next_seq + 1)
            st.rerun()

        if st.button("✔ 1+2단계 내용 통합/정규화 및 최종 계획 확정 (DB 저장)", type="primary", width="stretch", disabled=st.session_state.current_stage != 2):
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
    # STAGE 3: Pre-Test (사전 평가 - 5점 자기보고)
    # =====================================================================
    if st.session_state.session_id and st.session_state.current_stage >= 3:
        st.markdown('<div class="stage-bar stage-3">3단계: 기준선 Pre-Test (DB 확정 계획 기반 동적 진단)</div>', unsafe_allow_html=True)
        st.info("각 개념이나 방법을 현재 얼마나 아는지 선택하세요. 1 아주 모른다 · 2 모른다 · 3 보통이다 · 4 안다 · 5 잘 안다. 자기보고 이해도이며 실무 능력 인증은 아닙니다.")
        pre_assessment = assessment_service.get_assessment(st.session_state.session_id, "PRE")
        questions = pre_assessment.get("questions", [])
        if not questions:
            st.warning("아직 설문이 없습니다.")
            if st.session_state.current_stage == 3 and st.button("사전 설문 생성"):
                with st.spinner("프로젝트와 DB 스킬을 바탕으로 설문 생성 중..."):
                    questions = assessment_agent.generate_pre_assessment(st.session_state.session_id)
                    assessment_service.save_assessment_questions(st.session_state.session_id, "PRE", questions)
                st.rerun()
            st.stop()
        try:
            validate_questions(questions)
        except ValueError as exc:
            st.warning(str(exc))
            if st.session_state.current_stage == 3 and not pre_assessment.get("responses"):
                st.info("아직 응답하지 않은 기존 설문입니다. 프로젝트에 필요한 DB 스킬을 선택하면 5점 설문으로 다시 생성할 수 있습니다.")
                repair_skills = st.multiselect("설문에 사용할 DB 스킬 태그", names, key=f"repair_skills_{st.session_state.session_id}")
                if st.button("선택한 DB 스킬로 5점 설문 재생성", disabled=not repair_skills):
                    with st.spinner("DB 스킬에 맞춰 5점 설문을 생성하는 중..."):
                        replacement = assessment_agent.generate_pre_assessment(st.session_state.session_id, skill_names=repair_skills)
                        assessment_service.save_pre_assessment_with_skills(st.session_state.session_id, repair_skills, replacement)
                    st.rerun()
            else:
                st.info("기존 응답은 유지됩니다. 새 프로젝트에서 5점 설문을 시작해 주세요.")
            st.stop()
        saved_answers = {r["question_id"]: r for r in pre_assessment.get("responses", [])}
        st.caption(f"총 {len(questions)}문항 · 모든 문항에 응답해야 저장됩니다.")
        editable = st.session_state.current_stage == 3
        with st.form(f"form_pre_assessment_{st.session_state.session_id}"):
            user_answers = []
            for q in questions:
                qid = q["question_id"]
                opts = list(LIKERT_OPTIONS)
                saved_choice = saved_answers.get(qid, {}).get("selected_option")
                choice = st.radio(f"{q['sequence_no']}. [{q['skill']}] {q['question']}", opts,
                                  index=opts.index(saved_choice) if saved_choice in opts else None,
                                  key=f"pre_opt_{qid}", disabled=not editable)
                user_answers.append({"question_id": qid, "selected_option": choice,
                                     "score": opts.index(choice) + 1 if choice in opts else None})
            submit_pre = st.form_submit_button("사전 설문 응답 저장", type="primary", width="stretch", disabled=not editable)
            generate_pre = st.form_submit_button("저장된 진단으로 로드맵 3개 생성", width="stretch", disabled=not editable or not saved_answers)
        if submit_pre:
            assessment_service.save_assessment_responses(st.session_state.session_id, "PRE", user_answers)
            st.rerun()

        assessment_summary = None
        if saved_answers:
            assessment_summary = assessment_service.get_assessment_summary(st.session_state.session_id, "PRE")
            st.success("사전 설문 응답이 DB에 저장되어 있습니다.")
            st.dataframe([{"스킬": s["skill"], "문항 수": s["question_count"],
                           "자기보고 평균 (1~5)": s["mean_score"]} for s in assessment_summary["skills"]],
                         hide_index=True, width="stretch")
            with st.expander("로드맵 에이전트에 전달할 진단 입력 확인"):
                st.json(assessment_summary)
        # Unsaved edits must not silently feed an older assessment into the roadmap.
        answers_unchanged = bool(saved_answers) and all(
            saved_answers.get(a["question_id"], {}).get("score") == a["score"]
            and saved_answers.get(a["question_id"], {}).get("selected_option") == a["selected_option"]
            for a in user_answers)
        if editable and generate_pre:
            if not answers_unchanged:
                st.error("변경한 응답을 먼저 저장해 주세요.")
                st.stop()
            with st.spinner("DB 데이터를 기반으로 3개 로드맵 대안 생성 중..."):
                plan = assessment_service.get_project_plan(st.session_state.session_id)
                grounding = recommendation_service.retrieve_grounding_context(
                    target_skills=[item["skill"] for item in assessment_summary["skills"]],
                    target_job_title=user_profile["job_title"],
                    assessment_summary=assessment_summary,
                    project_summary=plan.get("refined_plan") or plan.get("draft_plan") or ""
                )
                orch_profile = dict(user_profile)
                orch_profile["target_skills"] = [s["skill"] for s in assessment_summary["skills"]]
                orch_profile["work_context_summary"] = plan.get("refined_plan", plan.get("draft_plan", ""))

                orch = multi_agent_orchestrator.generate_all_roadmaps(
                    user_context=orch_profile,
                    conversation_history=[],
                    retrieved_context=grounding,
                    hidden_assessment_responses=assessment_summary
                )
                results = orch.get("results", {})
                if len(results) != 3 or any(result.get("status") != "SUCCESS" for result in results.values()):
                    st.error("로드맵 생성 실패: 결과를 저장하지 않았습니다.")
                    for result in results.values():
                        if result.get("status") != "SUCCESS":
                            st.error(result.get("summary", "생성 실패"))
                    st.stop()
                roadmap_gens = []
                for k, v in orch.get("results", {}).items():
                    content = dict(v.get("roadmap_content", {}))
                    content["assessment_input"] = assessment_summary
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
                <b>계획 기간:</b> {duration_label(content.get('total_duration_weeks'))}
            </div>
        </div>
    </div>
    """,
                    unsafe_allow_html=True
                )
                st.write("")
                if st.button("이 로드맵 채택하기", key=f"btn_choose_{gid}", width="stretch"):
                    detailed_report = build_selection_report(content)
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
                "total_duration_weeks")
            proj_name = st.session_state.get("project_name", "엔지니어링 과제")

            # 1. 상단 배너
            st.markdown(
                f"""
    <div style="background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%); padding: 22px; border-radius: 10px; color: #FFFFFF; margin-bottom: 20px;">
        <h2 style="margin: 0; color: #FFFFFF; font-size: 22px;">📘 채택된 청사진: {strat_title}</h2>
        <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px;">계획 기간: {duration_label(total_weeks)} | 엔지니어: {u_name} ({u_grade}) | 과제명: {proj_name}</p>
    </div>
    """,
                unsafe_allow_html=True
            )

            st.markdown(f"**전략 요약:** {rep_content.get('project_summary', '')}")
            st.markdown(f"**선택 근거:** {rep_content.get('why_this_roadmap', '')}")
            st.markdown(f"**핵심 엔지니어링 목표:** {rep_content.get('project_goal', '')}")

            st.caption("기간·실행 과제·기대 효과는 생성된 계획 제안이며 검증된 성과가 아닙니다.")
            evidence = rep_content.get("resource_grounding", {})
            if evidence.get("status") == "no_evidence":
                st.warning("선택한 스킬에 대해 추천 가능한 DB 학습 자료가 없습니다. 강좌·도서·자격증 연결 데이터를 확인하세요.")
            with st.expander("학습 자료 검색 근거 확인"):
                st.json(evidence.get("retrieval", {}))

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
                dur_val = ms.get("duration") or "기간 미정"
                goal_val = ms.get("focus_goal", "")
                impact_val = ms.get("project_impact", "")
                table_rows.append(f"| **{p_name_val}** | {dur_val} | {goal_val} | {impact_val} |")

            st.markdown(
                "| 마일스톤 | 계획 기간 (추정) | 핵심 엔지니어링 목표 | 기술적 파급 효과 |\n| :--- | :---: | :--- | :--- |\n" + "\n".join(table_rows))

            # 4. 세부 과제 및 RAG 자원 명세
            st.markdown("---")
            st.markdown("### 🛠 단계별 세부 실행 과제 및 RAG 추천 학습 자원 명세")
            for ms in phases:
                p_title = ms.get("phase_name", f"Phase {ms.get('phase', 1)}")
                with st.expander(f"📌 {p_title} (기간: {ms.get('duration') or '기간 미정'}) - 세부 과제 및 추천 자원 열기", expanded=True):
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
                                    f"- [{c.get('title')}]({c['url']}) ({c.get('platform') or '플랫폼 미등록'})"
                                    if c.get("url") else f"- {c.get('title')} — URL 미등록")
                        else:
                            st.caption("매칭된 온라인 강좌 없음")

                        st.markdown("**🏆 연계 공인 자격증**")
                        certs = ms.get("recommended_certifications", [])
                        if certs:
                            for cert in certs:
                                st.markdown(f"- **{cert.get('title')}** `({cert.get('provider') or '기관 미등록'})`")
                        else:
                            st.caption("매칭된 공인 자격증 없음")

                        st.markdown("**📖 핵심 참고 도서**")
                        books = ms.get("recommended_books", [])
                        if books:
                            for b in books:
                                st.markdown(f"- *{b.get('title')}* (저자: {b.get('author') or '저자 미등록'})")
                        else:
                            st.caption("매칭된 참고 도서 없음")

            # 5. 6단계 진입 트리거
            st.markdown("---")
            if st.session_state.current_stage == 5:
                if st.button("🏁 프로젝트 수행 완료 및 6단계 Post-Test 시작", type="primary", width="stretch"):
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
        st.markdown('<div class="stage-bar stage-6">6단계: Post-Test (동일 문항으로 자기보고 이해도 재측정)</div>',
                    unsafe_allow_html=True)
        st.info("사전과 동일한 문항입니다. 현재의 이해도를 선택하세요. 이전보다 낮거나 같은 점수여도 괜찮습니다.")
        post_assessment = assessment_service.get_assessment(st.session_state.session_id, "POST")
        post_questions = post_assessment.get("questions", [])
        validate_questions(post_questions)
        saved_post = {r["question_id"]: r for r in post_assessment.get("responses", [])}
        post_editable = st.session_state.current_stage == 6
        with st.form(f"form_post_assessment_{st.session_state.session_id}"):
            post_answers = []
            for q in post_questions:
                qid = q["question_id"]
                opts = list(LIKERT_OPTIONS)
                saved_choice = saved_post.get(qid, {}).get("selected_option")
                choice = st.radio(f"{q['sequence_no']}. [{q['skill']}] {q['question']}", opts,
                                  index=opts.index(saved_choice) if saved_choice in opts else None,
                                  key=f"post_opt_{qid}", disabled=not post_editable)
                post_answers.append({"question_id": qid, "selected_option": choice,
                                     "score": opts.index(choice) + 1 if choice in opts else None})
            submit_post = st.form_submit_button("사후 설문 저장 및 자기보고 변화 비교", type="primary", width="stretch", disabled=not post_editable)

        if submit_post:
            assessment_service.save_assessment_responses(st.session_state.session_id, "POST", post_answers)

            with st.spinner("저장된 사전·사후 점수를 비교하는 중..."):
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
            st.subheader("📊 스킬별 자기보고 점수 비교")

            comp_list = report.get("skill_comparison", [])

            # 1. 요약 메트릭 카드
            total_growth = sum(c.get("growth", 0) for c in comp_list)
            avg_growth = round(total_growth / len(comp_list), 1) if comp_list else 0

            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("평가 역량 축 수", f"{len(comp_list)}개 스킬")
            with m_col2:
                st.metric("스킬별 평균 변화 합계", f"{total_growth:+.2f}점")
            with m_col3:
                st.metric("스킬별 평균 점수 변화", f"{avg_growth:+.2f}점", delta=avg_growth)

            # 2. 정밀 비교 표
            comp_rows = []
            for c in comp_list:
                sk = c.get("skill", "")
                pre_l = c.get("pre_level", 1)
                post_l = c.get("post_level", 1)
                growth = c.get("growth", 0)

                if growth > 0:
                    badge = f"**+{growth}점 상승**"
                elif growth == 0:
                    badge = "동일 유지 ⚪"
                else:
                    badge = f"{growth}점 하락"

                comp_rows.append(f"| **{sk}** | {pre_l}점 | {post_l}점 | {badge} |")

            st.markdown(
                "| 역량 축 | 사전 진단 (Pre) | 사후 자기보고 (Post) | 점수 차이 |\n| :--- | :---: | :---: | :---: |\n" + "\n".join(
                    comp_rows))

            # 3. 세부 행동 변화 진술문 대조 카드
            st.markdown("---")
            st.markdown("### 🔍 기술별 자기보고 점수 상세 비교")
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
                {pre_l}점 ➔ {post_l}점 ({'+' if growth > 0 else ''}{growth}점)
            </span>
        </div>
        <div style="font-size: 13px; color: #475569; margin-bottom: 4px;"><b>[사전 상태]</b> {pre_text}</div>
        <div style="font-size: 13px; color: #1D4ED8;"><b>[사후 응답]</b> {post_text}</div>
    </div>
    """,
                        unsafe_allow_html=True
                    )

            # 4. 정성 분석 요약
            st.markdown("---")
            st.subheader("비교 결과 해석")
            st.markdown(report.get("overall_summary", ""))

            col_rep1, col_rep2 = st.columns(2)
            with col_rep1:
                st.markdown("### 🌟 높게 응답한 스킬")
                for s in report.get("strengths", []):
                    st.markdown(f"- ✅ {s}")
            with col_rep2:
                st.markdown("### 🎯 향후 지속 보완 영역")
                for imp in report.get("improvement_areas", []):
                    st.markdown(f"- ⚠️ {imp}")

            st.info(f"**비교 안내:** {report.get('final_evaluation', '')}")
except (DatabaseOperationError, ValueError, RuntimeError) as exc:
    st.error(str(exc))
    st.stop()
