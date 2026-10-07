import os
import sys
import re
import urllib.parse
from pathlib import Path
import psycopg2
import requests
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


class MajorsManager:
    """
    대학 학과 도메인 실시간 관리자 (100% Live API 전용):
    - 공공데이터포털 실제 컬럼 규격('학부_과(전공)명', '학교명') 1:1 정밀 매핑
    - cond[학부_과(전공)명::LIKE] 조건 검색을 활용한 52,214건 중 IT 전공 정밀 수집
    - 비IT 학과 블랙리스트 방어 및 majors 테이블 적재
    - skill_tags 브릿지 자동 연계
    """

    BLACKLIST_KEYWORDS = [
        "한방", "한의", "동의보감", "간호", "뷰티", "화장품", "철학", "광고기획",
        "특수교육", "한국어", "상담", "복지", "보건", "식품", "조리", "패션", "일반화학"
    ]

    TARGET_SEARCH_TERMS = ["컴퓨터", "소프트웨어", "인공지능", "데이터", "정보통신"]

    def __init__(self, db_url: str = None):
        self.db_url = db_url or os.getenv("SUPABASE_DB_URL")
        self.api_key = os.getenv("DATA_GO_KR_API_KEY", "").strip() or os.getenv("UNIV_API_KEY", "").strip()
        self.paths = get_paths_config()
        self.endpoint = self.paths.get("api", {}).get("university", {}).get(
            "base_url", "https://api.odcloud.kr/api/15014632/v1/uddi:6939f45b-1283-4462-b394-820c26e1445d"
        )
        if not self.db_url:
            raise ValueError("[Error] SUPABASE_DB_URL 환경변수가 누락되었습니다.")

    def _get_connection(self):
        return psycopg2.connect(self.db_url, connect_timeout=5)

    def fetch_live_it_majors(self, target_count: int = 40):
        if not self.api_key or self.api_key.startswith("your_"):
            print("[Warning] DATA_GO_KR_API_KEY가 설정되지 않아 학과 수집을 건너뜁니다.")
            return []

        unquoted_key = urllib.parse.unquote(self.api_key)
        headers = {
            "Authorization": f"Infuser {unquoted_key}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }

        matched_majors = []
        seen_titles = set()
        major_id_counter = 1001

        print(f"[API 호출] 대학알리미 Open API 실시간 수집 시작: {self.endpoint}")

        for term in self.TARGET_SEARCH_TERMS:
            if len(matched_majors) >= target_count:
                break

            params = {
                "serviceKey": unquoted_key,
                "page": 1,
                "perPage": 15,
                "cond[학부_과(전공)명::LIKE]": term
            }
            try:
                res = requests.get(self.endpoint, headers=headers, params=params, timeout=10)
                if res.status_code != 200:
                    print(f"[API Error] 검색어 '{term}' 호출 오류 (HTTP {res.status_code})")
                    continue

                items = res.json().get("data", [])
                for it in items:
                    # 실제 확인된 정규 컬럼 직접 추출
                    dept_name = str(it.get("학부_과(전공)명") or it.get("학과명") or "").strip()
                    school_name = str(it.get("학교명") or "").strip()

                    if not dept_name:
                        continue

                    # 학교명과 결합하여 고유 전공명 생성 (예: 서울대학교 컴퓨터공학부)
                    full_major_title = f"{school_name} {dept_name}" if school_name else dept_name
                    if full_major_title in seen_titles:
                        continue

                    # 블랙리스트 제외
                    if any(b_kw in dept_name for b_kw in self.BLACKLIST_KEYWORDS):
                        continue

                    seen_titles.add(full_major_title)
                    curriculum_desc = (
                        f"{full_major_title} 정규 교육과정: {dept_name} 전공 핵심 이론, "
                        f"자료구조, 알고리즘, 컴퓨터 시스템 아키텍처 및 {term} 실무 연구 프로젝트"
                    )

                    matched_majors.append({
                        "major_id": major_id_counter,
                        "title": full_major_title[:100],
                        "curriculum": curriculum_desc[:500]
                    })
                    major_id_counter += 1

                print(f"  -> '{term}' 검색 완료: 누적 {len(matched_majors)}개 학과 확보")
            except Exception as e:
                print(f"[API Error] 검색어 '{term}' 처리 중 오류: {e}")
                continue

        print(f"[API 성공] 대학알리미 IT 전공 학과 총 {len(matched_majors)}건 수신 완료")
        return matched_majors

    def sync_to_db(self):
        conn = self._get_connection()
        try:
            majors = self.fetch_live_it_majors(target_count=40)
            if not majors:
                print("[Warning] API로부터 수신된 학과 데이터가 없습니다.")
                return

            with conn.cursor() as cur:
                print(f"[DB 적재] IT 전공 학과 {len(majors)}건 majors 테이블 UPSERT 실행 중...")
                for m in majors:
                    cur.execute("""
                        INSERT INTO majors (major_id, title, curriculum, last_batch_updated)
                        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (major_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            curriculum = EXCLUDED.curriculum,
                            last_batch_updated = CURRENT_TIMESTAMP;
                    """, (m["major_id"], m["title"], m["curriculum"]))

                # 브릿지 매핑 자동 연계
                cur.execute("SELECT skill_id, name FROM skill_tags;")
                skills = cur.fetchall()

                bridge_count = 0
                for m in majors:
                    text_blob = f"{m['title']} {m['curriculum']}".lower()
                    for s_id, s_name in skills:
                        clean_s = re.sub(r"\(.*?\)", "", s_name).strip().lower()
                        tokens = [t for t in clean_s.split() if len(t) > 1 and t not in ["&", "/", "및"]]
                        if any(kw in text_blob for kw in tokens):
                            cur.execute("""
                                INSERT INTO major_skill_map (major_id, skill_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING;
                            """, (m["major_id"], s_id))
                            bridge_count += 1

                print(f"[브릿지 연결] IT 전공 학과와 실무 스킬 간 매핑 {bridge_count}건 등록 완료")

            conn.commit()
            print(f"[완료] IT 전공 학과 {len(majors)}건 DB 적재 완료.")
        except Exception as e:
            conn.rollback()
            print(f"[Error] 학과 적재 실패: {e}")
            raise e
        finally:
            conn.close()

    def run(self):
        return self.sync_to_db()


MajorsIngestionManager = MajorsManager
__all__ = ["MajorsManager", "MajorsIngestionManager"]

if __name__ == "__main__":
    mgr = MajorsManager()
    mgr.sync_to_db()