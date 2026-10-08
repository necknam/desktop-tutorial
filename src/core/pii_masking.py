"""
개인식별정보(PII) 마스킹 모듈
파일 경로: core/pii_masking.py
역할: 이름, 사번, 연락처, 이메일, 주민번호 등 개인정보 자동 탐지 및 치환
"""

import re
import copy
from typing import Any, Dict, List, Optional


class PIIMasker:
    """정규표현식 및 엔티티 사전 기반 PII 자동 마스킹 엔진"""

    # 표준 PII 정규표현식 패턴
    PATTERNS = {
        "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "PHONE": re.compile(r"01[016789]-?\d{3,4}-?\d{4}"),
        "TEL": re.compile(r"0\d{1,2}-?\d{3,4}-?\d{4}"),
        "RRN": re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
        "EMP_CODE": re.compile(r"\b(EMP|HR|SKN)[-_]?\d{3,8}\b", re.IGNORECASE),
        "GENERIC_EMP_ID": re.compile(r"\b(사번\s*[:#]?\s*)([A-Za-z0-9_-]{4,12})\b"),
    }

    def mask_text(self, text: str, custom_entities: Optional[List[str]] = None) -> str:
        """
        단일 문자열 내의 개인정보 마스킹 처리
        """
        if not text or not isinstance(text, str):
            return text

        masked = text

        # 1. 특정 커스텀 엔티티(실제 사용자 이름, 사번 등) 우선 치환
        if custom_entities:
            for entity in custom_entities:
                if entity and len(entity.strip()) >= 2:
                    masked = re.sub(re.escape(entity.strip()), "[NAME]", masked)

        # 2. 정규표현식 기반 범용 PII 마스킹
        masked = self.PATTERNS["RRN"].sub("[RRN]", masked)
        masked = self.PATTERNS["EMAIL"].sub("[EMAIL]", masked)
        masked = self.PATTERNS["PHONE"].sub("[PHONE]", masked)
        masked = self.PATTERNS["TEL"].sub("[PHONE]", masked)
        masked = self.PATTERNS["EMP_CODE"].sub("[EMP_ID]", masked)
        masked = self.PATTERNS["GENERIC_EMP_ID"].sub(r"\1[EMP_ID]", masked)

        # 3. 전형적인 한국어 이름 호칭 패턴 마스킹 (예: 홍길동 연구원, 이철수 선임)
        masked = re.sub(
            r"\b([가-힣]{2,4})\s*(선임연구원|연구원|책임연구원|수석연구원|사원|대리|과장|차장|부장|팀장|멘토|님)\b",
            r"[NAME] \2",
            masked
        )

        return masked

    def mask_data_structure(self, data: Any, custom_entities: Optional[List[str]] = None) -> Any:
        """
        딕셔너리, 리스트 등 복합 계층형 데이터 재귀 마스킹
        """
        if isinstance(data, str):
            return self.mask_text(data, custom_entities)
        elif isinstance(data, dict):
            masked_dict = {}
            for k, v in data.items():
                # 키 자체가 민감 필드명인 경우 값 강제 마스킹
                if k in ("profile_name", "employee_name", "display_name", "name"):
                    masked_dict[k] = "[NAME]"
                elif k in ("employee_id", "employee_code", "emp_id", "user_id"):
                    masked_dict[k] = "[EMP_ID]"
                elif k in ("phone", "tel", "mobile"):
                    masked_dict[k] = "[PHONE]"
                elif k in ("email", "mail"):
                    masked_dict[k] = "[EMAIL]"
                else:
                    masked_dict[k] = self.mask_data_structure(v, custom_entities)
            return masked_dict
        elif isinstance(data, list):
            return [self.mask_data_structure(item, custom_entities) for item in data]
        else:
            return copy.deepcopy(data)


# 글로벌 인스턴스
pii_masker = PIIMasker()