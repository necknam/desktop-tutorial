"""
통합 LLM 어댑터 모듈 (gpt-5-nano 저토큰 최적화 및 실시간 토큰 사용량 추적 지원)
파일 경로: src/clients/llm_adapter.py
"""

import os
import time
import logging
import math
from threading import Lock
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import httpx
from openai import OpenAI, APITimeoutError, RateLimitError, InternalServerError, BadRequestError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("LLM_ADAPTER")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(name)s - %(message)s")

DISCONNECTED_STANDARD_MESSAGE = "LLM 모델 없음"


class CircuitState(Enum):
    """회로 차단기 상태 머신"""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


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
    """공급자별 멀티스레드 안전 독립 회로 차단기"""

    def __init__(self, provider_name: str, failure_threshold: int = 5, reset_timeout: float = 30.0) -> None:
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self._lock = Lock()

    def can_execute(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self.state == CircuitState.OPEN:
                if now - self.last_failure_time >= self.reset_timeout:
                    logger.info(f"[{self.provider_name}] 회로 차단기: OPEN -> HALF_OPEN (복구 탐색 시작)")
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.state = CircuitState.CLOSED
            self.consecutive_failures = 0

    def record_failure(self, error: Exception) -> None:
        with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.monotonic()
            logger.warning(
                "[%s] 호출 실패 (%d/%d): %s",
                self.provider_name,
                self.consecutive_failures,
                self.failure_threshold,
                type(error).__name__,
            )
            if self.consecutive_failures >= self.failure_threshold or self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN


class LLMAdapter:
    """OpenAI(gpt-5-nano 기본) 우선 및 Local Qwen Fallback을 지원하는 통합 LLM 어댑터"""

    def __init__(self) -> None:
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-5-nano").strip()
        # 추론 토큰 최소화를 위한 기본 effort 설정 (minimal -> 미지원 모델일 경우 자동으로 low로 폴백)
        self.reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "minimal").strip().lower()

        self.local_llm_base_url = os.getenv("LOCAL_LLM_BASE_URL", "").strip().rstrip("/")
        self.local_llm_model = os.getenv("LOCAL_LLM_MODEL", "").strip()
        self.local_api_key = os.getenv("LOCAL_LLM_API_KEY", "ollama").strip()

        try:
            self.timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
        except ValueError:
            raise ValueError("LLM_TIMEOUT_SECONDS는 1~600초 사이의 숫자여야 합니다.") from None
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 600:
            raise ValueError("LLM_TIMEOUT_SECONDS는 1~600초 사이의 숫자여야 합니다.")
        self.timeout = httpx.Timeout(timeout=self.timeout_seconds, connect=5.0)

        self.breakers = {
            "openai": CircuitBreaker("OpenAI", failure_threshold=5, reset_timeout=30.0),
            "local_qwen": CircuitBreaker("LocalQwen", failure_threshold=3, reset_timeout=30.0),
        }

        # 멀티스레드 안전 실시간 토큰 사용량 카운터
        self._usage_lock = Lock()
        self.token_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

        self._openai_client: Optional[OpenAI] = None
        if self.openai_api_key:
            self._openai_client = OpenAI(
                api_key=self.openai_api_key,
                timeout=self.timeout,
                max_retries=0,
            )

    def _is_reasoning_model(self, model_name: str) -> bool:
        """gpt-5*, gpt-6*, o1*, o3*, o4* 등 추론 모델 여부 판별 (temperature 제외 및 reasoning_effort 주입 대상)"""
        m = model_name.lower()
        return m.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))

    def _record_usage(self, usage: Any) -> None:
        """응답 객체에서 토큰 사용량을 추출하여 누적 집계"""
        if usage is None:
            return
        p_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        c_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        t_tok = int(getattr(usage, "total_tokens", 0) or (p_tok + c_tok))
        details = getattr(usage, "completion_tokens_details", None)
        r_tok = int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0

        with self._usage_lock:
            self.token_usage["prompt_tokens"] += p_tok
            self.token_usage["completion_tokens"] += c_tok
            self.token_usage["reasoning_tokens"] += r_tok
            self.token_usage["total_tokens"] += t_tok

    def get_usage_summary(self) -> Dict[str, int]:
        """현재까지 누적된 토큰 사용량 사본 반환"""
        with self._usage_lock:
            return dict(self.token_usage)

    def _call_openai(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """1순위: OpenAI API 호출 (gpt-5-nano minimal 추론 제어 및 429/5xx 지수 백오프)"""
        if not self.openai_api_key or not self._openai_client:
            raise ValueError("OPENAI_API_KEY가 환경변수에 설정되지 않았습니다.")

        params: Dict[str, Any] = {
            "model": self.openai_model,
            "messages": messages,
        }
        if self._is_reasoning_model(self.openai_model):
            params["reasoning_effort"] = self.reasoning_effort
        else:
            params["temperature"] = temperature

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._openai_client.chat.completions.create(**params)
                self._record_usage(getattr(response, "usage", None))
                if not response.choices or not response.choices[0].message.content:
                    raise RuntimeError("OpenAI로부터 빈 응답이 반환되었습니다.")
                return response.choices[0].message.content.strip()
            except BadRequestError as exc:
                # 일부 모델 스냅샷이 reasoning_effort="minimal"을 지원하지 않고 "low"만 허용할 경우 자동 폴백
                if params.get("reasoning_effort") == "minimal":
                    logger.info(f"[OpenAI] {self.openai_model}에서 minimal 미지원 감지 -> reasoning_effort='low'로 자동 전환")
                    self.reasoning_effort = "low"
                    params["reasoning_effort"] = "low"
                    response = self._openai_client.chat.completions.create(**params)
                    self._record_usage(getattr(response, "usage", None))
                    if not response.choices or not response.choices[0].message.content:
                        raise RuntimeError("OpenAI로부터 빈 응답이 반환되었습니다.")
                    return response.choices[0].message.content.strip()
                raise exc
            except (RateLimitError, InternalServerError) as exc:
                if attempt == max_attempts:
                    raise
                backoff = min(15.0, (2 ** attempt) + 0.5)
                logger.warning(
                    f"[OpenAI] 일시적 제한/서버 오류({type(exc).__name__}), {backoff:.1f}초 후 재시도 ({attempt}/{max_attempts})"
                )
                time.sleep(backoff)

        raise RuntimeError("OpenAI 호출 재시도 횟수 초과")

    def _call_local_qwen(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """2순위: Local Qwen 호출"""
        url = f"{self.local_llm_base_url}/chat/completions"
        payload = {
            "model": self.local_llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.local_api_key}"}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    def _safe_failure(self, provider: str, exc: Exception) -> Dict[str, Any]:
        status = getattr(exc, "status_code", None)
        if status is None and isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
            code, message = "TIMEOUT", f"응답 시간 초과 (요청 제한 {self.timeout_seconds:g}초). 잠시 후 다시 시도해 주세요."
        elif status in (401, 403):
            code, message = "AUTH_OR_PERMISSION", "API 인증 또는 모델 접근 권한을 확인해 주세요."
        elif status == 429:
            code, message = "RATE_OR_QUOTA_LIMIT", "API 호출 한도 또는 사용 가능한 크레딧을 확인해 주세요."
        elif status == 400:
            code, message = "INVALID_REQUEST", "모델 호출 파라미터를 확인해 주세요."
        elif status == 404:
            code, message = "MODEL_OR_ENDPOINT_NOT_FOUND", "모델 ID 또는 서비스 주소를 확인해 주세요."
        elif isinstance(status, int) and status >= 500:
            code, message = "PROVIDER_ERROR", "모델 제공 서비스 오류입니다. 잠시 후 다시 시도해 주세요."
        elif isinstance(exc, ValueError) and not self.openai_api_key and provider == "OpenAI":
            code, message = "NOT_CONFIGURED", "OPENAI_API_KEY가 설정되지 않았습니다."
        elif isinstance(exc, httpx.NetworkError) or type(exc).__name__ == "APIConnectionError":
            code, message = "CONNECTION_ERROR", "모델 서비스 연결에 실패했습니다. 네트워크를 확인해 주세요."
        else:
            code, message = "GENERATION_ERROR", "모델 응답 생성에 실패했습니다. 연결 및 응답 형식을 확인해 주세요."
        return {"provider": provider, "code": code, "message": message}

    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.5,
        force_provider: Optional[str] = None,
    ) -> LLMResponse:
        start_time = time.monotonic()
        errors: List[str] = []
        failures: List[Dict[str, Any]] = []

        if force_provider in (None, "openai"):
            breaker = self.breakers["openai"]
            if breaker.can_execute():
                try:
                    t0 = time.monotonic()
                    content = self._call_openai(messages, temperature)
                    latency = int((time.monotonic() - t0) * 1000)
                    breaker.record_success()
                    return LLMResponse(
                        content=content,
                        provider="openai",
                        model=self.openai_model,
                        latency_ms=latency,
                        success=True,
                    )
                except Exception as exc:
                    breaker.record_failure(exc)
                    failure = self._safe_failure("OpenAI", exc)
                    failures.append(failure)
                    errors.append(f"OpenAI: {failure['message']}")
            else:
                errors.append("OpenAI CircuitBreaker is OPEN")

        if force_provider in (None, "local_qwen") and self.local_llm_base_url and self.local_llm_model:
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
                        metadata={"fallback": True},
                    )
                except Exception as exc:
                    breaker.record_failure(exc)
                    failure = self._safe_failure("Qwen", exc)
                    failures.append(failure)
                    errors.append(f"Qwen: {failure['message']}")
            else:
                errors.append("Local Qwen CircuitBreaker is OPEN")

        total_latency = int((time.monotonic() - start_time) * 1000)
        logger.error(f"모든 LLM 공급자 연결 불가. 원인: {' | '.join(errors)}")

        return LLMResponse(
            content=DISCONNECTED_STANDARD_MESSAGE,
            provider="none",
            model="none",
            latency_ms=total_latency,
            success=False,
            error_message="; ".join(errors),
            metadata={"failures": failures, "timeout_seconds": self.timeout_seconds},
        )

    def check_health(self) -> Dict[str, Any]:
        return {
            "openai": {
                "configured": bool(self.openai_api_key),
                "model": self.openai_model,
                "timeout_seconds": self.timeout_seconds,
                "circuit_state": self.breakers["openai"].state.value,
                "consecutive_failures": self.breakers["openai"].consecutive_failures,
            },
            "local_qwen": {
                "configured": bool(self.local_llm_base_url and self.local_llm_model),
                "model": self.local_llm_model,
                "circuit_state": self.breakers["local_qwen"].state.value,
                "consecutive_failures": self.breakers["local_qwen"].consecutive_failures,
            },
        }


llm_adapter = LLMAdapter()
__all__ = ["LLMAdapter", "LLMResponse", "CircuitState", "DISCONNECTED_STANDARD_MESSAGE", "llm_adapter"]