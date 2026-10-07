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

try:
    from core.config_loader import get_paths_config
except ImportError:
    import yaml
    def get_paths_config():
        with open(PROJECT_ROOT / "config" / "paths.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)


class JobsManager:
    """
    한국직업능력연구원 커리어넷 직업백과 실시간 Open API 수집 매니저 (100% Live API 전용):
    - 하드코딩 모의 데이터 0% 배제
    - 다중 페이지 순회를 통해 IT/소프트웨어 전문 직무 40건 이상 수집
    - jobs 테이블 적재 및 skill_tags 브릿지 자동 연계
    """

    IT_JOB_KEYWORDS = [
        "데이터", "소프트웨어", "인공지능", "개발", "네트워크", "보안", "클라우드",
        "시스템", "프로그래머", "엔지니어", "정보", "웹", "컴퓨터", "db", "분석",
        "설계", "통신", "it", "디지털", "아키텍트", "기획", "사이버", "앱", "빅데이터"
    ]

    def __init__(self, db_url: str = None):
        self.db_url = db_url or os.getenv("SUPABASE_DB_URL")
        self.api_key = os.getenv("CAREERNET_API_KEY", "").strip()
        self.paths_cfg = get_paths_config()
        self.job_cfg = self.paths_cfg.get("api", {}).get("careernet", {})
        self.base_url = self.job_cfg.get("base_url", "https://www.career.go.kr/cnet/openapi/getOpenApi.json")

        if not self.db_url:
            raise ValueError("[Error] SUPABASE_DB_URL 환경변수 설정이 누락되었습니다.")

    def _get_connection(self):
        return psycopg2.connect(self.db_url, connect_timeout=5)

    def fetch_live_it_jobs(self, target_count: int = 40):
        if not self.api_key or self.api_key.startswith("your_"):
            print("[Warning] CAREERNET_API_KEY가 설정되지 않아 직무 수집을 건너뜁니다.")
            return []

        it_jobs = []
        seen_titles = set()
        page = 1
        job_id_counter = 1

        print(f"[API 호출] 커리어넷 직업백과 실시간 수집 시작: {self.base_url}")

        while len(it_jobs) < target_count and page <= 5:
            params = {
                "apiKey": self.api_key,
                "svcType": "api",
                "svcCode": "JOB",
                "contentType": "json",
                "gubun": "job_dic_list",
                "pageIndex": page,
                "perPage": 100
            }
            try:
                res = requests.get(self.base_url, params=params, timeout=10)
                if res.status_code != 200:
                    break

                data = res.json()
                job_list = data.get("dataSearch", {}).get("content", [])
                if not job_list:
                    break

                for j in job_list:
                    j_name = str(j.get("job_nm") or j.get("job") or "").strip()
                    j_summary = str(j.get("summary") or j.get("aptit_name") or j.get("capacity") or "IT 전문 직무 역량").strip()

                    if not j_name or j_name in seen_titles:
                        continue

                    if any(kw in j_name for kw in self.IT_JOB_KEYWORDS):
                        seen_titles.add(j_name)
                        it_jobs.append({
                            "job_id": job_id_counter,
                            "title": j_name[:100],
                            "ncs_units": f"직무 개요: {j_summary[:300]}"
                        })
                        job_id_counter += 1

                print(f"  -> 커리어넷 {page}페이지 완료: 누적 {len(it_jobs)}건 확보")
                page += 1
            except Exception as e:
                print(f"[API Error] 커리어넷 호출 실패: {e}")
                break

        print(f"[API 정제] IT 전문 직무 총 {len(it_jobs)}건 추출 완료")
        return it_jobs

    def sync_to_db(self):
        conn = self._get_connection()
        try:
            jobs = self.fetch_live_it_jobs(target_count=40)
            if not jobs:
                print("[Warning] API로부터 수신된 직무 데이터가 없습니다.")
                return

            with conn.cursor() as cur:
                print(f"[DB 적재] IT 전문 직무 {len(jobs)}건 jobs 테이블 UPSERT 실행 중...")
                for j in jobs:
                    cur.execute("""
                        INSERT INTO jobs (job_id, title, ncs_units, last_batch_updated)
                        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (job_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            ncs_units = EXCLUDED.ncs_units,
                            last_batch_updated = CURRENT_TIMESTAMP;
                    """, (j["job_id"], j["title"], j["ncs_units"]))

                cur.execute("SELECT skill_id, name FROM skill_tags;")
                skills = cur.fetchall()

                bridge_count = 0
                for j in jobs:
                    text_blob = f"{j['title']} {j['ncs_units']}".lower()
                    for s_id, s_name in skills:
                        clean_s = re.sub(r"\(.*?\)", "", s_name).strip().lower()
                        tokens = [t for t in clean_s.split() if len(t) > 1 and t not in ["&", "/", "및"]]
                        if any(kw in text_blob for kw in tokens):
                            cur.execute("""
                                INSERT INTO job_skill_map (job_id, skill_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING;
                            """, (j["job_id"], s_id))
                            bridge_count += 1

                print(f"[브릿지 연결] IT 직무와 실무 스킬 간 매핑 {bridge_count}건 등록 완료")

            conn.commit()
            print(f"[완료] IT 전문 직무 {len(jobs)}건 DB 적재 완료.")
        except Exception as e:
            conn.rollback()
            print(f"[Error] 직무 데이터 적재 실패: {e}")
            raise e
        finally:
            conn.close()

    def run(self):
        return self.sync_to_db()


JobsIngestionManager = JobsManager
__all__ = ["JobsManager", "JobsIngestionManager"]

if __name__ == "__main__":
    manager = JobsManager()
    manager.sync_to_db()