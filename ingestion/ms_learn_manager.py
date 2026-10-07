import os
import sys
import re
from pathlib import Path
import requests
import psycopg2
from dotenv import load_dotenv

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


class MsLearnManager:
    """
    Microsoft Learn Catalog API 실시간 수집 매니저 (100% Live API 전용):
    - 공식 Catalog API 실시간 호출을 통한 Cloud, AI, DevOps, Data 핵심 모듈 수집
    - courses 테이블 적재 및 skill_tags 브릿지 자동 연계
    """

    CATALOG_API_URL = "https://learn.microsoft.com/api/catalog/"

    def __init__(self, db_url: str = None):
        self.db_url = db_url or os.getenv("SUPABASE_DB_URL")
        if not self.db_url:
            raise ValueError("[Error] SUPABASE_DB_URL 환경변수 설정이 누락되었습니다.")

    def _get_connection(self):
        return psycopg2.connect(self.db_url, connect_timeout=5)

    @staticmethod
    def map_difficulty(levels: list) -> str:
        if not levels:
            return "BEGINNER"
        lvl = str(levels[0]).strip().lower()
        if "advanced" in lvl:
            return "ADVANCED"
        if "intermediate" in lvl:
            return "INTERMEDIATE"
        return "BEGINNER"

    def fetch_live_catalog_modules(self, max_items: int = 50):
        params = {"locale": "ko-kr"}
        try:
            print(f"[API 호출] Microsoft Learn 공식 Catalog API 호출 시작: {self.CATALOG_API_URL}")
            res = requests.get(self.CATALOG_API_URL, params=params, timeout=12)
            res.raise_for_status()
            data = res.json()

            modules = data.get("modules", [])
            print(f"[API 성공] 총 {len(modules):,}개의 모듈 수신, IT 실무 모듈 선별 중...")

            target_keywords = ["azure", "ai", "cloud", "docker", "kubernetes", "python", "data", "security", "devops", "sql", "api"]
            filtered = []

            for m in modules:
                title = str(m.get("title", "")).strip()
                summary = str(m.get("summary", "")).strip()
                combined_text = f"{title} {summary}".lower()

                if any(kw in combined_text for kw in target_keywords):
                    uid = str(m.get("uid", "")).strip()
                    clean_id = f"ms_{uid}"[:50]
                    filtered.append({
                        "course_id": clean_id,
                        "title": title[:200],
                        "org_name": "Microsoft",
                        "url": m.get("url", "https://learn.microsoft.com/"),
                        "is_matchup": True,
                        "syllabus_summary": summary[:600] or "Microsoft Learn 공식 클라우드/AI 실무 모듈",
                        "platform": "Microsoft Learn",
                        "difficulty": self.map_difficulty(m.get("levels", []))
                    })
                    if len(filtered) >= max_items:
                        break

            return filtered
        except Exception as e:
            print(f"[API Error] Microsoft Learn Catalog API 호출 실패: {e}")
            return []

    def sync_to_db(self):
        conn = self._get_connection()
        try:
            modules = self.fetch_live_catalog_modules(max_items=50)
            if not modules:
                print("[Warning] API로부터 수신된 Microsoft Learn 모듈이 없습니다.")
                return

            with conn.cursor() as cur:
                print(f"[DB 적재] Microsoft Learn 강좌 {len(modules)}건 courses 테이블 UPSERT 실행 중...")
                for m in modules:
                    cur.execute("""
                        INSERT INTO courses (
                            course_id, title, org_name, url, is_matchup, 
                            syllabus_summary, platform, difficulty, last_batch_updated
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (course_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            org_name = EXCLUDED.org_name,
                            url = EXCLUDED.url,
                            is_matchup = EXCLUDED.is_matchup,
                            syllabus_summary = EXCLUDED.syllabus_summary,
                            platform = EXCLUDED.platform,
                            difficulty = EXCLUDED.difficulty,
                            last_batch_updated = CURRENT_TIMESTAMP;
                    """, (
                        m["course_id"], m["title"], m["org_name"], m["url"],
                        m["is_matchup"], m["syllabus_summary"], m["platform"], m["difficulty"]
                    ))

                cur.execute("SELECT skill_id, name FROM skill_tags;")
                skills = cur.fetchall()

                bridge_count = 0
                for m in modules:
                    text_blob = f"{m['title']} {m['syllabus_summary']}".lower()
                    for s_id, s_name in skills:
                        clean_skill = re.sub(r"\(.*?\)", "", s_name).strip().lower()
                        tokens = [k for k in clean_skill.split() if len(k) > 1 and k not in ["&", "/", "및"]]
                        if any(kw in text_blob for kw in tokens):
                            cur.execute("""
                                INSERT INTO course_skill_map (course_id, skill_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING;
                            """, (m["course_id"], s_id))
                            bridge_count += 1

                print(f"[브릿지 연결] 신규 MS Learn 강좌와 실무 스킬 간 매핑 {bridge_count}건 등록 완료")

            conn.commit()
            print(f"[완료] Microsoft Learn 강좌 {len(modules)}건 DB 적재 완료.")
        except Exception as e:
            conn.rollback()
            print(f"[Error] Microsoft Learn 적재 실패: {e}")
            raise e
        finally:
            conn.close()

    def run(self):
        return self.sync_to_db()


MicrosoftLearnManager = MsLearnManager
__all__ = ["MsLearnManager", "MicrosoftLearnManager"]

if __name__ == "__main__":
    manager = MsLearnManager()
    manager.sync_to_db()