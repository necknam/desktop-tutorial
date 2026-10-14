"""Known-model vectors for existing skills and learning resources.

python -m src.db.resource_vectors status
python -m src.db.resource_vectors embed
Original embedding columns and source records are never updated.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from .deployment import connect
# Reuse the already validated OpenAI batch client and vector validator.
from .regulation_vectors import MODEL, DIMENSIONS, embed_texts, vector_values

TABLE = 'public.learning_resource_embedding'
INPUT_VERSION = 'db-resource-fields-and-skill-links/v1'
SPECS = {
    'skills': ('skill_tags', 'skill_id', ('skill_id', 'name', 'category'), None),
    'courses': ('courses', 'course_id', (
        'course_id', 'title', 'category_name', 'org_name', 'url', 'is_matchup',
        'cost', 'duration_weeks', 'recruitment_status', 'syllabus_summary',
        'platform', 'difficulty', 'record_source'), 'course_skill_map'),
    'certifications': ('certifications', 'cert_id', (
        'cert_id', 'title', 'provider', 'test_subjects', 'career_outlook',
        'exam_type', 'pass_score', 'record_source', 'description', 'source_url', 'content_status'), 'cert_skill_map'),
    'books': ('books', 'isbn', (
        'isbn', 'title', 'author', 'publisher', 'kdc_code', 'record_source', 'description', 'source_url', 'content_status'), 'book_skill_map'),
}
DDL = '''
SET LOCAL search_path TO public, extensions;
CREATE TABLE IF NOT EXISTS public.learning_resource_embedding (
    resource_type TEXT NOT NULL CHECK (resource_type IN ('skills','courses','certifications','books')),
    resource_id TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions = 1536),
    input_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    embedding vector(1536) NOT NULL CHECK (vector_norm(embedding) > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (resource_type, resource_id, embedding_model)
);
ALTER TABLE public.learning_resource_embedding ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.learning_resource_embedding FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON public.learning_resource_embedding FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON public.learning_resource_embedding FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.learning_resource_embedding TO service_role;
    END IF;
END $$;
'''


def embedding_text(kind, record):
    """Bound new detail text only; keep the complete source record/hash for grounding."""
    body = dict(record)
    def encode():
        return json.dumps({'resource_type': kind, 'record': body},
                          ensure_ascii=False, sort_keys=True, default=str)
    text = encode()
    if kind in ('books', 'certifications'):
        for field in ('description', 'career_outlook', 'test_subjects'):
            if len(text.encode('utf-8')) <= 8000:
                break
            value = body.get(field)
            if not isinstance(value, str):
                continue
            # Preserve at least 160 characters of each factual field.
            while len(text.encode('utf-8')) > 8000 and len(value) > 160:
                value = value[:max(160, len(value) * 3 // 4)]
                body[field] = value
                text = encode()
    return text


def sources(conn):
    """Only whitelisted real DB fields and real relation rows enter the index."""
    result = {}
    skill_names = {r['skill_id']: r['name'] for r in conn.execute(
        'SELECT skill_id,name FROM public.skill_tags').fetchall()}
    for kind, (table, pk, columns, mapping) in SPECS.items():
        links = {}
        if mapping:
            for row in conn.execute(f'SELECT {pk},skill_id FROM public.{mapping}').fetchall():
                links.setdefault(str(row[pk]), set()).add(row['skill_id'])
        projected = [f"to_jsonb(t)->>'{c}' AS {c}" if c in ('description','source_url','content_status') else f't.{c}' for c in columns]
        for row in conn.execute(f'SELECT {",".join(projected)} FROM public.{table} t ORDER BY {pk}').fetchall():
            key = (kind, str(row[pk]))
            row = dict(row)
            if mapping:
                row['linked_skill_ids'] = sorted(links.get(key[1], set()))
                row['linked_skill_names'] = [skill_names[i] for i in row['linked_skill_ids']]
            text = json.dumps({'resource_type': kind, 'record': row},
                              ensure_ascii=False, sort_keys=True, default=str)
            result[key] = {'record': row, 'text': embedding_text(kind, row),
                           'hash': hashlib.sha256(text.encode('utf-8')).hexdigest()}
    return result


def table_exists(conn):
    return bool(conn.execute('SELECT to_regclass(%s) AS id', (TABLE,)).fetchone()['id'])


def stored(conn):
    if not table_exists(conn):
        return {}
    return {(r['resource_type'], r['resource_id']): r for r in conn.execute(
        f'''SELECT resource_type,resource_id,input_sha256,input_version,dimensions
            FROM {TABLE} WHERE embedding_model=%s''', (MODEL,)).fetchall()}


def is_current(source, previous):
    return bool(previous and previous['input_sha256'] == source['hash']
                and previous['input_version'] == INPUT_VERSION
                and previous['dimensions'] == DIMENSIONS)


def status(conn):
    rows, previous = sources(conn), stored(conn)
    counts = {}
    for kind in SPECS:
        subset = {k: v for k, v in rows.items() if k[0] == kind}
        current = sum(is_current(v, previous.get(k)) for k, v in subset.items())
        counts[kind] = {'source_count': len(subset), 'current_vectors': current,
                        'missing_or_stale_vectors': len(subset) - current}
    return {'embedding_model': MODEL, 'dimensions': DIMENSIONS,
            'vector_table_exists': table_exists(conn), 'resources': counts,
            'orphan_vectors_ignored': len(set(previous) - set(rows)),
            'database_changes': False, 'embedding_api_called': False}


def embed(conn, client=None):
    """Atomic, repeatable ingestion; missing/stale records only, batches of 32."""
    with conn.transaction():
        conn.execute(DDL)
        rows, previous = sources(conn), stored(conn)
        pending = [(k, v) for k, v in rows.items() if not is_current(v, previous.get(k))]
        # Fail before any paid call when the source needs explicit re-chunking.
        if any(len(v['text'].encode('utf-8')) > 8000 for _, v in pending):
            raise ValueError('Resource text exceeds 8000 UTF-8 bytes; review the source before indexing.')
        for start in range(0, len(pending), 32):
            batch = pending[start:start + 32]
            vectors = embed_texts([v['text'] for _, v in batch], client=client)
            for ((kind, rid), row), vector in zip(batch, vectors):
                conn.execute(f'''INSERT INTO {TABLE}
                    (resource_type,resource_id,embedding_model,dimensions,input_version,input_sha256,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (resource_type,resource_id,embedding_model) DO UPDATE SET
                    dimensions=EXCLUDED.dimensions,input_version=EXCLUDED.input_version,
                    input_sha256=EXCLUDED.input_sha256,embedding=EXCLUDED.embedding,updated_at=now()''',
                    (kind, rid, MODEL, DIMENSIONS, INPUT_VERSION, row['hash'], json.dumps(vector)))
        if {k: v['hash'] for k, v in sources(conn).items()} != {k: v['hash'] for k, v in rows.items()}:
            raise ValueError('DB sources changed during embedding. Rolled back; retry with stable sources.')
        result = status(conn)
        result.update(embedded_count=len(pending), database_changes=True,
                      embedding_api_called=bool(pending), original_embeddings_changed=False)
        return result


def similarities(conn, rows, keys, query_vector):
    """Exact cosine search over current, real source rows; no unknown legacy vectors."""
    previous = stored(conn)
    keys = set(keys)
    if any(k not in rows or not is_current(rows[k], previous.get(k)) for k in keys):
        raise ValueError('학습 자원 벡터가 없거나 오래되었습니다. python -m src.db.resource_vectors embed 를 실행하세요.')
    if not keys:
        return {}
    vector = json.dumps(vector_values(query_vector))
    # 138 rows currently: exact pgvector search avoids ANN recall loss.
    conn.execute('SET LOCAL search_path TO public, extensions')
    scored = conn.execute(f'''SELECT resource_type,resource_id,
        1-(embedding <=> %s::vector) AS similarity FROM {TABLE}
        WHERE embedding_model=%s AND dimensions=%s AND input_version=%s''',
        (vector, MODEL, DIMENSIONS, INPUT_VERSION)).fetchall()
    return {(r['resource_type'], r['resource_id']): float(r['similarity'])
            for r in scored if (r['resource_type'], r['resource_id']) in keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-env', default='SUPABASE_DB_URL')
    parser.add_argument('command', choices=('status', 'embed'))
    args = parser.parse_args()
    try:
        with connect(args.db_env) as conn:
            if args.command == 'status':
                conn.execute('SET TRANSACTION READ ONLY')
            result = status(conn) if args.command == 'status' else embed(conn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Connection errors can include host/credentials; do not dump raw exceptions.
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__,
                          'message': 'DB 연결·원본 테이블·OPENAI_API_KEY·임베딩 한도를 확인하세요. 기존 데이터는 유지됩니다.'},
                         ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
