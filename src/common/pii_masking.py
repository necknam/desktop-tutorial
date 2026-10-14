import re
from typing import Any


class PIIMasker:
    """엔터프라이즈 데이터 보안 및 개인정보 비식별화(PII Masking) 전처리 모듈"""

    def __init__(self) -> None:
        self.patterns = {
            "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
            "PHONE": re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
            "SSN": re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
            "IPV4": re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
            "CARD": re.compile(r"\b\d{4}-?\d{4}-?\d{4}-?\d{4}\b"),
        }
        # 직무명(네트워크 엔지니어, 보안 엔지니어 등)이나 직급(선임연구원, 신입사원 등)을 오탐하지 않도록
        # 명시적인 성명 레이블 또는 호칭(님/씨)만 정밀 마스킹
        self.korean_name_context_pattern = re.compile(
            r"(?:이름|성명|담당자명|작성자명|신청자명)\s*[:：]\s*([가-힣]{2,4})"
            r"|(?<![가-힣])([가-힣]{2,3})\s*(?=(?:님|씨)\b)"
        )
        self.excluded_words = {
            "사용자", "관리자", "담당자", "작성자", "신청자", "대상자", "응시자", "후보자", "개발자", "엔지니어", "연구원",
        }

    def mask_text(self, text: str) -> str:
        """단일 텍스트 내의 모든 민감 개인정보 패턴을 안전 토큰으로 치환"""
        if not text or not isinstance(text, str):
            return text

        masked = text
        for pii_type, pattern in self.patterns.items():
            masked = pattern.sub(f"[{pii_type}_MASKED]", masked)

        def _replace_name(match: re.Match) -> str:
            full = match.group(0)
            name = match.group(1) or match.group(2)
            if not name or name in self.excluded_words:
                return full
            return full.replace(name, "[NAME]", 1)

        return self.korean_name_context_pattern.sub(_replace_name, masked)

    def mask_data_structure(self, data: Any) -> Any:
        """Dict, List 등 복합 JSON 데이터 구조를 재귀적으로 순회하며 마스킹 수행"""
        if isinstance(data, str):
            return self.mask_text(data)
        if isinstance(data, dict):
            return {k: self.mask_data_structure(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.mask_data_structure(item) for item in data]
        return data


pii_masker = PIIMasker()
__all__ = ["PIIMasker", "pii_masker"]