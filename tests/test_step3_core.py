import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.pii_masking import pii_masker
from src.core.embedding_service import embedding_service
from src.core.llm_adapter import llm_adapter, CircuitState


def test_pii_masking():
    """개인식별정보(이름, 이메일, 전화번호, 주민번호 등) 마스킹 검증"""
    sample_text = "홍길동 책임연구원의 연락처는 010-1234-5678이며 이메일은 test@sk.com 입니다."
    masked = pii_masker.mask_text(sample_text)

    assert "010-1234-5678" not in masked, "전화번호가 마스킹되지 않았습니다."
    assert "test@sk.com" not in masked, "이메일이 마스킹되지 않았습니다."
    assert "[PHONE]" in masked, "[PHONE] 태그 누락"
    assert "[EMAIL]" in masked, "[EMAIL] 태그 누락"
    print("[PASS] 3-1. PII 마스킹 정규표현식 정상 작동")


def test_cosine_similarity_computation():
    """임베딩 서비스의 코사인 유사도 수학적 연산 정합성 검증"""
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    vec_c = [0.0, 1.0, 0.0]

    sim_identical = embedding_service.cosine_similarity(vec_a, vec_b)
    sim_orthogonal = embedding_service.cosine_similarity(vec_a, vec_c)

    assert abs(sim_identical - 1.0) < 1e-5, "동일 벡터 유사도가 1.0이 아닙니다."
    assert abs(sim_orthogonal - 0.0) < 1e-5, "직교 벡터 유사도가 0.0이 아닙니다."
    print("[PASS] 3-2. 벡터 코사인 유사도 연산 정상")


def test_llm_adapter_circuit_breaker():
    """LLM 어댑터 서킷 브레이커 초기 상태 점검"""
    health = llm_adapter.check_health()
    assert "openai" in health, "OpenAI 상태 점검 항목 누락"
    assert "local_qwen" in health, "Local Qwen 상태 점검 항목 누락"
    assert health["openai"]["circuit_state"] == CircuitState.CLOSED.value, "초기 서킷 브레이커가 CLOSED가 아닙니다."
    print("[PASS] 3-3. LLM 서킷 브레이커 초기 상태(CLOSED) 정상")


if __name__ == "__main__":
    print("=== [Step 3] 코어 컴포넌트 기능 검증 시작 ===")
    test_pii_masking()
    test_cosine_similarity_computation()
    test_llm_adapter_circuit_breaker()
    print("=== [Step 3] 검증 완료 ===\n")