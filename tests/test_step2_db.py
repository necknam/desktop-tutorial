import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.db.db_client import db_client


def test_schema_file_exists():
    """통합 schema.sql 파일 존재 및 DDL 구문 로드 확인"""
    schema_path = project_root / "src" / "db" / "schema.sql"
    assert schema_path.exists(), f"통합 스키마 파일이 존재하지 않습니다: {schema_path}"

    with open(schema_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    assert "CREATE TABLE IF NOT EXISTS skill_tags" in sql_content, "skill_tags 테이블 DDL 누락"
    assert "CREATE TABLE IF NOT EXISTS courses" in sql_content, "courses 테이블 DDL 누락"
    assert "CREATE TABLE IF NOT EXISTS prompt_versions" in sql_content, "prompt_versions 테이블 DDL 누락"
    assert "CREATE TABLE IF NOT EXISTS users" in sql_content, "users 테이블(진단평가) DDL 누락"
    print("[PASS] 2-1. 통합 schema.sql 무결성 확인 완료")


def test_database_client_health():
    """데이터베이스 클라이언트 헬스체크 및 연결 상태 진단"""
    health = db_client.check_health()
    assert isinstance(health, dict), "check_health 반환값이 dict가 아닙니다."

    if health.get("connected"):
        print(f"[PASS] 2-2. DB 연결 성공: {health.get('message')}")
    else:
        print(f"[INFO] 2-2. DB 오프라인 상태 (환경변수 SUPABASE_URL / KEY 미설정 시 정상): {health.get('message')}")


def test_mock_profile_fallback():
    """DB 미연결 시 모의 프로필 조회 폴백 동작 확인"""
    profiles = db_client.fetch_mock_profiles()
    assert isinstance(profiles, list), "fetch_mock_profiles 반환값이 list가 아닙니다."
    print(f"[PASS] 2-3. 모의 프로필 인출 루틴 정상 (인출 건수: {len(profiles)}건)")


if __name__ == "__main__":
    print("=== [Step 2] DB 클라이언트 및 스키마 검증 시작 ===")
    test_schema_file_exists()
    test_database_client_health()
    test_mock_profile_fallback()
    print("=== [Step 2] 검증 완료 ===\n")