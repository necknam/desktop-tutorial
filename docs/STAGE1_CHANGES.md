# 1단계 변경 및 검증 내역

## 변경 범위

기존 `saved`를 기준으로 기능별 폴더를 나누고 내부 import를 새 위치에 맞췄습니다.
새 모델 동작, DB 테이블 수정, 데이터 합성 또는 DB 적재는 이번 단계에 포함하지 않았습니다.

| 기존 위치 | 변경 위치 |
|---|---|
| src/core/config_loader.py | src/common/config_loader.py |
| src/core/pii_masking.py | src/common/pii_masking.py |
| src/core/llm_adapter.py | src/clients/llm_adapter.py |
| src/core/embedding_service.py | src/clients/embedding_service.py |
| src/core/assessment_agent.py | src/agents/assessment_agent.py |
| src/core/roadmap_agent.py | src/agents/roadmap_agent.py |
| src/core/self_evolving_optimizer.py | src/agents/self_evolving_optimizer.py |
| src/core/assessment_service.py | src/services/assessment_service.py |
| src/core/selection_service.py | src/services/selection_service.py |
| src/core/recommendation_service.py | src/services/recommendation_service.py |
| src/core/prompt_manager.py | src/services/prompt_manager.py |
| src/core/real_data_ingestor.py | src/pipelines/real_data_ingestor.py |
| src/core/dataset_pipeline.py | src/pipelines/dataset_pipeline.py |
| src/processed/export_finetuning_dataset.py | src/pipelines/export_finetuning_dataset.py |

`app.py`, `load_data.py`, `reset_skills.py`, 기존 테스트의 내부 import도 함께 갱신했습니다.
`src.db` 경로는 유지하며 `db.db_client`를 사용하는 이중 import를 `src.db.db_client`로 통일했습니다.
`app.py`는 폴더 이름이 `saved`일 때 상위 폴더를 루트로 잡던 식을 실제 `app.py`의 부모 폴더로 수정했습니다.
설정 로더와 생성기는 같은 폴더 깊이로 이동하여 기존 프로젝트 루트·설정·출력 경로를 유지합니다.

이전 exporter 파일 경로에는 짧은 전달용 진입점을 남겨 기존 명령을 지원합니다.
이전 `src.core.*` import를 사용하는 외부 코드가 있다면 위 표에 맞춰 변경해야 합니다.

## 자료 이동·추가

- 인재개발규정 패키지 전체 → `data/regulations/인재개발규정_시범_V01/`.
- Supabase 구조 CSV → `data/reference/supabase_schema.csv`.
- 실제 스킬 36개 CSV → `data/reference/skill_tags.csv`.
- 같은 내용임을 해시로 확인한 루트 `exports/` 2개 파일 → `data/exports/`와 통합.
- `project_all_code.txt` → `docs/archive/project_all_code_original.txt`. 원본 코드 스냅샷으로 보관하며 현재 실행 코드와 구분.
- 기존 `data/processed` 학습 파일과 설계 문서는 원본 그대로 보존.

## 보존·검증 결과

- `paths.yaml`, `rules.yaml`, `src/db/schema.sql`, `requirements.txt`: 원본 바이트와 일치.
- 실제 DB 스킬 36개의 ID·이름·분류: 기존 `STANDARD_SKILLS`와 모두 일치.
- 기존 Python 파일 25개: import, 모듈 설명, 프로젝트 루트 경로 식을 제외한 AST가 원본과 일치.
- 규정 패키지 43개 파일: 원본 바이트와 모두 일치.
- 원래 프로젝트의 비Python 파일 17개: 이동·중복 통합 후 내용 보존 확인.
- Python 파일 35개: 구문 검사 통과.
- 내부 import 심볼 45개: 대상 파일과 정의 존재 확인.
- 오프라인 구조 검사 5개: 통과. 경로, 실제 스킬 보존, 규정 파일 연결, 이전 CLI 전달을 포함.

검증 환경에는 Streamlit, PyYAML, OpenAI/Supabase/PostgreSQL 연결 패키지가 설치되어 있지 않습니다.
따라서 실제 화면 실행, 전체 런타임 import 및 DB/API 통합 테스트는 실행하지 않았습니다.
기존 요구사항 파일에는 PyYAML·psycopg2 직접 의존성이 누락되어 있으며 후속 실행환경 정비 대상입니다.
이번 통과 결과는 폴더·경로 변경 검증이며, 기존 DB 불일치나 RAG·설문 문제가 해결됐다는 의미는 아닙니다.
