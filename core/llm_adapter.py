"""
통합 LLM 어댑터 모듈
파일 경로: core/llm_adapter.py
역할: OpenAI gpt-4o-mini 와 Local Qwen3.5 9B(Ollama/vLLM) 간의 Fallback, Circuit Breaker, 표준 장애 메시지 제어
"""

import os
import time
import logging
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import httpx
from openai import OpenAI
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

logger = logging.getLogger("LLM_ADAPTER")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(name)s - %(message)s")

# 전역 고정 장애 메시지 규격
DISCONNECTED_STANDARD_MESSAGE = "LLM이 연결되어 있지 않습니다."


class CircuitState(Enum):
    """회로 차단기 상태 머신"""
    CLOSED = "CLOSED"        # 정상 상태: 모든 호출 허용
    OPEN = "OPEN"            # 차단 상태: 즉시 호출 차단 후 하위 Fallback 이동
    HALF_OPEN = "HALF_OPEN"  # 시험 상태: 1회 탐색 호출 허용


@dataclass
class LLMResponse:
    """공통 LLM 응답 데이터 규격"""
    content: str
    provider: str
    model: str
    latency_ms: int
    success: bool
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class CircuitBreaker:
    """공급자별 독립 회로 차단기"""

    def __init__(self, provider_name: str, failure_threshold: int = 3, reset_timeout: float = 30.0) -> None:
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        """현재 호출 시도 가능 여부 판단"""
        now = time.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.reset_timeout:
                logger.info(f"[{self.provider_name}] 회로 차단기: OPEN -> HALF_OPEN (복구 탐색 시작)")
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        """호출 성공 시 상태 정상화"""
        if self.state != CircuitState.CLOSED:
            logger.info(f"[{self.provider_name}] 회로 차단기: {self.state.value} -> CLOSED (정상 복구 완료)")
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0

    def record_failure(self, error: Exception) -> None:
        """호출 실패 시 카운트 누적 및 차단 전이"""
        self.consecutive_failures += 1
        self.last_failure_time = time.monotonic()
        logger.warning(
            f"[{self.provider_name}] 호출 실패 ({self.consecutive_failures}/{self.failure_threshold}): {error}"
        )
        if self.consecutive_failures >= self.failure_threshold or self.state == CircuitState.HALF_OPEN:
            logger.error(
                f"[{self.provider_name}] 회로 차단기 가동: OPEN 상태로 전이 ({self.reset_timeout}초간 차단)"
            )
            self.state = CircuitState.OPEN


class LLMAdapter:
    """OpenAI 우선 및 Local Qwen Fallback을 지원하는 통합 LLM 어댑터"""

    def __init__(self) -> None:
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini").strip()

        # Local LLM 설정 (Ollama 기본: http://localhost:11434/v1, vLLM 기본: http://localhost:8000/v1)
        self.local_llm_base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        self.local_llm_model = os.getenv("LOCAL_LLM_MODEL_NAME", "qwen2.5:7b").strip()
        self.local_api_key = os.getenv("LOCAL_LLM_API_KEY", "ollama").strip()

        # 타임아웃 규격 (연결 5초, 읽기 30초)
        self.timeout = httpx.Timeout(timeout=30.0, connect=5.0)

        # 공급자별 서킷 브레이커
        self.breakers = {
            "openai": CircuitBreaker("OpenAI", failure_threshold=3, reset_timeout=30.0),
            "local_qwen": CircuitBreaker("LocalQwen", failure_threshold=3, reset_timeout=30.0),
        }

        # OpenAI 클라이언트 초기화
        self._openai_client: Optional[OpenAI] = None
        if self.openai_api_key:
            self._openai_client = OpenAI(
                api_key=self.openai_api_key,
                timeout=self.timeout,
                max_retries=1
            )

    def _call_openai(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """1순위: OpenAI API 호출"""
        if not self.openai_api_key or not self._openai_client:
            raise ValueError("OPENAI_API_KEY가 환경변수에 설정되지 않았습니다.")
        response = self._openai_client.chat.completions.create(
            model=self.openai_model,
            messages=messages,
            temperature=temperature,
        )
        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("OpenAI로부터 빈 응답이 반환되었습니다.")
        return response.choices[0].message.content.strip()

    def _call_local_qwen(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """2순위: Local Qwen3.5 (Ollama 또는 vLLM OpenAI 호환 엔드포인트) 호출"""
        url = f"{self.local_llm_base_url}/chat/completions"
        payload = {
            "model": self.local_llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False
        }
        headers = {"Authorization": f"Bearer {self.local_api_key}"}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.5,
        force_provider: Optional[str] = None
    ) -> LLMResponse:
        """
        메시지 기반 생성 질의 (Fallback 체인 실행)
        1순위: OpenAI (gpt-4o-mini)
        2순위: Local Qwen3.5 9B (Ollama / vLLM)
        전면 장애: DISCONNECTED_STANDARD_MESSAGE 반환
        """
        start_time = time.monotonic()
        errors: List[str] = []

        # ==================== 1단계: OpenAI 시도 ====================
        if force_provider in (None, "openai"):
            breaker = self.breakers["openai"]
            if breaker.can_execute():
                try:
                    logger.info(f"OpenAI({self.openai_model}) 호출 시도...")
                    t0 = time.monotonic()
                    content = self._call_openai(messages, temperature)
                    latency = int((time.monotonic() - t0) * 1000)
                    breaker.record_success()
                    return LLMResponse(
                        content=content,
                        provider="openai",
                        model=self.openai_model,
                        latency_ms=latency,
                        success=True
                    )
                except Exception as exc:
                    breaker.record_failure(exc)
                    errors.append(f"OpenAI Error: {str(exc)}")
            else:
                errors.append("OpenAI CircuitBreaker is OPEN")

        # ==================== 2단계: Local Qwen Fallback 시도 ====================
        if force_provider in (None, "local_qwen"):
            breaker = self.breakers["local_qwen"]
            if breaker.can_execute():
                try:
                    logger.warning(f"Fallback 가동: Local Qwen({self.local_llm_model}) 호출 시도...")
                    t0 = time.monotonic()
                    content = self._call_local_qwen(messages, temperature)
                    latency = int((time.monotonic() - t0) * 1000)
                    breaker.record_success()
                    return LLMResponse(
                        content=content,
                        provider="local_qwen",
                        model=self.local_llm_model,
                        latency_ms=latency,
                        success=True,
                        metadata={"fallback": True}
                    )
                except Exception as exc:
                    breaker.record_failure(exc)
                    errors.append(f"Local Qwen Error: {str(exc)}")
            else:
                errors.append("Local Qwen CircuitBreaker is OPEN")

        # ==================== 3단계: 양대 공급자 전면 고립 처리 ====================
        total_latency = int((time.monotonic() - start_time) * 1000)
        logger.error(f"모든 LLM 공급자 연결 불가. 원인: { ' | '.join(errors) }")

        return LLMResponse(
            content=DISCONNECTED_STANDARD_MESSAGE,
            provider="none",
            model="none",
            latency_ms=total_latency,
            success=False,
            error_message="; ".join(errors)
        )

    def check_health(self) -> Dict[str, Any]:
        """양대 공급자 상태 진단 및 서킷 브레이커 상태 점검"""
        health_info = {
            "openai": {
                "configured": bool(self.openai_api_key),
                "model": self.openai_model,
                "circuit_state": self.breakers["openai"].state.value,
                "consecutive_failures": self.breakers["openai"].consecutive_failures
            },
            "local_qwen": {
                "base_url": self.local_llm_base_url,
                "model": self.local_llm_model,
                "circuit_state": self.breakers["local_qwen"].state.value,
                "consecutive_failures": self.breakers["local_qwen"].consecutive_failures
            }
        }
        return health_info


# 편의용 글로벌 싱글톤 인스턴스
llm_adapter = LLMAdapter()