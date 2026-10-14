# GrowPath — 구조 정리 및 DB 구축 도구

1단계 폴더·import 정리본에 DB 스키마 구축·실데이터 이전 도구를 추가했습니다.
DB 도구 사용법은 [DB_DEPLOYMENT.md](docs/DB_DEPLOYMENT.md)에 있습니다.
`python scripts/db.py plan`은 DB 접속 없이 먼저 실행할 수 있습니다.
앱의 사용자 ID 연동, 벡터 RAG, 5점 설문 및 학습 생성기 변경은 후속 단계입니다.

## 구조

```text
saved/
├── app.py
├── load_data.py
├── reset_skills.py
├── requirements.txt
├── config/
│   ├── paths.yaml
│   └── rules.yaml
├── src/
│   ├── common/       # config_loader, pii_masking
│   ├── clients/      # llm_adapter, embedding_service
│   ├── agents/       # assessment_agent, roadmap_agent, self_evolving_optimizer
│   ├── services/     # assessment_service, selection_service,
│   │                 # recommendation_service, prompt_manager
│   ├── pipelines/    # real_data_ingestor, dataset_pipeline,
│   │                 # export_finetuning_dataset
│   ├── db/           # db_client, deployment, catalog, migrations
│   └── processed/    # 이전 exporter 실행 경로 호환용 진입점
├── data/
│   ├── raw/
│   ├── processed/    # 기존 학습 데이터, 새 요구사항으로 검증된 데이터가 아님
│   ├── exports/      # 기존 SFT/DPO 파일의 통합 위치
│   ├── reference/    # 사용자가 제공한 Supabase 구조·실제 스킬 조회 결과
│   └── regulations/
│       └── 인재개발규정_시범_V01/  # 원문·JSON/CSV·SQL·전처리 코드 전체
├── tests/
├── scripts/db.py     # DB 관리 CLI
├── requirements-db.txt
└── docs/
    ├── STAGE1_CHANGES.md
    └── archive/project_all_code_original.txt
```

## 실행 경로

작업 위치는 이 README가 있는 `saved`입니다. 기존 환경의 `.env`는 해당 폴더에서 사용합니다.
배포 ZIP에는 실제 `.env`, Git 메타데이터, Python 캐시를 포함하지 않았습니다.

기존 화면 실행 명령은 유지합니다.

```powershell
streamlit run app.py
```

네트워크·DB 연결 없이 수행하는 이번 단계 검사는 다음과 같습니다.

```powershell
python tests/test_project_structure.py
```

학습 생성기의 새 모듈 경로는 `src.pipelines.export_finetuning_dataset`입니다.
기존 `src/processed/export_finetuning_dataset.py`는 동일 구현으로 전달하는 진입점입니다.
생성기와 기존 학습 데이터에는 다음 단계에서 수정할 하드코딩된 규정 근거와 가상 자료가 남아 있으므로,
이번 구조 정리본을 최종 학습 데이터 생성기로 취급하지 않습니다.

## 데이터 기준

- 가상 데이터 허용 대상은 사용자 프로필뿐입니다.
- 규정·스킬 및 추천 자원은 실제 적재된 DB 데이터를 기준으로 사용하도록 후속 기능을 수정합니다.
- `data/reference/skill_tags.csv`는 사용자가 제공한 36개 스킬의 조회 스냅샷입니다. 운영 중 DB를 대체하는 가상 데이터나 자동 초기화 입력이 아닙니다.
- `rules.yaml`의 이름과 DB의 이름이 다른 항목은 후속 단계에서 DB ID를 보존하여 연결합니다.
- 기존 `reset_skills.py` 및 `load_data.py`에는 초기화·외부 수집 작업이 포함됩니다. 구조 검증에서는 실행하지 않았습니다.
- `data/regulations/인재개발규정_시범_V01`은 원본 패키지 43개 파일을 바이트 단위로 그대로 옮겼습니다. 패키지 내부 상대경로와 원문 해시는 유지됩니다. Supabase 적재 완료를 의미하지 않습니다.
- 별표·예외 시행일·검토 상태 등 규정 패키지의 제한은 유지됩니다.

## 다음 단계

1. DB 구축 도구의 실행 결과를 실제 Supabase에서 확인하고 정수 `users.user_id`에 앱을 맞춤.
2. 규정 적재 및 실데이터 이전 결과 확인. 사용자용 가상 프로필 SQL은 `src/db/demo_users.sql`에 제공.
3. 실제 DB 벡터 및 스킬 관계를 추천에 연결하고, 근거 없는 규정·스킬 대체 경로 제거.
4. 프로젝트별 약 10문항의 5점 자기평가와 Qwen3.5-27B/QLoRA용 근거 기반 학습 데이터 구성.

SQL은 후속 단계에서 Supabase SQL Editor에 붙여 넣을 수 있는 코드로 제공합니다.
