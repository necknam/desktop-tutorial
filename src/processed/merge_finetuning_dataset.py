"""
생성된 파인튜닝 케이스들을 스캔하여 기생성분 내 미세 결함([NAME] 엔지니어, 산출물1/2, 스킬명 생략 설문 등)까지
자동으로 정제(Retroactive Sanitization)한 뒤 순수 {"messages": [...]} 구조만 단일 JSON 및 JSONL로 병합하는 모듈
파일 경로: src/processed/merge_finetuning_dataset.py

실행 방법:
  python -m src.processed.merge_finetuning_dataset
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.config_loader import PROJECT_ROOT
from src.common.pii_masking import pii_masker
from src.pipelines.export_finetuning_dataset import (
    fix_typos_in_structure,
    synthesize_markdown_plan,
    _normalize_likert_question,
    _normalize_duration,
)


def canonical_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def compute_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


def extract_char_ngrams(text: str, n: int = 3) -> Set[str]:
    cleaned = re.sub(r"\s+", "", text.lower())
    if not cleaned:
        return set()
    if len(cleaned) < n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def is_near_duplicate(
    assistant_text: str,
    task_bucket: List[Set[str]],
    threshold: float = 0.85,
    max_window: int = 1000,
) -> bool:
    grams = extract_char_ngrams(assistant_text, n=3)
    if not grams:
        return False
    for existing_grams in task_bucket:
        intersection = len(grams & existing_grams)
        union = len(grams | existing_grams)
        if union > 0 and (intersection / union) >= threshold:
            return True
    if len(task_bucket) >= max_window:
        task_bucket.pop(0)
    task_bucket.append(grams)
    return False


def retro_sanitize_messages(task_name: str, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    기생성된 케이스 파일 내부의 messages에 대해:
    1) [NAME] 엔지니어 / [NAME] 연구원 복원 및 오타(모놀리즘, 서킷 브ーカー, 패스트랙 등) 교정
    2) assessment_plan_refine: '산출물1, 산출물2' 더미 제거 및 '5.0 / 마크다운 형식의...' 접두사 정리
    3) assessment_survey_gen: 스킬명이 생략된 질문에 스킬명 자동 결합 + 5점 척도 의문문 정규화
    4) roadmap 3종: user 프롬프트 내 '산출물1, 산출물2' 치환 및 Hidden Context 설문 문항에 스킬명 동기화
    """
    cleaned_msgs = fix_typos_in_structure(messages)
    if len(cleaned_msgs) != 3:
        return cleaned_msgs

    user_str = cleaned_msgs[1]["content"]
    asst_str = cleaned_msgs[2]["content"]

    try:
        if task_name == "assessment_plan_refine":
            asst_obj = json.loads(asst_str)
            if isinstance(asst_obj, dict):
                techs = asst_obj.get("tech_stack") or ["대상 기술"]
                skills_label = ", ".join(str(t) for t in techs)
                raw_deliv = asst_obj.get("deliverables", [])
                if isinstance(raw_deliv, list):
                    clean_deliv = [
                        re.sub(r"^산출물\s*\d+\s*[:：]\s*", "", str(d).strip())
                        for d in raw_deliv
                        if not re.match(r"^산출물\s*\d*$", str(d).strip())
                    ]
                    if not clean_deliv:
                        clean_deliv = [
                            f"{skills_label} 표준 아키텍처 설계서 및 인터페이스 명세서",
                            "핵심 모듈 프로토타입 코드 및 자동화 검증 리포트",
                        ]
                    asst_obj["deliverables"] = clean_deliv

                raw_plan = str(asst_obj.get("refined_plan") or "").strip()
                raw_plan = re.sub(
                    r"^(?:확정 계획 마크다운 전문|마크다운 형식의 최종 계획 전문|\d+\.\d+)\s*",
                    "",
                    raw_plan,
                ).strip()
                if len(raw_plan) < 60:
                    asst_obj["refined_plan"] = synthesize_markdown_plan(asst_obj)
                else:
                    asst_obj["refined_plan"] = raw_plan
                cleaned_msgs[2]["content"] = canonical_dumps(asst_obj)

        elif task_name == "assessment_survey_gen":
            user_obj = json.loads(user_str)
            asst_arr = json.loads(asst_str)
            if isinstance(user_obj, dict) and isinstance(asst_arr, list):
                raw_p = str(user_obj.get("project_plan") or "").strip()
                raw_p = re.sub(
                    r"^(?:확정 계획 마크다운 전문|마크다운 형식의 최종 계획 전문|\d+\.\d+)\s*",
                    "",
                    raw_p,
                ).strip()
                user_obj["project_plan"] = raw_p if raw_p else user_obj.get("project_goal", "")
                cleaned_msgs[1]["content"] = json.dumps(user_obj, ensure_ascii=False)

                slots = user_obj.get("slots", [])
                for idx, q_item in enumerate(asst_arr):
                    if isinstance(q_item, dict) and "question" in q_item:
                        slot = slots[idx] if idx < len(slots) and isinstance(slots[idx], dict) else {}
                        q_item["question"] = _normalize_likert_question(
                            str(q_item["question"]),
                            str(slot.get("skill", "대상 기술")),
                            str(slot.get("focus", "핵심 개념")),
                        )
                cleaned_msgs[2]["content"] = canonical_dumps(asst_arr)

        elif task_name in ("agent_practical", "agent_certified", "agent_fasttrack"):
            # User 프롬프트 내 '산출물1, 산출물2' 더미 텍스트 정제
            if "산출물1, 산출물2" in user_str:
                user_str = user_str.replace(
                    "산출물1, 산출물2",
                    "표준 아키텍처 설계서 및 인터페이스 명세서, 핵심 모듈 구현 코드 및 검증 리포트",
                )
            cleaned_msgs[1]["content"] = user_str

            asst_obj = json.loads(asst_str)
            if isinstance(asst_obj, dict):
                if isinstance(asst_obj.get("summary"), str):
                    asst_obj["summary"] = re.sub(
                        r"^(?:2~3문장\s*요약|핵심\s*요약)\s*[:：]\s*", "", asst_obj["summary"].strip()
                    )
                for ms in asst_obj.get("milestones", []):
                    if isinstance(ms, dict):
                        ms["duration"] = _normalize_duration(ms.get("duration"))
                        if isinstance(ms.get("key_actions"), list):
                            ms["key_actions"] = [
                                str(a).replace("산출물(산출물1, 산출물2)", "핵심 산출물(설계서 및 구현 모듈)").replace("산출물 1/2", "핵심 산출물")
                                for a in ms["key_actions"]
                            ]
                cleaned_msgs[2]["content"] = canonical_dumps(asst_obj)
    except Exception:
        pass

    return cleaned_msgs


def discover_default_input_dirs() -> List[Path]:
    processed_root = PROJECT_ROOT / "data" / "processed"
    if not processed_root.exists():
        return [processed_root / "qwen27b_sft_3000"]
    discovered = [p.parent for p in sorted(processed_root.glob("*/cases")) if p.is_dir()]
    return discovered if discovered else [processed_root / "qwen27b_sft_3000"]


def collect_case_files(input_dirs: List[Path]) -> List[Path]:
    collected: List[Path] = []
    seen_paths: Set[Path] = set()
    excluded_names = {
        "manifest.json",
        "schema.json",
        "catalog.json",
        "growpath_3000_finetuning_dataset.json",
        "growpath_3000_finetuning_dataset_report.json",
    }

    for d in input_dirs:
        target = d.resolve() if d.is_absolute() else (PROJECT_ROOT / d).resolve()
        if not target.exists():
            continue

        cases_dir = target / "cases"
        if cases_dir.is_dir():
            candidates = sorted(cases_dir.glob("*.json"))
        else:
            candidates = sorted(
                p for p in target.rglob("*.json")
                if p.name not in excluded_names and not p.name.endswith("_report.json")
            )

        for file_path in candidates:
            resolved = file_path.resolve()
            if resolved not in seen_paths and file_path.is_file():
                seen_paths.add(resolved)
                collected.append(resolved)

    return collected


def extract_pure_messages_from_file(raw_data: Dict[str, Any], include_unreviewed: bool = False) -> List[Tuple[str, List[Dict[str, str]]]]:
    extracted: List[Tuple[str, List[Dict[str, str]]]] = []

    if isinstance(raw_data.get("records"), list):
        for rec in raw_data["records"]:
            if isinstance(rec, dict) and isinstance(rec.get("messages"), list) and len(rec["messages"]) == 3:
                task_name = str(rec.get("task", "unknown"))
                sanitized = retro_sanitize_messages(task_name, rec["messages"])
                extracted.append((task_name, sanitized))
        return extracted

    if isinstance(raw_data.get("candidates"), list):
        for cand in raw_data["candidates"]:
            if not isinstance(cand, dict):
                continue
            if not bool(cand.get("accepted", False)) and not include_unreviewed:
                continue
            sys_val = cand.get("system")
            usr_val = cand.get("input")
            ast_val = cand.get("output")
            sys_str = sys_val.strip() if isinstance(sys_val, str) else canonical_dumps(sys_val)
            usr_str = usr_val.strip() if isinstance(usr_val, str) else canonical_dumps(usr_val)
            ast_str = ast_val.strip() if isinstance(ast_val, str) else canonical_dumps(ast_val)
            if sys_str and usr_str and ast_str:
                task_name = str(cand.get("task", "unknown"))
                msgs = [
                    {"role": "system", "content": sys_str},
                    {"role": "user", "content": usr_str},
                    {"role": "assistant", "content": ast_str},
                ]
                if pii_masker is not None:
                    msgs = pii_masker.mask_data_structure(msgs)
                sanitized = retro_sanitize_messages(task_name, msgs)
                extracted.append((task_name, sanitized))

    return extracted


def merge_datasets(
    input_dirs: List[Path],
    output_prefix: Path,
    include_unreviewed: bool = False,
    dedup_threshold: float = 0.85,
) -> Dict[str, Any]:
    case_files = collect_case_files(input_dirs)
    if not case_files:
        raise FileNotFoundError(
            "병합할 케이스 JSON 파일(cases/*.json)을 찾을 수 없습니다. "
            "먼저 export_finetuning_dataset 파이프라인을 실행해 케이스를 생성하세요."
        )

    total_files = len(case_files)
    print(f"📦 [순수 messages 정제 및 병합 시작] 총 {total_files}개의 케이스 파일을 스캔합니다...", flush=True)

    stats: Counter = Counter()
    task_counts: Counter = Counter()
    split_counts: Counter = Counter()
    seen_hashes: Set[str] = set()
    ngram_buckets: Dict[str, List[Set[str]]] = {}
    group_split_map: Dict[str, str] = {}

    pure_all_records: List[Dict[str, Any]] = []
    pure_train_records: List[Dict[str, Any]] = []
    pure_val_records: List[Dict[str, Any]] = []

    train_groups: Set[str] = set()
    val_groups: Set[str] = set()

    for idx, file_path in enumerate(case_files, 1):
        try:
            if file_path.stat().st_size == 0:
                stats["empty_file_skipped"] += 1
                continue
            raw_data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stats["corrupted_file_skipped"] += 1
            continue

        if not isinstance(raw_data, dict):
            stats["invalid_schema_file_skipped"] += 1
            continue

        stats["cases_read"] += 1
        group_id = str(raw_data.get("group_id") or "default_group")
        raw_split = str(raw_data.get("split") or "train")
        if raw_split not in ("train", "validation"):
            raw_split = "train"
        assigned_split = group_split_map.setdefault(group_id, raw_split)

        pairs = extract_pure_messages_from_file(raw_data, include_unreviewed=include_unreviewed)
        for task_name, messages in pairs:
            stats["total_candidates_seen"] += 1
            pure_item = {"messages": messages}

            msg_hash = compute_sha256(pure_item)
            if msg_hash in seen_hashes:
                stats["exact_duplicate_removed"] += 1
                continue

            assistant_content = messages[-1]["content"]
            bucket = ngram_buckets.setdefault(task_name, [])
            if dedup_threshold < 1.0 and is_near_duplicate(assistant_content, bucket, threshold=dedup_threshold):
                stats["near_duplicate_removed"] += 1
                continue

            seen_hashes.add(msg_hash)
            pure_all_records.append(pure_item)
            task_counts[task_name] += 1
            split_counts[assigned_split] += 1

            if assigned_split == "validation":
                pure_val_records.append(pure_item)
                val_groups.add(group_id)
            else:
                pure_train_records.append(pure_item)
                train_groups.add(group_id)

        if idx % 50 == 0 or idx == total_files:
            print(
                f"  -> [병합 진행중] {idx}/{total_files} 케이스 처리 완료 "
                f"(남은 파일: {total_files - idx}개 | 누적 순수 학습 레코드: {len(pure_all_records)}건)",
                flush=True,
            )

    if not pure_all_records:
        raise ValueError("조건을 만족하는 유효한 학습 데이터가 0건입니다.")

    resolved_prefix = output_prefix.resolve() if output_prefix.is_absolute() else (PROJECT_ROOT / output_prefix).resolve()
    resolved_prefix.parent.mkdir(parents=True, exist_ok=True)

    single_json_path = resolved_prefix.with_suffix(".json")
    tmp_json = single_json_path.with_suffix(".json.tmp")
    tmp_json.write_text(
        json.dumps(pure_all_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_json.replace(single_json_path)

    single_jsonl_path = resolved_prefix.with_suffix(".jsonl")
    tmp_jsonl = single_jsonl_path.with_suffix(".jsonl.tmp")
    with tmp_jsonl.open("w", encoding="utf-8") as f:
        for item in pure_all_records:
            f.write(canonical_dumps(item) + "\n")
    tmp_jsonl.replace(single_jsonl_path)

    train_jsonl_path = resolved_prefix.parent / f"{resolved_prefix.stem}_train.jsonl"
    val_jsonl_path = resolved_prefix.parent / f"{resolved_prefix.stem}_validation.jsonl"
    with train_jsonl_path.open("w", encoding="utf-8") as tf:
        for item in pure_train_records:
            tf.write(canonical_dumps(item) + "\n")
    with val_jsonl_path.open("w", encoding="utf-8") as vf:
        for item in pure_val_records:
            vf.write(canonical_dumps(item) + "\n")

    leakage_groups = len(train_groups & val_groups)

    summary_report = {
        "status": "MERGED_SUCCESS",
        "schema_format": "PURE_MESSAGES_ONLY (Retro-Sanitized v2)",
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "total_merged_records": len(pure_all_records),
        "split_breakdown": dict(split_counts),
        "task_breakdown": dict(task_counts),
        "zero_group_leakage_verified": leakage_groups == 0,
        "processing_stats": dict(stats),
        "output_files": {
            "single_json_array": str(single_json_path),
            "single_jsonl": str(single_jsonl_path),
            "train_jsonl": str(train_jsonl_path),
            "validation_jsonl": str(val_jsonl_path),
        },
    }

    report_path = resolved_prefix.parent / f"{resolved_prefix.stem}_report.json"
    report_path.write_text(json.dumps(summary_report, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary_report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        nargs="+",
        type=Path,
        default=None,
        help="병합할 케이스 폴더 경로 (미지정 시 data/processed/*/cases 자동 탐색)",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=PROJECT_ROOT / "data/processed/growpath_3000_finetuning_dataset",
        help="출력 파일 접두사 (.json 및 .jsonl 동시 생성)",
    )
    parser.add_argument(
        "--all-valid",
        action="store_true",
        help="구형 포맷에서 Judge 심사 탈락 후보까지 포함할 때 사용",
    )
    parser.add_argument(
        "--dedup-threshold",
        type=float,
        default=0.85,
        help="3-gram Jaccard 유사 중복 제거 임계값 (기본 0.85)",
    )
    args = parser.parse_args(argv)

    input_dirs = args.input_dir if args.input_dir else discover_default_input_dirs()

    try:
        report = merge_datasets(
            input_dirs=input_dirs,
            output_prefix=args.output_prefix,
            include_unreviewed=args.all_valid,
            dedup_threshold=args.dedup_threshold,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "MERGE_FAILED",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())