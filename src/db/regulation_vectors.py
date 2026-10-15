"""Regulation pgvector storage/search. Run: python -m src.db.regulation_vectors --help."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from .deployment import connect

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
TABLE = 'hr_regulation_pilot.rag_chunk_embedding'
INPUT_VERSION = 'context_path+blank_line+chunk_text/v1'
DDL = '''
SET LOCAL search_path TO public, extensions;
CREATE TABLE IF NOT EXISTS hr_regulation_pilot.rag_chunk_embedding (
    rag_chunk_id VARCHAR(64) NOT NULL REFERENCES hr_regulation_pilot.rag_chunk(rag_chunk_id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions = 1536),
    input_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    embedding vector(1536) NOT NULL CHECK (vector_norm(embedding) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (rag_chunk_id, embedding_model)
);
CREATE INDEX IF NOT EXISTS rag_chunk_embedding_model_idx
    ON hr_regulation_pilot.rag_chunk_embedding (embedding_model);
ALTER TABLE hr_regulation_pilot.rag_chunk_embedding ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON hr_regulation_pilot.rag_chunk_embedding FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON hr_regulation_pilot.rag_chunk_embedding FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON hr_regulation_pilot.rag_chunk_embedding FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
        GRANT USAGE ON SCHEMA hr_regulation_pilot TO service_role;
        GRANT SELECT, INSERT, UPDATE, DELETE ON hr_regulation_pilot.rag_chunk_embedding TO service_role;
    END IF;
END $$;
'''

# Source data remains authoritative. Superseded/not_for_answer chunks are excluded from embedding.
SOURCE_SQL = '''SELECT c.*, v.review_status AS version_review_status,
    v.availability_status, v.effective_from, v.effective_to
    FROM hr_regulation_pilot.rag_chunk c
    JOIN hr_regulation_pilot.regulation_version v USING (regulation_version_id)
    WHERE c.search_status NOT IN ('superseded', 'not_for_answer')
    ORDER BY c.rag_chunk_id'''

HASH_SQL = "encode(digest(convert_to(c.context_path || E'\\n\\n' || c.chunk_text,'UTF8'),'sha256'),'hex')"
SEARCH_SQL = '''WITH eligible AS MATERIALIZED (
    SELECT c.rag_chunk_id, c.regulation_version_id, c.chunk_text, c.context_path,
           c.search_status, v.review_status, v.availability_status,
           v.effective_from, v.effective_to, e.embedding
    FROM hr_regulation_pilot.rag_chunk_embedding e
    JOIN hr_regulation_pilot.rag_chunk c USING (rag_chunk_id)
    JOIN hr_regulation_pilot.regulation_version v USING (regulation_version_id)
    WHERE e.embedding_model=%s AND e.dimensions=1536 AND e.input_version=%s
      AND e.input_sha256=''' + HASH_SQL + '''
      AND c.search_status IN ('active','pending_review','needs_review')
      AND (%s OR (c.search_status='active' AND v.review_status='approved'
                  AND v.availability_status='available'))
      AND (%s::text IS NULL OR c.regulation_version_id=%s)
      AND v.effective_from IS NOT NULL AND v.effective_from<=%s
      AND (v.effective_to IS NULL OR v.effective_to>%s)
      AND EXISTS (SELECT 1 FROM hr_regulation_pilot.rag_chunk_source s
                  WHERE s.rag_chunk_id=c.rag_chunk_id)
      AND NOT EXISTS (
          SELECT 1 FROM hr_regulation_pilot.rag_chunk_source s
          JOIN hr_regulation_pilot.regulation_node n USING (regulation_node_id)
          WHERE s.rag_chunk_id=c.rag_chunk_id
            AND ((n.effective_from_override IS NOT NULL AND n.effective_from_override>%s)
              OR (n.effective_to_override IS NOT NULL AND n.effective_to_override<=%s))
      )
), scored AS (
    SELECT *,1-(embedding <=> %s::vector) AS similarity FROM eligible
)
SELECT rag_chunk_id,regulation_version_id,chunk_text,context_path,search_status,
       review_status,availability_status,effective_from,effective_to,similarity
FROM scored WHERE similarity >= %s
ORDER BY similarity DESC,rag_chunk_id LIMIT %s'''


def input_text(row):
    if not isinstance(row['chunk_text'],str) or not row['chunk_text'].strip():
        raise ValueError('Empty regulation text: '+row['rag_chunk_id'])
    return row['context_path']+'\n\n'+row['chunk_text']


def input_hash(row):
    return hashlib.sha256(input_text(row).encode('utf-8')).hexdigest()


def vector_values(value):
    values = json.loads(value) if isinstance(value,str) else value
    if not isinstance(values,list) or len(values)!=DIMENSIONS or any(
        isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values
    ) or not any(x!=0 for x in values):
        raise ValueError('Invalid regulation vector: expected 1536 finite values and nonzero norm')
    return values


def table_exists(conn):
    return bool(conn.execute('SELECT to_regclass(%s) AS id',(TABLE,)).fetchone()['id'])


def sources(conn):
    if not conn.execute("SELECT to_regclass('hr_regulation_pilot.rag_chunk') AS id").fetchone()['id']:
        raise ValueError('Load the regulation schema and real regulation data first.')
    return conn.execute(SOURCE_SQL).fetchall()


def stored(conn):
    if not table_exists(conn):
        return {}
    return {r['rag_chunk_id']:r for r in conn.execute(
        f'SELECT rag_chunk_id,input_sha256,input_version FROM {TABLE} WHERE embedding_model=%s',
        (MODEL,)).fetchall()}


def needs_embedding(row, previous):
    old = previous.get(row['rag_chunk_id'])
    return not old or old['input_version']!=INPUT_VERSION or old['input_sha256']!=input_hash(row)


def status(conn):
    rows, previous = sources(conn), stored(conn)
    pending = sum(needs_embedding(r,previous) for r in rows)
    return {'source_chunks':len(rows),'vector_table_exists':table_exists(conn),
            'embedding_model':MODEL,'dimensions':DIMENSIONS,'current_vectors':len(rows)-pending,
            'missing_or_stale_vectors':pending,'search_statuses':dict(Counter(r['search_status'] for r in rows)),
            'version_review_statuses':dict(Counter(r['version_review_status'] for r in rows)),
            'database_changes':False,'embedding_api_called':False}


def embed_texts(texts, client=None):
    # UTF-8 byte length is a conservative token upper bound; never silently truncate.
    if not texts or any(not t.strip() or len(t.encode('utf-8'))>8000 for t in texts):
        raise ValueError('Empty or oversized input; re-chunk inputs over 8000 UTF-8 bytes first.')
    if client is None:
        from openai import OpenAI
        key = os.getenv('OPENAI_API_KEY')
        if not key:
            raise ValueError('OPENAI_API_KEY is required for embed/search; status and export do not need it.')
        client = OpenAI(api_key=key,base_url='https://api.openai.com/v1',timeout=30,max_retries=2)
    response = client.embeddings.create(model=MODEL,input=texts,dimensions=DIMENSIONS,encoding_format='float')
    if response.model!=MODEL or len(response.data)!=len(texts):
        raise ValueError('Embedding API model/count mismatch; nothing from this batch was stored.')
    items = sorted(response.data,key=lambda item:item.index)
    if [item.index for item in items]!=list(range(len(texts))):
        raise ValueError('Embedding API response indices do not match the inputs.')
    return [vector_values(item.embedding) for item in items]


def store_rows(conn, entries):
    for entry in entries:
        current = conn.execute('''SELECT rag_chunk_id,chunk_text,context_path
            FROM hr_regulation_pilot.rag_chunk WHERE rag_chunk_id=%s FOR SHARE''',
            (entry['rag_chunk_id'],)).fetchone()
        if current is None or input_hash(current)!=entry['input_sha256']:
            raise ValueError('Regulation source missing/changed; rerun embed: '+entry['rag_chunk_id'])
        vector = json.dumps(vector_values(entry['embedding']),allow_nan=False)
        conn.execute(f'''INSERT INTO {TABLE}
            (rag_chunk_id,embedding_model,dimensions,input_version,input_sha256,embedding)
            VALUES (%s,%s,%s,%s,%s,%s::vector)
            ON CONFLICT (rag_chunk_id,embedding_model) DO UPDATE SET
              dimensions=EXCLUDED.dimensions,input_version=EXCLUDED.input_version,
              input_sha256=EXCLUDED.input_sha256,embedding=EXCLUDED.embedding,updated_at=now()''',
            (entry['rag_chunk_id'],MODEL,DIMENSIONS,INPUT_VERSION,entry['input_sha256'],vector))


def embed(conn, batch_size=16):
    if not 1<=batch_size<=32:
        raise ValueError('batch-size must be between 1 and 32')
    rows, previous = sources(conn), stored(conn)
    pending = [r for r in rows if needs_embedding(r,previous)]
    if not rows:
        raise ValueError('No real regulation chunks loaded.')
    for row in pending:
        if len(input_text(row).encode('utf-8'))>8000:
            raise ValueError('Re-chunk oversized input before embedding: '+row['rag_chunk_id'])
    if pending and not os.getenv('OPENAI_API_KEY'):
        raise ValueError('OPENAI_API_KEY is required for embedding.')
    conn.execute(DDL,prepare=False)
    conn.commit()
    saved = 0
    for offset in range(0,len(pending),batch_size):
        batch = pending[offset:offset+batch_size]
        vectors = embed_texts([input_text(row) for row in batch])
        with conn.transaction():
            conn.execute('SET LOCAL search_path TO public, extensions')
            store_rows(conn,[{'rag_chunk_id':row['rag_chunk_id'],'input_sha256':input_hash(row),'embedding':vector}
                             for row,vector in zip(batch,vectors)])
        saved += len(batch)
        print(f'Embedded {saved}/{len(pending)} chunks',file=sys.stderr)
    return {'saved':saved,'skipped_unchanged':len(rows)-len(pending),'embedding_model':MODEL,
            'review_statuses_changed':False,'note':'Completed batches are committed; rerun to resume.'}


def search(conn,query,limit=5,threshold=0.25,include_review=False,as_of=None,version_id=None):
    if not 1<=limit<=50 or not math.isfinite(threshold) or not -1<=threshold<=1:
        raise ValueError('limit must be 1..50 and threshold must be finite in -1..1')
    if not table_exists(conn):
        raise ValueError('Run embed to create regulation vectors first.')
    day = as_of or date.today()
    vector = json.dumps(embed_texts([query])[0])
    conn.execute('SET LOCAL search_path TO public, extensions')
    rows = conn.execute(SEARCH_SQL,(MODEL,INPUT_VERSION,include_review,version_id,version_id,
                                   day,day,day,day,vector,threshold,limit)).fetchall()
    for row in rows:
        row['requires_review'] = (row['search_status']!='active' or row['review_status']!='approved'
                                  or row['availability_status']!='available')
        row['sources'] = conn.execute('''SELECT s.regulation_node_id,s.source_role,s.source_span,
            n.label,n.path,n.source_locator,n.extraction_status,n.review_note,
            n.effective_from_override,n.effective_to_override,f.regulation_file_id,f.file_name,f.storage_key
            FROM hr_regulation_pilot.rag_chunk_source s
            JOIN hr_regulation_pilot.regulation_node n USING (regulation_node_id)
            JOIN hr_regulation_pilot.regulation_file f USING (regulation_file_id)
            WHERE s.rag_chunk_id=%s ORDER BY s.source_order''',(row['rag_chunk_id'],)).fetchall()
    return {'matches':rows,'embedding_model':MODEL,'as_of':str(day),
            'review_results_requested':include_review,'no_evidence':not rows}


def export_vectors(conn,output):
    if output.exists():
        raise ValueError('Output file exists; choose a new name.')
    if not table_exists(conn):
        raise ValueError('No regulation vector table; run embed first.')
    conn.execute('SET LOCAL search_path TO public, extensions')
    rows = conn.execute(f'''SELECT e.rag_chunk_id,e.input_sha256,e.embedding::text AS embedding
        FROM {TABLE} e JOIN hr_regulation_pilot.rag_chunk c USING (rag_chunk_id)
        WHERE e.embedding_model=%s AND e.input_version=%s AND e.input_sha256={HASH_SQL}
          AND c.search_status NOT IN ('superseded', 'not_for_answer')
        ORDER BY e.rag_chunk_id''',(MODEL,INPUT_VERSION)).fetchall()
    if len(rows)!=len(sources(conn)):
        raise ValueError('Some regulation vectors are missing/stale; finish embed before export.')
    for row in rows:
        row['embedding'] = vector_values(row['embedding'])
    payload = {'format_version':1,'kind':'regulation_vectors','model':MODEL,'dimensions':DIMENSIONS,
               'input_version':INPUT_VERSION,'rows':rows}
    data = json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)
    wrapper = {'sha256':hashlib.sha256(data.encode('utf-8')).hexdigest(),'payload':data}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(wrapper,stream,ensure_ascii=False)
    return {'exported_vectors':len(rows),'output':str(output),'model':MODEL}


def import_vectors(conn,path):
    wrapper = json.loads(path.read_text(encoding='utf-8'))
    data = wrapper['payload']
    if hashlib.sha256(data.encode('utf-8')).hexdigest()!=wrapper['sha256']:
        raise ValueError('Regulation vector export checksum mismatch')
    payload = json.loads(data)
    if (payload['format_version'],payload['kind'],payload['model'],payload['dimensions'],payload['input_version']) != (
            1,'regulation_vectors',MODEL,DIMENSIONS,INPUT_VERSION):
        raise ValueError('Regulation vector export format/model mismatch')
    rows = payload['rows']
    if len({r['rag_chunk_id'] for r in rows})!=len(rows):
        raise ValueError('Duplicate regulation chunk IDs')
    current = {r['rag_chunk_id']:r for r in sources(conn)}
    for row in rows:
        vector_values(row['embedding'])
        if row['rag_chunk_id'] not in current or input_hash(current[row['rag_chunk_id']])!=row['input_sha256']:
            raise ValueError('Restore matching regulation source data before vector import.')
    conn.execute(DDL,prepare=False)
    store_rows(conn,rows)
    return {'imported_vectors':len(rows),'model':MODEL,'embedding_api_called':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-env',default='SUPABASE_DB_URL')
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status',help='Read-only counts and remaining work; no API call')
    emb = commands.add_parser('embed',help='Create vector table and embed real chunks; API charges apply')
    emb.add_argument('--batch-size',type=int,default=16)
    query = commands.add_parser('search',help='Embed query and search the regulation vectors')
    query.add_argument('--query',required=True)
    query.add_argument('--limit',type=int,default=5)
    query.add_argument('--threshold',type=float,default=0.25)
    query.add_argument('--include-review',action='store_true')
    query.add_argument('--as-of',type=date.fromisoformat)
    query.add_argument('--version-id')
    exp = commands.add_parser('export',help='Export regulation vectors separately from db.py source bundle')
    exp.add_argument('--output',type=Path,required=True)
    imp = commands.add_parser('import',help='Restore vectors after restoring matching source regulations')
    imp.add_argument('--input',type=Path,required=True)
    args = parser.parse_args(argv)
    with connect(args.db_env) as conn:
        if args.command in ('status','search','export'):
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if args.command=='status':
            result = status(conn)
        elif args.command=='embed':
            result = embed(conn,args.batch_size)
        elif args.command=='search':
            result = search(conn,args.query,args.limit,args.threshold,args.include_review,args.as_of,args.version_id)
        elif args.command=='export':
            result = export_vectors(conn,args.output)
        else:
            result = import_vectors(conn,args.input)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Avoid exposing connection strings, keys, or raw API request data.
        print(f'{type(exc).__name__}: regulation vector operation failed; completed batches remain saved.',file=sys.stderr)
        raise SystemExit(1)