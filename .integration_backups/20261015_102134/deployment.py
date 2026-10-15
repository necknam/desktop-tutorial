"""Reproducible schema and real-data migration. Application code is never imported."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT/'src/db/catalog.json').read_text(encoding='utf-8'))
# API fields absent in a response must be representable as unknown, not fabricated.
INGESTION_NULLABLE = {'public.courses': ('cost', 'is_matchup', 'difficulty'),
                      'public.certifications': ('provider',)}
for _table, _columns in INGESTION_NULLABLE.items():
    for _column in CATALOG[_table]['columns']:
        if _column['column'] in _columns:
            _column['not_null'] = False
            _column['default'] = None



# Canonical source content travels with the five master tables on export/bootstrap.
CONTENT_COLUMNS = {}
for _name in ('courses','books','certifications','jobs','majors'):
    _extra = {'source_url':'text', 'content_status':'text', 'source_verified_at':'timestamp with time zone'}
    if _name != 'courses':
        _extra['description'] = 'text'
    CONTENT_COLUMNS['public.'+_name] = _extra
    for _field, _type in _extra.items():
        if not any(c['column']==_field for c in CATALOG['public.'+_name]['columns']):
            CATALOG['public.'+_name]['columns'].append({'column':_field,'type':_type,
                'default':None,'identity':'','generated':'','not_null':False})


def apply_ingestion_schema(conn):
    """Idempotent non-destructive extension for current and newly bootstrapped DBs."""
    for table, columns in CONTENT_COLUMNS.items():
        for column, kind in columns.items():
            conn.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}')
    for table, columns in INGESTION_NULLABLE.items():
        for column in columns:
            conn.execute(f'ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL, ALTER COLUMN {column} DROP DEFAULT')


MIGRATIONS = ROOT/'src/db/migrations'
PILOT = ROOT/'data/regulations/인재개발규정_시범_V01'
REFERENCE_NAMES = {'skill_tags', 'courses', 'books', 'certifications', 'jobs', 'majors',
                   'course_skill_map', 'book_skill_map', 'cert_skill_map', 'job_skill_map', 'major_skill_map'}
TRANSFER_TABLES = {t for t in CATALOG if t.startswith('hr_regulation_pilot.') or t in {'public.'+n for n in REFERENCE_NAMES}}


def ident(value):
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', value):
        raise ValueError('Unsupported SQL identifier')
    return '"'+value+'"'


def table_sql(table):
    if table not in CATALOG:
        raise ValueError(f'Unknown table: {table}')
    return '.'.join(ident(part) for part in table.split('.'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(directory, relative):
    root = directory.resolve()
    result = (root/relative).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError('Path escapes the selected data directory')
    return result


def table_order(tables):
    pending, ordered = set(tables), []
    while pending:
        ready = sorted(t for t in pending if not any(
            fk['target'] in pending and fk['target'] != t for fk in CATALOG[t]['fks']))
        if not ready:
            raise ValueError('Cross-table foreign-key cycle in transfer set')
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def row_order(table, rows):
    spec = CATALOG[table]
    keys = spec['pk']
    by_key = {tuple(r[k] for k in keys): r for r in rows}
    if len(by_key) != len(rows):
        raise ValueError(f'Duplicate primary keys in {table}')
    self_fks = [f for f in spec['fks'] if f['target'] == table]
    if not self_fks:
        return rows
    dependencies = {}
    for key, row in by_key.items():
        refs = set()
        for fk in self_fks:
            if fk['target_columns'] != keys:
                raise ValueError(f'Unsupported self-reference in {table}')
            parent = tuple(row[c] for c in fk['columns'])
            if all(v is not None for v in parent) and parent in by_key and parent != key:
                refs.add(parent)
        dependencies[key] = refs
    pending, ordered = dict(by_key), []
    while pending:
        ready = [k for k in pending if not (dependencies[k] & pending.keys())]
        if not ready:
            raise ValueError(f'Self-reference cycle in {table}')
        for key in ready:
            ordered.append(pending.pop(key))
    return ordered


def validate_rows(table, rows):
    spec = CATALOG[table]
    expected = {c['column'] for c in spec['columns']}
    for row in rows:
        for field in CONTENT_COLUMNS.get(table, {}):
            row.setdefault(field, None)  # pre-extension transfer bundles
        if set(row) != expected:
            raise ValueError(f'Column set mismatch in {table}')
        for col in spec['columns']:
            value = row[col['column']]
            if col.get('not_null') and value is None:
                raise ValueError(f"NULL in {table}.{col['column']}")
            if col['type'].startswith('vector(') and value is not None:
                vec = json.loads(value) if isinstance(value, str) else value
                dim = int(re.search(r'\d+', col['type'])[0])
                if not isinstance(vec, list) or len(vec) != dim or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vec):
                    raise ValueError(f"Invalid vector in {table}.{col['column']}")
    return row_order(table, rows)


def connect(env_name):
    try:
        import psycopg
        from psycopg.rows import dict_row
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError('Install requirements-db.txt before connecting.') from exc
    load_dotenv(ROOT/'.env', override=False)
    url = os.getenv(env_name)
    if not url:
        available = [name for name in ('SUPABASE_DB_URL','SOURCE_DB_URL','TARGET_DB_URL') if os.getenv(name)]
        hint = (' Existing DB: omit --db-env to use SUPABASE_DB_URL. A new destination requires its own URL; '
                'do not alias TARGET_DB_URL to the source.') if env_name == 'TARGET_DB_URL' else ''
        raise ValueError(f'Missing environment variable: {env_name}. Configured names: {available}.{hint}')
    try:
        return psycopg.connect(url, connect_timeout=10, sslmode=os.getenv('DB_SSLMODE','require'),
                               application_name='growpath_db_deployment', row_factory=dict_row)
    except psycopg.OperationalError:
        raise RuntimeError(f'DB connection failed. Check {env_name}, host access and SSL settings.') from None


def normalize_type(value):
    return value.replace('extensions.', '').replace('public.', '').lower()


def check_schema(conn, allow_missing=False):
    errors = []
    for table, spec in CATALOG.items():
        schema, name = table.split('.')
        cols = conn.execute('''SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS type
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped''', (schema,name)).fetchall()
        found = {c['name']: c['type'] for c in cols}
        for col in spec['columns']:
            field = col['column']
            if field not in found:
                if not allow_missing:
                    errors.append(f'{table}.{field}: missing')
            elif normalize_type(found[field]) != normalize_type(col['type']):
                errors.append(f"{table}.{field}: {found[field]} != {col['type']}")
        if not found or allow_missing:
            continue
        key_rows = conn.execute('''SELECT con.contype, rn.nspname AS target_schema, rc.relname AS target_table,
            ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY AS k(num, ord)
                  JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.num ORDER BY k.ord) AS columns,
            ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY AS k(num, ord)
                  JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=k.num ORDER BY k.ord) AS target_columns
            FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_class rc ON rc.oid=con.confrelid LEFT JOIN pg_namespace rn ON rn.oid=rc.relnamespace
            WHERE n.nspname=%s AND c.relname=%s AND con.contype IN ('p','f')''',(schema,name)).fetchall()
        if not any(k['contype']=='p' and k['columns']==spec['pk'] for k in key_rows):
            errors.append(f'{table}: primary key mismatch')
        for fk in spec['fks']:
            if not any(k['contype']=='f' and k['columns']==fk['columns'] and
                       f"{k['target_schema']}.{k['target_table']}"==fk['target'] and k['target_columns']==fk['target_columns'] for k in key_rows):
                errors.append(f"{table}: missing foreign key {fk['columns']}")
    if errors:
        raise ValueError('Schema mismatch; no automatic destructive conversion:\n'+'\n'.join(errors))


def migrate(conn):
    conn.execute('SELECT pg_advisory_xact_lock(763250130)')
    check_schema(conn, allow_missing=True)
    conn.execute('CREATE SCHEMA IF NOT EXISTS growpath_meta')
    conn.execute('''CREATE TABLE IF NOT EXISTS growpath_meta.schema_migrations (
        version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())''')
    applied = {r['version']: r['sha256'] for r in conn.execute('SELECT version,sha256 FROM growpath_meta.schema_migrations').fetchall()}
    done = []
    for path in sorted(MIGRATIONS.glob('*.sql')):
        version, checksum = path.name, digest(path)
        if version in applied:
            if applied[version] != checksum:
                raise ValueError(f'Applied migration changed: {version}')
            continue
        conn.execute(path.read_text(encoding='utf-8'), prepare=False)
        conn.execute('INSERT INTO growpath_meta.schema_migrations(version,sha256) VALUES (%s,%s)', (version,checksum))
        done.append(version)
    apply_ingestion_schema(conn)
    check_schema(conn)
    # Ask Supabase's PostgREST to refresh after commit. Ordinary PostgreSQL accepts NOTIFY too.
    conn.execute("NOTIFY pgrst, 'reload schema'")
    return {'applied': done, 'tables_verified': len(CATALOG)}


def record_model(conn, table, model):
    conn.execute('''CREATE TABLE IF NOT EXISTS growpath_meta.embedding_provenance (
        table_name text PRIMARY KEY, model_name text NOT NULL, dimensions int NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT now())''')
    old = conn.execute('SELECT model_name FROM growpath_meta.embedding_provenance WHERE table_name=%s',(table,)).fetchone()
    if old and old['model_name'] != model:
        raise ValueError(f'Embedding model mismatch for {table}')
    conn.execute('''INSERT INTO growpath_meta.embedding_provenance(table_name,model_name,dimensions)
        VALUES (%s,%s,1536) ON CONFLICT(table_name) DO NOTHING''',(table,model))


def validate_skill_identity(current, incoming):
    by_id = {r['skill_id']:r for r in current}
    by_name = {r['name']:r['skill_id'] for r in current}
    for row in incoming:
        old = by_id.get(row['skill_id'])
        if (old and any(old[k] != row[k] for k in ('name','category'))) or (row['name'] in by_name and by_name[row['name']] != row['skill_id']):
            raise ValueError('Existing skill IDs/names conflict with the source; migration stopped.')


def upsert_rows(conn, table, rows):
    from psycopg.types.json import Jsonb
    rows = validate_rows(table, rows)
    if not rows:
        return 0
    spec = CATALOG[table]
    fields = [c['column'] for c in spec['columns']]
    if table == 'public.skill_tags':
        validate_skill_identity(conn.execute('SELECT skill_id,name,category FROM public.skill_tags').fetchall(), rows)
    # Different model spaces must never be silently mixed. Provenance is checked by the caller.
    slots = ['%s::'+c['type'] if c['type'].startswith('vector(') else '%s' for c in spec['columns']]
    assignments = ', '.join(f'{ident(c)}=EXCLUDED.{ident(c)}' for c in fields if c not in spec['pk'])
    conflict = 'DO UPDATE SET '+assignments if assignments else 'DO NOTHING'
    statement = f"INSERT INTO {table_sql(table)} ({', '.join(map(ident, fields))}) VALUES ({', '.join(slots)}) ON CONFLICT ({', '.join(map(ident,spec['pk']))}) {conflict}"
    values = []
    for r in rows:
        values.append([Jsonb(r[c['column']]) if c['type']=='jsonb' and r[c['column']] is not None else
                       json.dumps(r[c['column']]) if c['type'].startswith('vector(') and isinstance(r[c['column']], list) else r[c['column']]
                      for c in spec['columns']])
    with conn.cursor() as cur:
        cur.executemany(statement, values)
    return len(rows)


def sync_sequences(conn, tables):
    # Called only for imported tables, after explicit IDs are restored.
    for table in tables:
        for col in CATALOG[table]['columns']:
            seq = conn.execute('SELECT pg_get_serial_sequence(%s,%s) AS seq',(table,col['column'])).fetchone()['seq']
            if seq:
                conn.execute(f'''SELECT setval(%s::regclass,
                    GREATEST(COALESCE((SELECT MAX({ident(col['column'])}) FROM {table_sql(table)}),1),
                    COALESCE(pg_sequence_last_value(%s::regclass),1)), true)''',(seq,seq))


def package_rows(package):
    checksums = json.loads((package/'report/package_checksums.json').read_text(encoding='utf-8'))
    for relative, checksum in checksums.items():
        if digest(inside(package, relative)) != checksum:
            raise ValueError(f'Regulation package checksum mismatch: {relative}')
    tables = {}
    for table in CATALOG:
        if table.startswith('hr_regulation_pilot.'):
            path = package/'data'/f"{table.split('.')[1]}.json"
            tables[table] = validate_rows(table,json.loads(path.read_text(encoding='utf-8')))
    return tables


def load_regulations(conn, package):
    tables = package_rows(package)
    check_schema(conn)
    counts = {t:upsert_rows(conn,t,tables[t]) for t in table_order(tables)}
    return {'loaded': counts, 'review_states_preserved': True, 'embeddings_generated': False}


def export_model(value):
    if value is None or (isinstance(value,str) and not value.strip()):
        return None
    if not isinstance(value,str) or value.strip().upper() in {'ACTUAL_EMBEDDING_MODEL','UNKNOWN','NONE','NULL'}:
        raise ValueError('Use a verified embedding model name, or omit --embedding-model to back up with unknown provenance.')
    return value.strip()


def require_import_provenance(manifest, tables):
    if export_model(manifest.get('embedding_model')):
        return
    populated = [table for table,rows in tables.items() if any(
        row[col['column']] is not None for row in rows for col in CATALOG[table]['columns']
        if col['type'].startswith('vector('))]
    if populated:
        raise ValueError('Backup is valid, but vector model provenance is unknown for: '+', '.join(populated)+
                         '. Verify the original model or plan re-embedding before importing. No DB changes were made.')


def export_data(conn, output, model=None):
    model = export_model(model)
    if output.exists():
        raise ValueError('Export directory already exists; use a new directory.')
    output.mkdir(parents=True)
    # One repeatable-read snapshot for all tables. Export can run before migrations on the source.
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute('SET LOCAL search_path TO public, extensions')
    conn.execute('SET LOCAL row_security = off')  # Error instead of silently exporting RLS-filtered partial data.
    manifest = {'format_version':2,'kind':'database_export','created_at':datetime.now(timezone.utc).isoformat(),
                'embedding_model':model,'embedding_model_status':'operator_supplied' if model else 'unknown',
                'tables':[],'missing_tables':[]}
    for table in table_order(TRANSFER_TABLES):
        exists = conn.execute('SELECT to_regclass(%s) AS id',(table,)).fetchone()['id']
        if not exists:
            manifest['missing_tables'].append(table)
            continue
        spec = CATALOG[table]
        filename = table+'.jsonl'
        path, count = output/filename, 0
        existing_cols = {r['column_name'] for r in conn.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s', tuple(table.split('.'))).fetchall()}
        select_cols = [ident(c['column']) if c['column'] in existing_cols else 'NULL AS '+ident(c['column']) for c in spec['columns']]
        with conn.cursor(name='export_'+table.replace('.','_')) as cur, path.open('w',encoding='utf-8',newline='\n') as stream:
            cur.execute(f"SELECT {', '.join(select_cols)} FROM {table_sql(table)} ORDER BY {', '.join(map(ident,spec['pk']))}")
            for row in cur:
                stream.write(json.dumps(row,ensure_ascii=False,default=str,allow_nan=False)+'\n')
                count += 1
        manifest['tables'].append({'table':table,'file':filename,'rows':count,'sha256':digest(path)})
    # A manifest is written last. An interrupted export is intentionally not importable.
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def read_bundle(directory):
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format_version') not in (1,2) or manifest.get('kind') != 'database_export':
        raise ValueError('Unsupported export manifest')
    model = export_model(manifest.get('embedding_model'))
    if manifest['format_version'] == 1 and not model:
        raise ValueError('Version 1 bundle is missing embedding model provenance')
    if manifest['format_version'] == 2 and manifest.get('embedding_model_status') != ('operator_supplied' if model else 'unknown'):
        raise ValueError('Embedding model status mismatch')
    manifest['embedding_model'] = model
    tables = {}
    for entry in manifest['tables']:
        table = entry['table']
        if table not in TRANSFER_TABLES or table in tables:
            raise ValueError(f'Unsupported or duplicate transfer table: {table}')
        path = inside(directory,entry['file'])
        if digest(path) != entry['sha256']:
            raise ValueError(f'Checksum mismatch: {table}')
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(rows) != entry['rows']:
            raise ValueError(f'Row count mismatch: {table}')
        tables[table] = validate_rows(table,rows)
    return manifest, tables


def import_data(conn, directory):
    manifest, tables = read_bundle(directory)
    require_import_provenance(manifest,tables)
    check_schema(conn)
    conn.execute('SET LOCAL search_path TO public, extensions')
    counts = {}
    for table in table_order(tables):
        vector_cols = [c['column'] for c in CATALOG[table]['columns'] if c['type'].startswith('vector(')]
        if vector_cols and any(r[c] is not None for r in tables[table] for c in vector_cols):
            old = conn.execute('SELECT to_regclass(%s) AS id',('growpath_meta.embedding_provenance',)).fetchone()['id']
            known = conn.execute('SELECT model_name FROM growpath_meta.embedding_provenance WHERE table_name=%s',(table,)).fetchone() if old else None
            populated = conn.execute(f"SELECT EXISTS(SELECT 1 FROM {table_sql(table)} WHERE {ident(vector_cols[0])} IS NOT NULL) AS found").fetchone()['found']
            if populated and not known:
                raise ValueError(f'{table} already has vectors with unknown model provenance. Import into an empty target or record verified model provenance first.')
            record_model(conn,table,manifest['embedding_model'])
        counts[table] = upsert_rows(conn,table,tables[table])
    sync_sequences(conn,tables)
    return {'loaded': counts, 'embedding_model':manifest['embedding_model']}


def verify(conn):
    check_schema(conn)
    conn.execute('SET LOCAL search_path TO public, extensions')
    rows = []
    for table in table_order(CATALOG):
        result = conn.execute(f'SELECT COUNT(*) AS count FROM {table_sql(table)}').fetchone()
        result['table'] = table
        for col in CATALOG[table]['columns']:
            if col['type'].startswith('vector('):
                field = ident(col['column'])
                result.update(conn.execute(f'''SELECT COUNT({field}) AS non_null_vectors,
                    COUNT(*) FILTER (WHERE vector_norm({field})=0) AS zero_vectors,
                    MIN(vector_dims({field})) AS min_vector_dimensions,
                    MAX(vector_dims({field})) AS max_vector_dimensions FROM {table_sql(table)}''').fetchone())
        rows.append(result)
    return {'tables':rows,'schema_and_foreign_keys':'pass','semantic_search_quality':'not_tested'}


def demo_users(conn):
    check_schema(conn)
    sync_sequences(conn,['public.users'])
    conn.execute((ROOT/'src/db/demo_users.sql').read_text(encoding='utf-8'),prepare=False)
    return {'demo_users':[dict(r) for r in conn.execute("SELECT user_id,name,email FROM public.users WHERE email LIKE '%@growpath.example.invalid'").fetchall()]}


def bootstrap(conn, directory, include_demo=False):
    # Validate the bundle before any schema change. One outer transaction covers the complete bootstrap.
    manifest, tables = read_bundle(directory)
    if not tables.get('public.skill_tags'):
        raise ValueError('Bootstrap requires real skill_tags exported from the source DB.')
    regulatory = {t for t in CATALOG if t.startswith('hr_regulation_pilot.')}
    supplied = regulatory & tables.keys()
    if supplied and supplied != regulatory:
        raise ValueError('Partial regulation export; include all nine regulation tables.')
    require_import_provenance(manifest,tables)
    result = {'schema': migrate(conn), 'data': import_data(conn,directory)}
    if not supplied:
        result['regulations'] = load_regulations(conn,PILOT)
    if include_demo:
        result['users'] = demo_users(conn)
    result['verification'] = verify(conn)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='GrowPath DB schema and real-data deployment')
    parser.add_argument('--db-env', default='SUPABASE_DB_URL', help='Environment variable holding PostgreSQL URL')
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('plan', help='Offline schema/data plan; no DB connection')
    commands.add_parser('migrate', help='Apply missing schema migrations atomically')
    commands.add_parser('verify', help='Read-only schema/FK/count checks')
    reg = commands.add_parser('load-regulations', help='Load verified local regulation JSON')
    reg.add_argument('--package',type=Path,default=PILOT)
    exp = commands.add_parser('export-data', help='Read real reference data/vectors from source')
    exp.add_argument('--output',type=Path,required=True)
    exp.add_argument('--embedding-model',nargs='?',default=None,help='Verified source model; omit if unknown (backup allowed, vector import blocked)')
    imp = commands.add_parser('import-data', help='Restore a checked export bundle into destination')
    imp.add_argument('--input',type=Path,required=True)
    commands.add_parser('demo-users', help='Insert only synthetic users/profiles')
    boot = commands.add_parser('bootstrap', help='Schema + real-data bundle + regulation package + verification')
    boot.add_argument('--input',type=Path,required=True)
    boot.add_argument('--demo-users',action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'export-data':
        args.embedding_model = export_model(args.embedding_model)
    if args.command in ('bootstrap','import-data'):
        manifest, tables = read_bundle(args.input)
        require_import_provenance(manifest,tables)
    if args.command == 'plan':
        local = package_rows(PILOT)
        result = {'database_connection':False,'tables':len(CATALOG),
                  'migrations':[p.name for p in sorted(MIGRATIONS.glob('*.sql'))],
                  'transfer_order':table_order(TRANSFER_TABLES),
                  'local_regulation_rows':{k:len(v) for k,v in local.items()},
                  'skill_and_course_data':'Export from existing DB; no synthetic seeds',
                  'storage_files':'Regulation source files remain in the package; remote Storage upload is a separate deployment task.'}
    else:
        with connect(args.db_env) as conn:
            if args.command == 'migrate':
                result = migrate(conn)
            elif args.command == 'load-regulations':
                result = load_regulations(conn,args.package)
            elif args.command == 'export-data':
                result = export_data(conn,args.output,args.embedding_model)
            elif args.command == 'import-data':
                result = import_data(conn,args.input)
            elif args.command == 'demo-users':
                result = demo_users(conn)
            elif args.command == 'bootstrap':
                result = bootstrap(conn,args.input,args.demo_users)
            else:
                conn.execute('SET TRANSACTION READ ONLY')
                result = verify(conn)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
