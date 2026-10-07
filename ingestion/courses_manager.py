import os
import sys
import re
from pathlib import Path
import urllib.parse
import requests
import psycopg2
from dotenv import load_dotenv

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

try:
    from core.config_loader import get_paths_config
except ImportError:
    import yaml
    def get_paths_config():
        with open(PROJECT_ROOT / "config" / "paths.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)


class CoursesManager:
    """
    K-MOOC v2.0 실시간 Open API 수집 매니저 (100% Live API 전용):
    - 하드코딩 모의 데이터 0% 배제
    - 최소 40건 이상 IT 강좌 확보 시까지 자동 페이지 순회(Pagination)
    - 강좌명/요약문 기반 3단계 난이도 자동 라벨링 및 skill_tags 브릿지 자동 연계
    """

    IT_KEYWORDS = [
        "데이터", "프로그래밍", "컴퓨터", "인공지능", "ai", "sw", "소프트웨어",
        "알고리즘", "파이썬", "python", "머신러닝", "딥러닝", "클라우드",
        "네트워크", "보안", "웹", "시스템", "리눅스", "sql", "코딩", "통신", "컨테이너", "빅데이터"
    ]

    def __init__(self, db_url: str = None):
        self.db_url = db_url or os.getenv("SUPABASE_DB_URL")
        self.service_key = os.getenv("KMOOC_SERVICE_KEY", "").strip() or os.getenv("DATA_GO_KR_API_KEY", "").strip()
        self.paths_cfg = get_paths_config()
        self.kmooc_cfg = self.paths_cfg.get("api", {}).get("kmooc", {})
        self.base_url = self.kmooc_cfg.get("base_url", "https://apis.data.go.kr/B552881/kmooc_v2_0/courseList_v2_0")

        if not self.db_url:
            raise ValueError("[Error] SUPABASE_DB_URL 환경변수 설정이 누락되었습니다.")

    def _get_connection(self):
        return psycopg2.connect(self.db_url, connect_timeout=5)

    @staticmethod
    def classify_difficulty(title: str, summary: str) -> str:
        text = f"{title or ''} {summary or ''}".lower()
        if any(k in text for k in ["고급", "심화", "최적화", "성능", "대규모", "커널", "튜닝", "expert", "advanced"]):
            return "ADVANCED"
        if any(k in text for k in ["활용", "응용", "실무", "실습", "분석", "프로젝트", "파이프라인", "intermediate"]):
            return "INTERMEDIATE"
        return "BEGINNER"

    def fetch_live_kmooc_courses(self, target_count: int = 40):
        if not self.service_key or self.service_key.startswith("your_"):
            print("[Warning] KMOOC_SERVICE_KEY가 설정되지 않아 K-MOOC 수집을 건너뜁니다.")
            return []

        unquoted_key = urllib.parse.unquote(self.service_key)
        cleaned_courses = []
        seen_ids = set()
        page = 1

        print(f"[API 호출] K-MOOC API v2.0 실시간 수집 시작: {self.base_url}")

        while len(cleaned_courses) < target_count and page <= 8:
            params = {
                "serviceKey": unquoted_key,
                "Page": page,
                "size": 50
            }
            try:
                res = requests.get(self.base_url, params=params, timeout=10)
                if res.status_code != 200:
                    print(f"[API Error] K-MOOC {page}페이지 응답 오류: {res.status_code}")
                    break

                data = res.json()
                raw_items = data.get("results") or data.get("items") or data.get("data") or []
                if isinstance(raw_items, dict) and "item" in raw_items:
                    raw_items = raw_items["item"]

                if not raw_items:
                    break

                for it in raw_items:
                    cid = str(it.get("id") or it.get("course_id") or "").strip()
                    title = str(it.get("name") or it.get("title") or "").strip()
                    org = str(it.get("org_name") or it.get("univ_name") or "K-MOOC 주관대학").strip()
                    summary = str(it.get("short_description") or it.get("description") or it.get("summary") or "").strip()

                    if not cid or not title or cid in seen_ids:
                        continue

                    combined_text = f"{title} {summary}".lower()
                    if not any(kw in combined_text for kw in self.IT_KEYWORDS):
                        continue

                    seen_ids.add(cid)
                    safe_url = f"https://www.kmooc.kr/view/course/detail/{cid}"
                    is_matchup = bool("매치업" in title or "matchup" in cid.lower() or it.get("is_matchup"))

                    cleaned_courses.append({
                        "course_id": cid[:50],
                        "title": title[:200],
                        "org_name": org[:100],
                        "url": safe_url[:255],
                        "is_matchup": is_matchup,
                        "syllabus_summary": summary[:600] or "K-MOOC 실무 온라인 공개강좌",
                        "platform": "K-MOOC",
                        "difficulty": self.classify_difficulty(title, summary)
                    })

                print(f"  -> K-MOOC {page}페이지 완료: 누적 {len(cleaned_courses)}건 확보")
                page += 1
            except Exception as e:
                print(f"[API Error] K-MOOC {page}페이지 호출 실패: {e}")
                break

        print(f"[API 정제] IT 표준 실무 강좌 총 {len(cleaned_courses)}건 수집 완료")
        return cleaned_courses

    def sync_to_db(self):
        conn = self._get_connection()
        try:
            courses = self.fetch_live_kmooc_courses(target_count=40)
            if not courses:
                print("[Warning] API로부터 수신된 K-MOOC 강좌가 없습니다.")
                return

            with conn.cursor() as cur:
                print(f"[DB 적재] K-MOOC 실시간 강좌 {len(courses)}건 courses 테이블 UPSERT 실행 중...")
                for c in courses:
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
                        c["course_id"], c["title"], c["org_name"], c["url"],
                        c["is_matchup"], c["syllabus_summary"], c["platform"], c["difficulty"]
                    ))

                # 실시간 스킬 브릿지 자동 연계
                cur.execute("SELECT skill_id, name FROM skill_tags;")
                skills = cur.fetchall()

                bridge_count = 0
                for c in courses:
                    text_blob = f"{c['title']} {c['syllabus_summary']}".lower()
                    for s_id, s_name in skills:
                        clean_s = re.sub(r"\(.*?\)", "", s_name).strip().lower()
                        tokens = [k for k in clean_s.split() if len(k) > 1 and k not in ["&", "/", "및"]]
                        if any(kw in text_blob for kw in tokens):
                            cur.execute("""
                                INSERT INTO course_skill_map (course_id, skill_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING;
                            """, (c["course_id"], s_id))
                            bridge_count += 1

                print(f"[브릿지 연결] K-MOOC 강좌와 실무 스킬 간 매핑 {bridge_count}건 등록 완료")

            conn.commit()
            print(f"[완료] K-MOOC 실시간 강좌 {len(courses)}건 DB 적재 완료.")
        except Exception as e:
            conn.rollback()
            print(f"[Error] K-MOOC 적재 실패: {e}")
            raise e
        finally:
            conn.close()

    def run(self):
        return self.sync_to_db()


CoursesIngestionManager = CoursesManager
__all__ = ["CoursesManager", "CoursesIngestionManager"]

if __name__ == "__main__":
    manager = CoursesManager()
    manager.sync_to_db()