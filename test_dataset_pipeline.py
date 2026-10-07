"""
6단계 SFT / DPO 데이터 파이프라인 및 PII 마스킹 검증 스크립트
파일 경로: test_dataset_pipeline.py
역할: PII 마스킹 유효성 검사 -> DPO 페어 (1:N) 생성 -> SFT/DPO JSONL 추출 및 파일 검증
"""

import sys
import os
import json
import logging

# 프로젝트 루트 sys.path 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.pii_masking import pii_masker
from core.dataset_pipeline import dataset_pipeline
from database.db_client import db_client

logger = logging.getLogger("TEST_DATASET_PIPELINE")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def test_pii_masking() -> None:
    logger.info("=== [테스트 1] PII 개인정보 자동 마스킹 검증 ===")

    sample_text = (
        "안녕하세요, 플랫폼개발팀 김백엔 선임연구원(사번: EMP_202409, 연락처: 010-1234-5678, "
        "이메일: baek.kim@company.com, 주민번호: 950101-1234567)의 업무 개발 로드맵입니다."
    )

    masked_text = pii_masker.mask_text(sample_text, custom_entities=["김백엔"])
    logger.info(f"원본 텍스트:\n{sample_text}")
    logger.info(f"마스킹 결과:\n{masked_text}\n")

    assert "김백엔" not in masked_text, "이름이 마스킹되어야 합니다."
    assert "010-1234-5678" not in masked_text, "전화번호가 마스킹되어야 합니다."
    assert "baek.kim@company.com" not in masked_text, "이메일이 마스킹되어야 합니다."
    assert "950101-1234567" not in masked_text, "주민번호가 마스킹되어야 합니다."
    assert "EMP_202409" not in masked_text, "사번이 마스킹되어야 합니다."

    assert "[NAME]" in masked_text
    assert "[PHONE]" in masked_text
    assert "[EMAIL]" in masked_text
    assert "[RRN]" in masked_text
    assert "[EMP_ID]" in masked_text
    logger.info("-> PII 마스킹 정규식/엔티티 치환 완벽 통과\n")


def test_dpo_pair_generation() -> None:
    logger.info("=== [테스트 2] DPO Chosen 1 : Rejected N 선호도 페어 생성 검증 ===")

    # 4단계에서 생성된 TEST_MANUAL 및 REAL_USER 데이터를 바탕으로 페어 생성
    pairs = dataset_pipeline.build_preference_pairs(user_type=None)
    logger.info(f"생성/동기화된 DPO 페어 수: {len(pairs)}건")

    assert len(pairs) >= 2, "1개 요청당 최소 2개의 DPO 페어(Chosen 1 : Rejected 2)가 생성되어야 합니다."

    sample_pair = pairs[0]
    logger.info(f"샘플 페어 ID: {sample_pair.get('pair_id')}")
    logger.info(f"요청 ID: {sample_pair.get('request_id')}")
    logger.info(f"Chosen 생성 ID: {sample_pair.get('chosen_generation_id')} vs Rejected: {sample_pair.get('rejected_generation_id')}\n")

    assert sample_pair["chosen_generation_id"] != sample_pair["rejected_generation_id"]
    logger.info("-> DPO 1:N 선호도 페어 생성 및 DB 동기화 통과\n")


def test_jsonl_exports() -> None:
    logger.info("=== [테스트 3] SFT 및 DPO JSONL 파일 익스포트 검증 ===")

    export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
    sft_file = os.path.join(export_dir, "sft_training.jsonl")
    dpo_file = os.path.join(export_dir, "dpo_training.jsonl")

    # 1. SFT JSONL 추출 (전체 데이터 대상)
    sft_count = dataset_pipeline.export_sft_jsonl(output_path=sft_file, user_type="", mask_pii=True)
    logger.info(f"SFT 레코드 내보내기 결과: {sft_count}건")
    assert sft_count > 0, "SFT 데이터가 1건 이상 추출되어야 합니다."
    assert os.path.exists(sft_file), "SFT JSONL 파일이 생성되어야 합니다."

    # SFT 파일 라인별 구조 검증
    with open(sft_file, "r", encoding="utf-8") as f:
        first_line = f.readline()
        item = json.loads(first_line)
        assert "messages" in item, "ChatML 표준 messages 키가 존재해야 합니다."
        assert len(item["messages"]) == 3, "system, user, assistant 3개 메시지로 구성되어야 합니다."
        logger.info(f"SFT 첫 행 구조 검증 완료: {[m['role'] for m in item['messages']]}")

    # 2. DPO JSONL 추출 (전체 데이터 대상)
    dpo_count = dataset_pipeline.export_dpo_jsonl(output_path=dpo_file, user_type="", mask_pii=True)
    logger.info(f"DPO 레코드 내보내기 결과: {dpo_count}건")
    assert dpo_count > 0, "DPO 데이터가 1건 이상 추출되어야 합니다."
    assert os.path.exists(dpo_file), "DPO JSONL 파일이 생성되어야 합니다."

    # DPO 파일 라인별 구조 검증
    with open(dpo_file, "r", encoding="utf-8") as f:
        first_line = f.readline()
        item = json.loads(first_line)
        assert "prompt" in item, "DPO 표준 prompt 키가 존재해야 합니다."
        assert "chosen" in item, "DPO 표준 chosen 키가 존재해야 합니다."
        assert "rejected" in item, "DPO 표준 rejected 키가 존재해야 합니다."
        logger.info("DPO 첫 행 구조 검증 완료: prompt, chosen, rejected 키 확인")

    # 3. finetuning_datasets 이력 기록 확인
    resp = db_client.client.table("finetuning_datasets").select("*").execute()
    assert len(resp.data) >= 2, "SFT 및 DPO 익스포트 로그가 finetuning_datasets 테이블에 기록되어야 합니다."
    logger.info(f"finetuning_datasets 테이블 등록 로그 수: {len(resp.data)}건\n")

    logger.info("=== [성공] 6단계 SFT / DPO 데이터 파이프라인 및 PII 마스킹 검증 완료 ===")


if __name__ == "__main__":
    test_pii_masking()
    test_dpo_pair_generation()
    test_jsonl_exports()