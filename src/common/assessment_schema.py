"""Five-point self-report contract. No DB, network, or generated scores."""
from collections import defaultdict
import hashlib
import json

LIKERT_OPTIONS = ("아주 모른다", "모른다", "보통이다", "안다", "잘 안다")
SCHEMA_VERSION = "skill_self_report_v1"


def validate_questions(questions, *, require_ids=True):
    if not isinstance(questions, list) or not questions:
        raise ValueError("저장된 설문 문항이 없습니다.")
    ids, texts = set(), set()
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            raise ValueError("설문 문항 형식이 올바르지 않습니다.")
        if type(q.get("skill_id")) is not int or q["skill_id"] <= 0:
            raise ValueError("DB 스킬 ID가 없는 구형 설문입니다. 새 프로젝트에서 5점 설문을 생성해 주세요.")
        if not isinstance(q.get("skill"), str) or not q["skill"].strip():
            raise ValueError("문항의 스킬 이름이 없습니다.")
        if q.get("options") != list(LIKERT_OPTIONS):
            raise ValueError("기존 설문의 척도가 다릅니다. 기존 응답을 변환하지 말고 새 프로젝트에서 5점 설문을 생성해 주세요.")
        if type(q.get("sequence_no")) is not int or q["sequence_no"] != i:
            raise ValueError("설문 순서가 올바르지 않습니다.")
        text = q.get("question")
        if not isinstance(text, str) or not 5 <= len(text.strip()) <= 500:
            raise ValueError("설문 문항은 5~500자의 문자열이어야 합니다.")
        normalized = " ".join(text.split()).casefold()
        if normalized in texts:
            raise ValueError("중복 문항이 있습니다. 설문을 다시 생성해 주세요.")
        texts.add(normalized)
        if require_ids:
            qid = str(q.get("question_id") or "")
            if not qid or qid in ids:
                raise ValueError("문항 ID가 없거나 중복되었습니다.")
            ids.add(qid)


def validate_responses(questions, responses, *, complete=True):
    validate_questions(questions)
    if not isinstance(responses, list):
        raise ValueError("응답 형식이 올바르지 않습니다.")
    by_id = {str(q["question_id"]): q for q in questions}
    seen = set()
    for r in responses:
        if not isinstance(r, dict):
            raise ValueError("응답 형식이 올바르지 않습니다.")
        qid = str(r.get("question_id") or "")
        if qid not in by_id or qid in seen:
            raise ValueError("현재 설문에 없는 문항 또는 중복 응답입니다.")
        seen.add(qid)
        score = r.get("score")
        if type(score) is not int or not 1 <= score <= 5:
            raise ValueError("모든 문항에서 응답을 직접 선택해 주세요. 점수는 1~5입니다.")
        if r.get("selected_option") != LIKERT_OPTIONS[score - 1]:
            raise ValueError("선택한 응답과 점수가 일치하지 않습니다.")
    if complete and set(by_id) != seen:
        raise ValueError("모든 문항에 응답한 후 저장해 주세요.")


def summarize_assessment(questions, responses):
    validate_responses(questions, responses)
    answers = {str(r["question_id"]): r for r in responses}
    groups = defaultdict(list)
    rows = []
    for q in questions:
        r = answers[str(q["question_id"])]
        row = {key: q[key] for key in ("question_id", "sequence_no", "skill_id", "skill", "question")}
        row.update(score=r["score"], selected_option=r["selected_option"])
        rows.append(row)
        groups[q["skill_id"]].append(row)
    skills = []
    for skill_id, items in groups.items():
        scores = [r["score"] for r in items]
        skills.append({"skill_id": skill_id, "skill": items[0]["skill"],
                       "question_count": len(items), "mean_score": round(sum(scores) / len(scores), 2),
                       "min_score": min(scores), "max_score": max(scores),
                       "question_ids": [r["question_id"] for r in items]})
    fingerprint = [{k: r[k] for k in ("sequence_no", "skill_id", "question")} for r in rows]
    return {"schema_version": SCHEMA_VERSION, "assessment_method": "self_report",
            "scale": [{"score": i, "label": label} for i, label in enumerate(LIKERT_OPTIONS, 1)],
            "question_count": len(rows), "answered_count": len(rows),
            "overall_mean_score": round(sum(r["score"] for r in rows) / len(rows), 2),
            "questionnaire_sha256": hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "skills": skills, "responses": rows,
            "interpretation": "자기보고 이해도 1~5점. 실무 능력 인증이나 직무 요구 수준이 아니다. 낮은 점수는 학습 우선순위의 참고이며 프로젝트 관련성과 DB 근거를 함께 사용한다. 미측정 스킬의 점수를 추정하지 않는다."}
