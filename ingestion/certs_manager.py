import os
import re
import sys
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


class CertsManager:
    """
    공인 기술 자격증 실시간 API 수집 매니저 (100% Live API 전용):
    - 정규 엔드포인트(uddi:070b18af-bb50-46c5-8324-948d91fb5d58) 기반 실시간 수집
    - [PG], [CBT] 등 행정 불용어 정규식 자동 세척
    - 종목별 공인 합격 기준(SQLP 75점, SQLD 60점 등) 및 정규 출제과목 자동 산출 적재
    - certifications 테이블 적재 및 skill_tags 브릿지 자동 연계
    """

    def __init__(self, db_url: str = None):
        self.db_url = db_url or os.getenv("SUPABASE_DB_URL")
        self.api_key = os.getenv("DATA_GO_KR_API_KEY", "").strip() or os.getenv("KDATA_API_KEY", "").strip()
        self.paths = get_paths_config()
        self.endpoint = self.paths.get("api", {}).get("kdata", {}).get(
            "base_url", "https://api.odcloud.kr/api/15148115/v1/uddi:070b18af-bb50-46c5-8324-948d91fb5d58"
        )
        if not self.db_url:
            raise ValueError("[Error] SUPABASE_DB_URL 환경변수가 누락되었습니다.")

    def _get_connection(self):
        return psycopg2.connect(self.db_url, connect_timeout=5)

    @staticmethod
    def clean_cert_title(raw_title: str) -> str:
        if not raw_title:
            return ""
        cleaned = re.sub(r'\[.*?\]', '', raw_title)
        cleaned = re.sub(r'\(실기\)', '', cleaned)
        cleaned = re.sub(r'\(필기\)', '', cleaned)
        return cleaned.strip()

    @staticmethod
    def get_cert_criteria(title: str):
        """자격증 종목명 기반 공인 합격 점수 및 정규 출제과목 도출"""
        t = title.upper()
        # 1. SQL 전문가 (SQLP)
        if "SQL 전문가" in title or "SQLP" in t:
            return 75, "1과목: 데이터 모델링의 이해 / 2과목: SQL 기본 및 활용 / 3과목: SQL 고급 활용 및 튜닝"
        # 2. SQL 개발자 (SQLD)
        elif "SQL 개발자" in title or "SQLD" in t:
            return 60, "1과목: 데이터 모델링의 이해 / 2과목: SQL 기본 및 활용 (조인, 함수, 서브쿼리 등)"
        # 3. 데이터아키텍처 전문가 (DAP)
        elif "데이터아키텍처 전문가" in title or "DAP" in t:
            return 75, "1과목: 전사아키텍처 이해 / 2과목: 데이터 요건 분석 / 3과목: 데이터 표준화 / 4과목: 데이터 모델링"
        # 4. 데이터아키텍처 준전문가 (DASP)
        elif "데이터아키텍처 준전문가" in title or "DASP" in t:
            return 60, "1과목: 전사아키텍처 이해 / 2과목: 데이터 요건 분석 / 3과목: 데이터 모델링"
        # 5. 데이터분석 준전문가 (ADsP)
        elif "데이터분석 준전문가" in title or "ADSP" in t:
            return 60, "1과목: 데이터 이해 / 2과목: 데이터 분석 기획 / 3과목: 데이터 분석 (R/통계 분석)"
        # 6. 빅데이터분석기사
        elif "빅데이터분석기사" in title:
            return 60, "1과목: 빅데이터 분석 기획 / 2과목: 빅데이터 탐색 / 3과목: 빅데이터 모델링 / 4과목: 모델 평가"
        # 7. 국가기술 및 기타 IT 자격증
        elif any(k in title for k in ["정보처리", "보안", "네트워크", "리눅스"]):
            return 60, "소프트웨어 공학, 데이터베이스 구축, 시스템 네트워크 아키텍처 및 보안 실무"
        return 60, "공인 기술 자격 검정 출제 기준에 따름"

    def fetch_live_certs(self, target_count: int = 40):
        if not self.api_key or self.api_key.startswith("your_"):
            print("[Warning] DATA_GO_KR_API_KEY가 설정되지 않아 자격증 수집을 건너뜁니다.")
            return []

        unquoted_key = urllib.parse.unquote(self.api_key)
        headers = {
            "Authorization": f"Infuser {unquoted_key}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }

        live_certs = []
        seen_titles = set()
        page = 1

        print(f"[API 호출] 공인 자격증 실시간 수집 시작: {self.endpoint}")

        while len(live_certs) < target_count and page <= 5:
            params = {
                "serviceKey": unquoted_key,
                "page": page,
                "perPage": 50
            }
            try:
                res = requests.get(self.endpoint, headers=headers, params=params, timeout=10)
                if res.status_code != 200:
                    print(f"[API Error] 자격증 API {page}페이지 응답 오류: {res.status_code}")
                    break

                data = res.json()
                items = data.get("data", [])
                if not items:
                    break

                for it in items:
                    raw_t = (it.get("종목명") or it.get("자격명") or it.get("자격종목") or "").strip()
                    clean_t = self.clean_cert_title(raw_t)
                    prov = (it.get("시행기관") or it.get("발급기관") or "한국데이터산업진흥원 (K-DATA)").strip()
                    api_subj = (it.get("과목명") or it.get("시험과목") or it.get("직무내용") or "").strip()

                    if clean_t and len(clean_t) >= 2 and clean_t not in seen_titles:
                        seen_titles.add(clean_t)
                        # 합격 점수 및 과목 매핑
                        pass_score, default_subj = self.get_cert_criteria(clean_t)
                        final_subj = api_subj if api_subj else default_subj

                        live_certs.append({
                            "title": clean_t[:100],
                            "provider": prov[:50],
                            "test_subjects": final_subj[:600],
                            "pass_score": pass_score
                        })

                print(f"  -> 자격증 {page}페이지 완료: 누적 {len(live_certs)}건 확보")
                page += 1
            except Exception as e:
                print(f"[API Error] 자격증 API 호출 중 실패: {e}")
                break

        print(f"[API 성공] 공인 자격증 실시간 데이터 총 {len(live_certs)}건 수신 완료")
        return live_certs

    def sync_to_db(self):
        conn = self._get_connection()
        try:
            certs = self.fetch_live_certs(target_count=40)
            if not certs:
                print("[Warning] API로부터 수신된 자격증 데이터가 없습니다.")
                return

            with conn.cursor() as cur:
                print(f"[DB 적재] 공인 자격증 {len(certs)}종 certifications 테이블 UPSERT 실행 중 (합격선 반영)...")
                for c in certs:
                    cur.execute("""
                        INSERT INTO certifications (title, provider, test_subjects, pass_score, last_batch_updated)
                        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (title) DO UPDATE SET
                            provider = EXCLUDED.provider,
                            test_subjects = EXCLUDED.test_subjects,
                            pass_score = EXCLUDED.pass_score,
                            last_batch_updated = CURRENT_TIMESTAMP;
                    """, (c["title"], c["provider"], c["test_subjects"], c["pass_score"]))

                # 브릿지 매핑 자동 연계
                cur.execute("SELECT cert_id, title, test_subjects FROM certifications;")
                db_certs = cur.fetchall()
                cur.execute("SELECT skill_id, name FROM skill_tags;")
                skills = cur.fetchall()

                bridge_count = 0
                for c_id, c_title, c_subjs in db_certs:
                    text_blob = f"{c_title} {c_subjs}".lower()
                    for s_id, s_name in skills:
                        clean_s = re.sub(r"\(.*?\)", "", s_name).strip().lower()
                        tokens = [k for k in clean_s.split() if len(k) > 1 and k not in ["&", "/", "및"]]
                        if any(k in text_blob for k in tokens):
                            cur.execute("""
                                INSERT INTO cert_skill_map (cert_id, skill_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING;
                            """, (c_id, s_id))
                            bridge_count += 1

                print(f"[브릿지 연결] 공인 자격증과 실무 스킬 간 매핑 {bridge_count}건 등록 완료")

            conn.commit()
            print(f"[완료] 공인 자격증 {len(certs)}종 DB 적재 완료.")
        except Exception as e:
            conn.rollback()
            print(f"[Error] 자격증 적재 실패: {e}")
            raise e
        finally:
            conn.close()

    def run(self):
        return self.sync_to_db()


CertsIngestionManager = CertsManager
__all__ = ["CertsManager", "CertsIngestionManager"]

if __name__ == "__main__":
    mgr = CertsManager()
    mgr.sync_to_db()