import os
import logging
from typing import List, Union
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("EMBEDDING_SERVICE")


class EmbeddingService:
    """1536차원 시맨틱 벡터 생성 및 코사인 유사도 연산 모듈"""

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model_name = os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-small")
        self.client = None
        if self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key)
            except Exception as exc:
                logger.warning(f"OpenAI 클라이언트 초기화 실패: {exc}")

    def get_embedding(self, text: str) -> List[float]:
        """단일 텍스트를 1536차원 정규화 벡터로 변환"""
        if not text or not self.client:
            return [0.0] * 1536

        clean_text = text.replace("\n", " ").strip()
        try:
            resp = self.client.embeddings.create(
                input=[clean_text],
                model=self.model_name
            )
            return resp.data[0].embedding
        except Exception as exc:
            logger.error(f"임베딩 벡터 생성 실패: {exc}")
            return [0.0] * 1536

    @staticmethod
    def cosine_similarity(
        vec_a: Union[List[float], np.ndarray], 
        vec_b: Union[List[float], np.ndarray]
    ) -> float:
        """두 벡터 간의 코사인 유사도 (-1.0 ~ 1.0) 연산"""
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


embedding_service = EmbeddingService()
__all__ = ["EmbeddingService", "embedding_service"]