"""
2단계 LLM 어댑터 기능 검증 스크립트
파일 경로: test_llm_adapter.py
역할: 정상 호출, Fallback 동작, Circuit Breaker 상태 전이 및 표준 장애 메시지 확인
"""

import sys
import os
import logging

# 프로젝트 루트 sys.path 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.llm_adapter import llm_adapter, DISCONNECTED_STANDARD_MESSAGE, CircuitState

logger = logging.getLogger("TEST_LLM_ADAPTER")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def test_health_check() -> None:
    """1. 헬스체크 검증"""
    logger.info("=== [테스트 1] LLM 어댑터 헬스체크 ===")
    health = llm_adapter.check_health()
    logger.info(f"헬스체크 결과: {health}")
    assert "openai" in health and "local_qwen" in health
    logger.info("-> 헬스체크 통과\n")


def test_generation_pipeline() -> None:
    """2. 일반 질의 파이프라인 (OpenAI 정상 응답 또는 Fallback 확인)"""
    logger.info("=== [테스트 2] 일반 메시지 질의 테스트 ===")
    sample_messages = [
        {"role": "system", "content": "You are a concise HR assistant."},
        {"role": "user", "content": "사원 역량 개발을 위한 한 줄 조언을 출력하세요."}
    ]

    resp = llm_adapter.generate(sample_messages)
    logger.info(f"응답 공급자: {resp.provider}")
    logger.info(f"응답 모델: {resp.model}")
    logger.info(f"지연 시간: {resp.latency_ms} ms")
    logger.info(f"응답 내용: {resp.content}")
    logger.info(f"성공 여부: {resp.success}")

    if resp.success:
        logger.info("-> 질의 응답 성공 (정상 호출 또는 Fallback 동작 확인)\n")
    else:
        assert resp.content == DISCONNECTED_STANDARD_MESSAGE
        logger.warning(f"-> 공급자 미연결 상태: 표준 메시지 '{DISCONNECTED_STANDARD_MESSAGE}' 정확 반환 확인\n")


def test_circuit_breaker_and_fallback_to_standard_error() -> None:
    """3. 양대 공급자 강제 장애 발생 시 Circuit Breaker 및 표준 문구 검증"""
    logger.info("=== [테스트 3] 회로 차단기 및 표준 장애 문구 반환 검증 ===")
    # 고의로 잘못된 키와 엔드포인트 임시 주입
    original_key = llm_adapter.openai_api_key
    original_url = llm_adapter.local_llm_base_url

    llm_adapter.openai_api_key = "invalid_dummy_key"
    llm_adapter._openai_client = None  # 클라이언트 무효화
    llm_adapter.local_llm_base_url = "http://127.0.0.1:9999/v1"  # 존재하지 않는 포트

    sample_messages = [{"role": "user", "content": "테스트"}]

    # 연속 3회 실패시켜 회로 차단기를 OPEN 상태로 전이
    for i in range(1, 4):
        logger.info(f"강제 실패 시도 {i}회차...")
        resp = llm_adapter.generate(sample_messages)
        assert resp.success is False
        assert resp.content == DISCONNECTED_STANDARD_MESSAGE

    # 서킷 브레이커 상태 점검
    assert llm_adapter.breakers["openai"].state == CircuitState.OPEN
    assert llm_adapter.breakers["local_qwen"].state == CircuitState.OPEN
    logger.info("-> 양대 공급자 회로 차단기 OPEN 전이 확인 완료")

    # 4회차 호출 시: 차단기가 OPEN이므로 통신 시도조차 않고 즉시 표준 메시지 반환
    fast_fail_resp = llm_adapter.generate(sample_messages)
    assert fast_fail_resp.content == DISCONNECTED_STANDARD_MESSAGE
    assert fast_fail_resp.latency_ms < 50  # 즉시 반환으로 인한 초저지연 확인
    logger.info(f"-> OPEN 상태 즉시 차단 완료 (소요시간: {fast_fail_resp.latency_ms}ms, 반환: '{fast_fail_resp.content}')")

    # 원래 설정 복원 및 차단기 리셋
    llm_adapter.openai_api_key = original_key
    llm_adapter.local_llm_base_url = original_url
    llm_adapter.breakers["openai"].record_success()
    llm_adapter.breakers["local_qwen"].record_success()
    if original_key:
        from openai import OpenAI
        llm_adapter._openai_client = OpenAI(api_key=original_key, timeout=llm_adapter.timeout)

    logger.info("=== [성공] 2단계 모든 LLM 어댑터 및 장애 복구 테스트 통과 ===\n")


if __name__ == "__main__":
    test_health_check()
    test_generation_pipeline()
    test_circuit_breaker_and_fallback_to_standard_error()