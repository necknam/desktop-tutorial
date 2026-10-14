"""Read-only connectivity checks. Run from saved: python api_test.py"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def openai_config():
    return (
        os.getenv("OPENAI_API_KEY", "").strip(),
        os.getenv("OPENAI_MODEL", "gpt-5-nano").strip(),
    )


def check_database():
    from src.db.deployment import connect, check_schema
    from src.db.regulation_vectors import status

    with connect("SUPABASE_DB_URL") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        check_schema(conn)
        users = conn.execute(
            "SELECT COUNT(*) AS n FROM public.users WHERE source_type IN ('TEST_MANUAL','TEST_AUTO')"
        ).fetchone()["n"]
        skills = conn.execute("SELECT COUNT(*) AS n FROM public.skill_tags").fetchone()["n"]
        return {
            "status": "PASS",
            "environment_variable": "SUPABASE_DB_URL",
            "application_schema": "PASS",
            "test_user_count": users,
            "skill_tag_count": skills,
            "regulation_vectors": status(conn),
            "database_changes": False,
        }


def check_openai():
    key, model = openai_config()
    if not key:
        return {"status": "NOT_CONFIGURED", "priority": 1, "model": model}
    from openai import OpenAI

    with OpenAI(api_key=key, timeout=10, max_retries=0) as client:
        item = client.models.retrieve(model)
    return {
        "status": "PASS",
        "priority": 1,
        "model": item.id,
        "checked": "API authentication and model metadata access",
        "generation_tested": False,
    }


def check_local_qwen():
    base = os.getenv("LOCAL_LLM_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("LOCAL_LLM_MODEL", "").strip()
    if not base or not model:
        return {
            "status": "NOT_CONFIGURED",
            "priority": 2,
            "note": "Configure LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL in .env.",
        }
    import httpx

    key = os.getenv("LOCAL_LLM_API_KEY", "ollama")
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(base + "/models", headers={"Authorization": "Bearer " + key})
        response.raise_for_status()
    ids = {item["id"] for item in response.json().get("data", [])}
    return {
        "status": "PASS" if model in ids else "FAIL",
        "priority": 2,
        "model": model,
        "checked": "Configured served model ID is listed",
        "generation_tested": False,
    }


def safe_check(function):
    try:
        return function()
    except Exception as exc:
        return {"status": "FAIL", "error_type": type(exc).__name__}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-only", action="store_true")
    parser.add_argument("--embedding-smoke", action="store_true", help="One paid embedding API request; no DB write")
    args = parser.parse_args(argv)
    if args.db_only and args.embedding_smoke:
        parser.error("--db-only cannot be combined with --embedding-smoke")
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        print("Install requirements-db.txt before running this script.")
        return 1
    result = {"database": safe_check(check_database)}
    if not args.db_only:
        result["openai"] = safe_check(check_openai)
        result["local_finetuned_qwen"] = safe_check(check_local_qwen)
        result["llm_route"] = (
            "OpenAI (1순위)"
            if result["openai"]["status"] == "PASS"
            else "Qwen 파인튜닝 모델 (2순위)"
            if result["local_finetuned_qwen"]["status"] == "PASS"
            else "LLM 모델 없음"
        )
        result["llm_note"] = "Metadata checks do not prove generation availability, quota, or output quality."
    if args.embedding_smoke:

        def embedding_check():
            from src.db.regulation_vectors import embed_texts, MODEL

            vector = embed_texts(["연결 확인"])
            return {"status": "PASS", "model": MODEL, "dimensions": len(vector[0]), "database_changes": False}

        result["embedding_smoke"] = safe_check(embedding_check)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    failed = result["database"]["status"] != "PASS"
    failed |= result.get("llm_route") == "LLM 모델 없음"
    failed |= result.get("embedding_smoke", {}).get("status") == "FAIL"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())