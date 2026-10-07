import os
from pathlib import Path
import yaml

# saved/ 프로젝트 루트 디렉터리 경로 자동 감지
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_yaml(filename: str) -> dict:
    """config 폴더 내 YAML 파일을 UTF-8로 안전하게 파싱"""
    target_path = PROJECT_ROOT / "config" / filename
    if not target_path.exists():
        raise FileNotFoundError(f"[Config Error] 설정 파일을 찾을 수 없습니다: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_paths_config() -> dict:
    """paths.yaml 설정 인출"""
    return load_yaml("paths.yaml")


def get_rules_config() -> dict:
    """rules.yaml (36개 스킬, 정규식 패턴, 10대 시나리오) 설정 인출"""
    return load_yaml("rules.yaml")


def resolve_project_path(relative_path: str) -> Path:
    """프로젝트 루트 기준 절대 경로 생성"""
    return PROJECT_ROOT / relative_path