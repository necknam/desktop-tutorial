import sys
import subprocess
from pathlib import Path

tests_dir = Path(__file__).resolve().parent

test_steps = [
    ("Step 1: 경로 및 YAML 설정 검증", tests_dir / "test_step1_config.py"),
    ("Step 2: DB 클라이언트 및 스키마 검증", tests_dir / "test_step2_db.py"),
    ("Step 3: 코어 컴포넌트(PII, 벡터, 서킷) 검증", tests_dir / "test_step3_core.py"),
    ("Step 4: 에이전트 및 파이프라인 검증", tests_dir / "test_step4_agents.py"),
]

def run_suite():
    print("==================================================")
    print("   GrowPath AI 프로젝트 단계별 무결성 진단 시작   ")
    print("==================================================\n")

    failed = False
    for step_name, script_path in test_steps:
        print(f"▶ 실행 중: {step_name} ({script_path.name})")
        res = subprocess.run([sys.executable, str(script_path)], capture_output=False)
        if res.returncode != 0:
            print(f"❌ [FAIL] {step_name}에서 오류가 발생했습니다. 해당 단계를 점검하세요.\n")
            failed = True
            break
        print("-" * 50)

    if not failed:
        print("\n🎉 모든 단계별 테스트를 통과했습니다! 프로젝트 구조가 완전히 정상입니다.")
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    run_suite()