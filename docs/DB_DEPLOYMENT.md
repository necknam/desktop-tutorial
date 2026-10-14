# DB 초기 구축·실데이터 이전 도구

빈 PostgreSQL/Supabase와 현재 DB에 같은 스키마를 적용하기 위한 도구입니다.
현재 DB의 초기화를 요구하지 않습니다. 기존 구조가 다른 경우 임의로 자료형을 변환하거나 테이블을 삭제하지 않고 중단합니다.

## 포함 범위

- 제공된 실제 DB 구조 24개 테이블, 앱 저장용 9개 테이블, 규정 패키지의 9개 테이블: 총 42개.
- PK, FK, 고유 제약, 원래 벡터 인덱스 및 신규 저장 테이블 인덱스.
- `users.user_id`는 정수 유지. 앱 연결에 필요한 email·department·job_title·grade·source_type·updated_at 컬럼 추가.
- 질문·응답, 로드맵 생성·채택 간 복합 FK로 다른 세션의 자료를 잘못 연결하는 경우 차단.
- 응답 점수는 1~5 범위. 질문 생성/화면의 5점 척도 개편은 이후 단계.
- 마이그레이션 파일별 SHA-256 및 적용 시각을 `growpath_meta.schema_migrations`에 저장. 적용한 파일의 내용이 달라지면 중단.
- 한 명령의 DB 변경은 한 트랜잭션에서 실행. PostgreSQL 시퀀스 번호는 롤백 시 빈 번호가 남을 수 있습니다.
- 기존 업무 데이터·벡터와 규정 원문만 이전. 가상 규정·가상 스킬·가상 강좌 생성 없음.
- 사용자가 요청한 가상 사용자 3개는 `demo-users`를 명시했을 때만 추가. 보유·목표 스킬은 빈 목록으로 두며 가상의 스킬 태그를 만들지 않음.

기존 `src/db/schema.sql`은 과거 스키마입니다. 이번 도구의 기준은 `src/db/migrations/*.sql`이며 과거 SQL을 새 DB에 따로 실행하지 않습니다.

## 1. 설치 및 오프라인 확인

새 ZIP의 `saved` 폴더에서 실행합니다. 실제 `.env`는 원래 파일을 사용합니다.

```powershell
python scripts/db.py plan
python -m pip install -r requirements-db.txt
```

`plan`은 표준 라이브러리만 사용하고 DB에 접속하지 않습니다. 규정 파일의 해시·구조·적재 순서를 확인합니다.

DB 접속 문자열은 `.env`의 `SUPABASE_DB_URL`을 사용합니다. 이는 PostgreSQL 접속 URL이며 Supabase HTTP API URL/API 키와 다릅니다.
SSL은 기본 `require`입니다. SSL 없는 로컬 개발 DB에만 `DB_SSLMODE=disable`을 명시합니다.

## 2. 현재 DB에 필요한 구조 추가

```powershell
python scripts/db.py migrate
python scripts/db.py load-regulations
python scripts/db.py demo-users
python scripts/db.py verify
```

- `migrate`: 새 테이블과 필요한 추가 컬럼·관계·인덱스 생성. 기존 스킬·강좌 행 변경 없음.
- `load-regulations`: 프로젝트의 실제 규정 JSON 적재. 동일 PK는 원문 패키지 값으로 갱신되므로 DB에서 별도로 검토 상태 등을 수정한 뒤에는 재적재 시 그 변경이 덮일 수 있습니다. 최초 적재 이후에는 필요한 데이터 갱신 여부를 확인합니다.
- `demo-users`: 가상 사용자 3명 및 테스트 프로필 삽입. 동일 이메일/프로필 ID는 추가하지 않음.
- `verify`: 컬럼 자료형, PK/FK 연결 및 테이블·벡터 건수 조회. 검색 품질이나 규정 의미 검증을 대신하지 않음.

빈 DB에서 이 네 명령만 실행하면 스킬·강좌가 자동으로 생기지 않습니다. 아래 실데이터 export/import가 필요합니다.

## 3. 배포할 빈 DB에 실데이터까지 한 번에 복원

`.env`에 기존 DB 접속 URL을 `SOURCE_DB_URL`, 새 DB 접속 URL을 `TARGET_DB_URL`로 각각 설정합니다. 값은 채팅이나 저장소에 공유하지 않습니다.

기존 DB에서 실제 자원·매핑·벡터를 내보냅니다. 아래 모델명은 반드시 현재 벡터를 생성한 실제 모델명으로 바꿉니다.

```powershell
python scripts/db.py --db-env SOURCE_DB_URL export-data --output data/transfer/source_001 --embedding-model ACTUAL_EMBEDDING_MODEL
```

이후 새 DB에 스키마·관계·실데이터·규정을 복원하고 가상 사용자도 추가합니다.

```powershell
python scripts/db.py --db-env TARGET_DB_URL bootstrap --input data/transfer/source_001 --demo-users
```

`bootstrap`은 다음을 하나의 DB 트랜잭션에서 처리합니다.

1. 묶음의 파일 해시·필드·ID·벡터 차원을 검증.
2. 적용되지 않은 스키마 마이그레이션 실행.
3. 부모 테이블부터 기존 ID를 유지하여 자원과 연결 테이블 적재.
4. 규정 9개 테이블이 묶음에 모두 있다면 해당 DB 자료를 사용. 묶음에 규정이 전혀 없다면 프로젝트의 검증된 원문 패키지 사용. 일부만 있으면 중단.
5. 정수 ID 시퀀스를 적재된 최대 ID와 동기화.
6. 요청한 경우에만 가상 사용자 추가.
7. 실제 컬럼·외래키·건수 확인.

스키마 적용과 데이터 이전을 나누려면 `migrate` 후 `import-data --input ...`를 사용할 수 있습니다.
스킬 ID/이름/카테고리가 목적지와 충돌하거나 벡터 모델이 다르면 덮어쓰지 않고 중단합니다.
기존 목적지에 모델 이력 없는 벡터가 이미 있으면 자동으로 합치지 않습니다. 기존 모델을 확인하거나 빈 목적지를 사용합니다.

## 이전하는 데이터와 남은 연결 작업

- 기본 이전 대상: 스킬·강좌·도서·자격증·직무·전공, 5개 스킬 매핑, 규정 9개 테이블.
- 기존 사용자·대화·평가 결과는 이 실데이터 묶음에 포함하지 않습니다. 사용자는 가상 데이터만 사용한다는 현재 요구사항을 따른 것입니다.
- 기존 `public.regulations`, `ai_cases`는 구조만 재현하고 데이터는 기본 이전에서 제외합니다. 규정의 기준은 최신 `hr_regulation_pilot`입니다.
- 벡터는 저장된 값 그대로 이전하며 재임베딩하지 않습니다. 1536차원 여부를 검사하며 내보낸 모델명은 운영자가 확인한 값이어야 합니다.
- 원문 PDF/이미지는 프로젝트의 규정 패키지에 보존됩니다. Supabase Storage 버킷·파일 업로드와 공개 URL 연결은 별도입니다. DB의 `storage_key`는 패키지 기준 상대경로입니다.
- CSV 스키마 조회에 포함되지 않은 사용자 정의 RPC, 기존 RLS 정책, Auth 계정, Storage 객체는 자동 추정하거나 복제하지 않습니다.
- 새 저장·규정 테이블은 RLS를 켜고, Supabase의 `service_role`이 있을 경우 서버 측 접근 권한을 부여합니다. 공개 클라이언트용 정책은 만들지 않습니다. DB URL을 사용하는 관리 도구와 앱용 API 키의 권한은 별개입니다.
- 앱의 기존 UUID 사용자 처리, DB 실패 후 메모리 대체, RAG 검색, 설문 생성 및 학습 생성기는 이 도구 추가만으로 변경되지 않습니다. 앱과 DB를 연결하는 다음 코드 수정 단계가 남아 있습니다.

## 검증 범위

오프라인 배포 검사 12개와 1단계 구조 검사 5개를 실행했습니다. 실제 규정 439개 행의 PK/FK 및 원본 해시를 점검했습니다.
현재 검증 환경에는 PostgreSQL 서버·psycopg가 없어 실제 PostgreSQL에서의 DDL/트랜잭션/벡터 복원 검증은 아직 수행하지 않았습니다.
처음에는 실행 결과를 공유하여 실제 Supabase 환경에서 확인합니다.
