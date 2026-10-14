-- 전달용 독립 스키마. 기존 public 테이블에는 변경 없음.
CREATE SCHEMA IF NOT EXISTS hr_regulation_pilot;

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.institution (
  institution_id VARCHAR(64) PRIMARY KEY NOT NULL,
  name VARCHAR(255) NOT NULL,
  external_code VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation (
  regulation_id VARCHAR(64) PRIMARY KEY NOT NULL,
  institution_id VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  category VARCHAR(40) NOT NULL,
  document_kind VARCHAR(40) NOT NULL,
  FOREIGN KEY (institution_id) REFERENCES hr_regulation_pilot.institution (institution_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation_version (
  regulation_version_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_id VARCHAR(64) NOT NULL,
  version_label VARCHAR(255) NOT NULL,
  title_at_version VARCHAR(255) NOT NULL,
  content_kind VARCHAR(40) NOT NULL,
  coverage_status VARCHAR(40) NOT NULL,
  amendment_date DATE,
  effective_from DATE,
  effective_to DATE,
  date_status VARCHAR(40) NOT NULL,
  review_status VARCHAR(40) NOT NULL,
  availability_status VARCHAR(40) NOT NULL,
  previous_version_id VARCHAR(64),
  review_note TEXT,
  FOREIGN KEY (regulation_id) REFERENCES hr_regulation_pilot.regulation (regulation_id),
  FOREIGN KEY (previous_version_id) REFERENCES hr_regulation_pilot.regulation_version (regulation_version_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation_file (
  regulation_file_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_version_id VARCHAR(64) NOT NULL,
  source_file_id VARCHAR(64),
  file_name VARCHAR(255) NOT NULL,
  file_format VARCHAR(40) NOT NULL,
  file_role VARCHAR(40) NOT NULL,
  storage_key VARCHAR(2048) NOT NULL,
  source_type VARCHAR(40) NOT NULL,
  source_url VARCHAR(2048),
  retrieved_at TIMESTAMPTZ NOT NULL,
  file_hash VARCHAR(128) NOT NULL,
  is_extraction_source BOOLEAN NOT NULL,
  parse_status VARCHAR(40) NOT NULL,
  access_level VARCHAR(40) NOT NULL,
  FOREIGN KEY (regulation_version_id) REFERENCES hr_regulation_pilot.regulation_version (regulation_version_id),
  FOREIGN KEY (source_file_id) REFERENCES hr_regulation_pilot.regulation_file (regulation_file_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation_node (
  regulation_node_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_version_id VARCHAR(64) NOT NULL,
  regulation_file_id VARCHAR(64) NOT NULL,
  parent_node_id VARCHAR(64),
  node_type VARCHAR(40) NOT NULL,
  label VARCHAR(255) NOT NULL,
  title VARCHAR(255),
  path VARCHAR(1024) NOT NULL,
  display_order INT NOT NULL,
  body_text TEXT NOT NULL,
  source_locator JSONB NOT NULL,
  effective_from_override DATE,
  effective_to_override DATE,
  extraction_status VARCHAR(40) NOT NULL,
  review_note TEXT,
  FOREIGN KEY (regulation_version_id) REFERENCES hr_regulation_pilot.regulation_version (regulation_version_id),
  FOREIGN KEY (regulation_file_id) REFERENCES hr_regulation_pilot.regulation_file (regulation_file_id),
  FOREIGN KEY (parent_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation_scope (
  regulation_scope_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_node_id VARCHAR(64) NOT NULL,
  scope_type VARCHAR(40) NOT NULL,
  condition_text TEXT NOT NULL,
  condition_json JSONB,
  applies_to_node_id VARCHAR(64),
  review_status VARCHAR(40) NOT NULL,
  FOREIGN KEY (regulation_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id),
  FOREIGN KEY (applies_to_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.regulation_reference (
  regulation_reference_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_node_id VARCHAR(64) NOT NULL,
  reference_type VARCHAR(40) NOT NULL,
  target_text TEXT NOT NULL,
  target_regulation_id VARCHAR(64),
  target_version_id VARCHAR(64),
  target_node_id VARCHAR(64),
  resolution_status VARCHAR(40) NOT NULL,
  review_note TEXT,
  FOREIGN KEY (regulation_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id),
  FOREIGN KEY (target_regulation_id) REFERENCES hr_regulation_pilot.regulation (regulation_id),
  FOREIGN KEY (target_version_id) REFERENCES hr_regulation_pilot.regulation_version (regulation_version_id),
  FOREIGN KEY (target_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.rag_chunk (
  rag_chunk_id VARCHAR(64) PRIMARY KEY NOT NULL,
  regulation_version_id VARCHAR(64) NOT NULL,
  chunk_text TEXT NOT NULL,
  chunk_type VARCHAR(40) NOT NULL,
  context_path VARCHAR(1024) NOT NULL,
  content_hash VARCHAR(128) NOT NULL,
  chunking_version VARCHAR(255) NOT NULL,
  index_revision VARCHAR(255) NOT NULL,
  embedding_model VARCHAR(255),
  search_status VARCHAR(40) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  token_count INT,
  FOREIGN KEY (regulation_version_id) REFERENCES hr_regulation_pilot.regulation_version (regulation_version_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.rag_chunk_source (
  rag_chunk_source_id VARCHAR(64) PRIMARY KEY NOT NULL,
  rag_chunk_id VARCHAR(64) NOT NULL,
  regulation_node_id VARCHAR(64) NOT NULL,
  source_order INT NOT NULL,
  source_role VARCHAR(40) NOT NULL,
  source_span JSONB,
  FOREIGN KEY (rag_chunk_id) REFERENCES hr_regulation_pilot.rag_chunk (rag_chunk_id),
  FOREIGN KEY (regulation_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id),
  UNIQUE (rag_chunk_id, source_order)
);
