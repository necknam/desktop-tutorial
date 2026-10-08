"""
SFT / DPO 파인튜닝 데이터셋 파이프라인 모듈
파일 경로: src/core/dataset_pipeline.py
역할: Chosen 1 : Rejected N 페어 생성, PII 마스킹, SFT/DPO JSONL 파일 익스포트
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from src.db.db_client import db_client
from src.core.pii_masking import pii_masker

logger = logging.getLogger("DATASET_PIPELINE")


class DatasetPipeline:
    """Local Qwen3.5 파인튜닝용 SFT 및 DPO 데이터셋 추출 파이프라인"""

    def __init__(self) -> None:
        self.db = db_client
        self.masker = pii_masker

    def build_preference_pairs(
        self,
        request_id: Optional[str] = None,
        user_type: Optional[str] = "REAL_USER"
    ) -> List[Dict[str, Any]]:
        """
        사용자 선택 로그를 기반으로 Chosen 1 : Rejected N 선호도 페어 생성 및 DB 동기화
        - user_type: None 지정 시 모든 사용자 유형(테스트 포함) 처리
        """
        if self.db.client is None:
            return []

        created_pairs = []

        try:
            query = self.db.client.table("agent_selections").select("*")
            if request_id:
                query = query.eq("request_id", request_id)
            elif user_type:
                query = query.eq("user_type", user_type)

            sel_resp = query.execute()
            selections = sel_resp.data or []

            for sel in selections:
                req_id = sel["request_id"]
                chosen_gid = sel["chosen_generation_id"]
                u_type = sel["user_type"]

                req_resp = self.db.client.table("roadmap_requests").select("*").eq("request_id", req_id).execute()
                gen_resp = self.db.client.table("agent_generations").select("*").eq("request_id", req_id).execute()

                if not req_resp.data or not gen_resp.data:
                    continue

                req_data = req_resp.data[0]
                all_gens = gen_resp.data

                chosen_gen = next((g for g in all_gens if g["generation_id"] == chosen_gid), None)
                rejected_gens = [g for g in all_gens if g["generation_id"] != chosen_gid]

                if not chosen_gen or not rejected_gens:
                    continue

                prompt_input = {
                    "raw_user_prompt": req_data.get("raw_user_prompt"),
                    "user_requirements": req_data.get("user_requirements"),
                    "retrieved_context": req_data.get("retrieved_context"),
                    "conversation_history": req_data.get("conversation_history")
                }

                for rej in rejected_gens:
                    pair_payload = {
                        "request_id": req_id,
                        "chosen_generation_id": chosen_gid,
                        "rejected_generation_id": rej["generation_id"],
                        "prompt_input": prompt_input,
                        "chosen_output": {
                            "agent_id": chosen_gen.get("agent_id"),
                            "summary": chosen_gen.get("summary"),
                            "roadmap_content": chosen_gen.get("roadmap_content")
                        },
                        "rejected_output": {
                            "agent_id": rej.get("agent_id"),
                            "summary": rej.get("summary"),
                            "roadmap_content": rej.get("roadmap_content")
                        },
                        "user_type": u_type
                    }

                    try:
                        chk_resp = self.db.client.table("preference_pairs").select("pair_id") \
                            .eq("request_id", req_id) \
                            .eq("chosen_generation_id", chosen_gid) \
                            .eq("rejected_generation_id", rej["generation_id"]) \
                            .execute()

                        if not chk_resp.data:
                            ins_resp = self.db.client.table("preference_pairs").insert(pair_payload).execute()
                            if ins_resp.data:
                                created_pairs.append(ins_resp.data[0])
                        else:
                            created_pairs.append(chk_resp.data[0])
                    except Exception as ins_err:
                        logger.error(f"선호도 페어 개별 레코드 저장 실패: {ins_err}")

            logger.info(f"DPO 선호도 페어 총 {len(created_pairs)}건 동기화 완료")
            return created_pairs

        except Exception as exc:
            logger.error(f"선호도 페어 구축 중 예외 발생: {exc}")
            return []

    def export_sft_jsonl(
        self,
        output_path: str,
        user_type: str = "REAL_USER",
        mask_pii: bool = True
    ) -> int:
        """
        최종 채택된 Chosen 결과 기반 SFT JSONL (ChatML / OpenAI messages 포맷) 추출
        """
        if self.db.client is None:
            return 0

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        count = 0

        try:
            query = self.db.client.table("agent_selections").select("*")
            if user_type:
                query = query.eq("user_type", user_type)
            selections = query.execute().data or []

            with open(output_path, "w", encoding="utf-8") as f:
                for sel in selections:
                    req_resp = self.db.client.table("roadmap_requests").select("*").eq("request_id", sel["request_id"]).execute()
                    gen_resp = self.db.client.table("agent_generations").select("*").eq("generation_id", sel["chosen_generation_id"]).execute()

                    if not req_resp.data or not gen_resp.data:
                        continue

                    req = req_resp.data[0]
                    gen = gen_resp.data[0]

                    p_resp = self.db.client.table("prompt_versions").select("system_prompt").eq("agent_id", gen["agent_id"]).eq("is_active", True).execute()
                    sys_prompt = p_resp.data[0]["system_prompt"] if p_resp.data else "당신은 업무 개발 로드맵 전문가입니다."

                    user_context_str = json.dumps({
                        "user_requirements": req.get("user_requirements", {}),
                        "retrieved_context": req.get("retrieved_context", {}),
                        "user_prompt": req.get("raw_user_prompt", "")
                    }, ensure_ascii=False)

                    assistant_output_str = json.dumps(gen.get("roadmap_content", {}), ensure_ascii=False)

                    sft_entry = {
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_context_str},
                            {"role": "assistant", "content": assistant_output_str}
                        ]
                    }

                    if mask_pii:
                        sft_entry = self.masker.mask_data_structure(sft_entry)

                    f.write(json.dumps(sft_entry, ensure_ascii=False) + "\n")
                    count += 1

            self._log_dataset_export("SFT_JSONL", count, output_path, mask_pii, {"user_type": user_type or "ALL"})
            logger.info(f"SFT JSONL 추출 완료: {count}건 -> {output_path}")
            return count

        except Exception as exc:
            logger.error(f"SFT JSONL 추출 실패: {exc}")
            return 0

    def export_dpo_jsonl(
        self,
        output_path: str,
        user_type: str = "REAL_USER",
        mask_pii: bool = True
    ) -> int:
        """
        Chosen 1 : Rejected N 기반 DPO JSONL (HuggingFace trl 표준 포맷) 추출
        """
        if self.db.client is None:
            return 0

        self.build_preference_pairs(user_type=user_type if user_type else None)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        count = 0

        try:
            query = self.db.client.table("preference_pairs").select("*")
            if user_type:
                query = query.eq("user_type", user_type)
            pairs = query.execute().data or []

            with open(output_path, "w", encoding="utf-8") as f:
                for pair in pairs:
                    prompt_str = json.dumps(pair.get("prompt_input", {}), ensure_ascii=False)
                    chosen_str = json.dumps(pair.get("chosen_output", {}), ensure_ascii=False)
                    rejected_str = json.dumps(pair.get("rejected_output", {}), ensure_ascii=False)

                    dpo_entry = {
                        "prompt": prompt_str,
                        "chosen": chosen_str,
                        "rejected": rejected_str
                    }

                    if mask_pii:
                        dpo_entry = self.masker.mask_data_structure(dpo_entry)

                    f.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                    count += 1

            self._log_dataset_export("DPO_JSONL", count, output_path, mask_pii, {"user_type": user_type or "ALL"})
            logger.info(f"DPO JSONL 추출 완료: {count}건 -> {output_path}")
            return count

        except Exception as exc:
            logger.error(f"DPO JSONL 추출 실패: {exc}")
            return 0

    def _log_dataset_export(
        self,
        dataset_type: str,
        record_count: int,
        export_path: str,
        pii_masked: bool,
        filter_criteria: Dict[str, Any]
    ) -> None:
        """finetuning_datasets 테이블에 내보내기 이력 기록"""
        try:
            payload = {
                "dataset_type": dataset_type,
                "record_count": record_count,
                "export_path": export_path,
                "pii_masked": pii_masked,
                "filter_criteria": filter_criteria
            }
            self.db.client.table("finetuning_datasets").insert(payload).execute()
        except Exception as exc:
            logger.warning(f"데이터셋 익스포트 로그 기록 실패: {exc}")


dataset_pipeline = DatasetPipeline()