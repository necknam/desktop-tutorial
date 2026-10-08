"""
프롬프트 버전 관리 및 롤백 제어 모듈
파일 경로: src/core/prompt_manager.py
역할: DB prompt_versions 조회, 신규 버전 승격, 부모 버전 롤백, 인메모리 프롬프트 동기화
"""

import logging
from typing import Dict, Any, List, Optional
from src.db.db_client import db_client

logger = logging.getLogger("PROMPT_MANAGER")


class PromptManager:
    """에이전트별 시스템 프롬프트 버전 및 수명주기 관리자"""

    def __init__(self) -> None:
        self.db = db_client

    def get_active_prompt(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """에이전트의 현재 활성 프롬프트 조회"""
        return self.db.get_active_prompt_for_agent(agent_id)

    def get_all_active_prompts(self) -> Dict[str, Dict[str, Any]]:
        """전체 에이전트 활성 프롬프트 맵 조회"""
        return self.db.get_active_prompts()

    def get_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """에이전트의 버전 이력 조회"""
        return self.db.get_prompt_history(agent_id)

    def promote_candidate_prompt(
        self,
        agent_id: str,
        candidate_text: str,
        change_reason: str,
        parent_version_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        가드레일 평가를 통과한 후보 프롬프트를 신규 버전으로 승격 및 활성화
        """
        history = self.get_history(agent_id)
        next_version = 1
        if history:
            next_version = max(row.get("version_num", 1) for row in history) + 1

        logger.info(f"[{agent_id}] 새 버전(v{next_version}) 등록 및 활성화 진행...")

        created = self.db.create_prompt_version(
            agent_id=agent_id,
            version_num=next_version,
            system_prompt=candidate_text,
            change_reason=change_reason,
            parent_version_id=parent_version_id,
            is_active=False
        )

        if not created:
            logger.error(f"[{agent_id}] 프롬프트 버전 레코드 생성 실패")
            return None

        new_vid = created["prompt_version_id"]

        success = self.db.activate_prompt_version(new_vid, agent_id)
        if success:
            logger.info(f"[{agent_id}] 신규 프롬프트 v{next_version} 활성화 성공 (ID: {new_vid})")
            created["is_active"] = True
            return created
        else:
            logger.error(f"[{agent_id}] 신규 프롬프트 활성화 전환 실패")
            return None

    def rollback(self, agent_id: str) -> Dict[str, Any]:
        """
        현재 버전에 품질 이상 발생 시 직전 부모 버전으로 원클릭 롤백
        """
        logger.warning(f"[{agent_id}] 프롬프트 롤백 요청 수신...")
        return self.db.rollback_prompt_version(agent_id)


prompt_manager = PromptManager()