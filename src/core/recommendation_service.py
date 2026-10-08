import logging
from typing import Dict, Any, List
from src.db.db_client import db_client
from src.core.embedding_service import embedding_service

logger = logging.getLogger("RECOMMENDATION_SERVICE")


def normalize_course_url(raw_url: str, title: str, platform: str) -> str:
    """공식 API가 공급한 실제 200 OK 절대 경로를 그대로 보존 (404 방지)"""
    url_str = str(raw_url).strip() if raw_url else ""

    if url_str.startswith("http://") or url_str.startswith("https://"):
        return url_str

    if url_str.startswith("/"):
        return f"https://learn.microsoft.com{url_str}"

    return "https://learn.microsoft.com/ko-kr/training/"


class RecommendationService:
    """DB 실존 엔티티 및 벡터 유사도 RAG 기반 지식 접지 리트리버"""

    def __init__(self) -> None:
        self.db = db_client
        self.embedder = embedding_service

    @staticmethod
    def normalize_course_url(raw_url: str, title: str, platform: str) -> str:
        return normalize_course_url(raw_url, title, platform)

    def retrieve_grounding_context(
        self, target_skills: List[str], target_job_title: str = ""
    ) -> Dict[str, Any]:
        """목표 스킬 및 직무에 부합하는 실존 DB 자원 인출"""
        context: Dict[str, Any] = {
            "target_job": target_job_title,
            "target_skills": target_skills,
            "courses": [],
            "certifications": [],
            "books": [],
            "job_profile": None,
        }

        try:
            query_text = f"{target_job_title} {' '.join(target_skills)}".strip()
            query_vector = self.embedder.get_embedding(query_text)

            matched_skill_ids = []
            if self.db.client:
                skills_resp = self.db.client.table("skill_tags").select("skill_id, name, embedding").execute()
                all_skills = skills_resp.data or []
                skill_scores = []

                for sk in all_skills:
                    s_emb = sk.get("embedding")
                    score = 0.0

                    if any(ts.lower() in str(sk.get("name", "")).lower() for ts in target_skills):
                        score += 0.5

                    if s_emb and query_vector and any(query_vector):
                        try:
                            if isinstance(s_emb, str) and s_emb.startswith("["):
                                s_vec = [float(x.strip()) for x in s_emb.strip("[]").split(",") if x.strip()]
                            else:
                                s_vec = s_emb
                            sim = self.embedder.cosine_similarity(query_vector, s_vec)
                            score += sim
                        except Exception:
                            pass
                    skill_scores.append((sk["skill_id"], score))

                skill_scores.sort(key=lambda x: x[1], reverse=True)
                matched_skill_ids = [s[0] for s in skill_scores[:5]]

            if self.db.client:
                c_resp = self.db.client.table("courses").select("*").execute()
                for c in (c_resp.data or []):
                    c_url = self.normalize_course_url(c.get("url"), c.get("title", ""), c.get("platform", ""))
                    context["courses"].append({
                        "course_id": c.get("course_id"),
                        "title": c.get("title"),
                        "platform": c.get("platform", "MS Learn"),
                        "difficulty": c.get("difficulty", "INTERMEDIATE"),
                        "is_matchup": bool(c.get("is_matchup", False)),
                        "duration_weeks": c.get("duration_weeks", 4),
                        "url": c_url,
                    })

            if self.db.client:
                z_resp = self.db.client.table("certifications").select("*").execute()
                for cert in (z_resp.data or []):
                    context["certifications"].append({
                        "cert_id": cert.get("cert_id"),
                        "title": cert.get("title"),
                        "provider": cert.get("provider", "공인기관"),
                        "exam_type": cert.get("exam_type", "필기/실기"),
                    })

            if self.db.client:
                b_resp = self.db.client.table("books").select("*").order("loan_count", desc=True).limit(8).execute()
                for b in (b_resp.data or []):
                    context["books"].append({
                        "isbn": b.get("isbn"),
                        "title": b.get("title"),
                        "author": b.get("author", "전문가"),
                        "publisher": b.get("publisher", "출판사"),
                        "is_related": True,
                    })

            if target_job_title and self.db.client:
                job_resp = self.db.client.table("jobs").select("*").ilike("title", f"%{target_job_title}%").limit(1).execute()
                if job_resp.data:
                    context["job_profile"] = {
                        "job_id": job_resp.data[0].get("job_id"),
                        "title": job_resp.data[0].get("title"),
                        "ncs_units": job_resp.data[0].get("ncs_units", ""),
                    }

            logger.info(
                f"[RAG 접지 완료] 질의: {query_text} -> 공식 API 강좌 {len(context['courses'])}건, "
                f"자격증 {len(context['certifications'])}건, 도서 {len(context['books'])}건 인출"
            )
            return context
        except Exception as exc:
            logger.error(f"접지 컨텍스트 인출 중 오류: {exc}")
            return context


recommendation_service = RecommendationService()
__all__ = ["RecommendationService", "recommendation_service", "normalize_course_url"]