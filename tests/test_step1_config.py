import os
import sys
from pathlib import Path

# 프로젝트 루트 경로를 sys.path에 등록
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.config_loader import PROJECT_ROOT, resolve_project_path


def test_project_root_detection():
    """PROJECT_ROOT가 실제 프로젝트 최상위를 가리키는지 확인"""
    assert PROJECT_ROOT.exists(), f"PROJECT_ROOT 경로가 존재하지 않습니다: {PROJECT_ROOT}"
    assert (PROJECT_ROOT / "src").exists(), f"src 디렉터리를 찾을 수 없습니다: {PROJECT_ROOT / 'src'}"
    print("[PASS] 1-1. PROJECT_ROOT 자동 감지 정상")


def test_config_directory_and_files():
    """config 디렉터리 및 필수 파일 접근성 확인"""
    config_dir = resolve_project_path("config")
    assert config_dir.exists(), f"config 디렉터리가 존재하지 않습니다: {config_dir}"

    paths_file = config_dir / "paths.yaml"
    rules_file = config_dir / "rules.yaml"

    if paths_file.exists():
        from src.core.config_loader import get_paths_config
        paths_cfg = get_paths_config()
        assert isinstance(paths_cfg, dict), "paths.yaml 파싱 결과가 dict 형태가 아닙니다."
        print("[PASS] 1-2. paths.yaml 파싱 정상")
    else:
        print("[WARN] 1-2. paths.yaml 파일이 아직 생성되지 않았습니다 (건너뜀).")

    if rules_file.exists():
        from src.core.config_loader import get_rules_config
        rules_cfg = get_rules_config()
        assert isinstance(rules_cfg, dict), "rules.yaml 파싱 결과가 dict 형태가 아닙니다."
        print("[PASS] 1-3. rules.yaml 파싱 정상")
    else:
        print("[WARN] 1-3. rules.yaml 파일이 아직 생성되지 않았습니다 (건너뜀).")


if __name__ == "__main__":
    print("=== [Step 1] 경로 및 설정 파일 연결성 검증 시작 ===")
    test_project_root_detection()
    test_config_directory_and_files()
    print("=== [Step 1] 검증 완료 ===\n")