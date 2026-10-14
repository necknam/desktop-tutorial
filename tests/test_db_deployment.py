"""Dependency-free deployment checks; no DB, API, or synthetic knowledge is created."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.db import deployment as db


class DeploymentTests(unittest.TestCase):
    def test_unknown_vector_backup_readable_but_import_blocked_before_db(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            directory = Path(folder)
            manifest = self.bundle(directory)
            path = directory/'skills.jsonl'
            path.write_text(json.dumps({**self.skill(),'embedding':[0.1]*1536})+'\n',encoding='utf-8')
            manifest.update(format_version=2,embedding_model=None,embedding_model_status='unknown')
            manifest['tables'][0]['sha256'] = db.digest(path)
            (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            _,rows = db.read_bundle(directory)
            self.assertEqual(len(rows['public.skill_tags']),1)
            with patch.object(db,'migrate') as migrate:
                with self.assertRaisesRegex(ValueError,'provenance is unknown'):
                    db.bootstrap(None,directory)
                migrate.assert_not_called()
            with patch.object(db,'connect') as connect:
                with self.assertRaisesRegex(ValueError,'provenance is unknown'):
                    db.main(['--db-env','TARGET_DB_URL','bootstrap','--input',str(directory)])
                connect.assert_not_called()

    def test_unknown_model_without_vectors_can_import(self):
        db.require_import_provenance({'embedding_model':None},{'public.skill_tags':[self.skill()]})

    def test_placeholder_model_rejected_before_connect(self):
        with patch.object(db,'connect') as connect:
            with self.assertRaisesRegex(ValueError,'verified embedding model'):
                db.main(['export-data','--output','unused','--embedding-model','ACTUAL_EMBEDDING_MODEL'])
            connect.assert_not_called()

    def test_export_without_model_or_with_empty_shell_argument(self):
        from contextlib import redirect_stdout
        from io import StringIO
        for suffix in ([],['--embedding-model'],['--embedding-model','']):
            with patch.object(db,'connect') as connect, patch.object(db,'export_data',return_value={}) as export:
                with redirect_stdout(StringIO()):
                    db.main(['export-data','--output','unused']+suffix)
                connect.assert_called_once_with('SUPABASE_DB_URL')
                self.assertIsNone(export.call_args.args[2])

    def skill(self):
        return {'skill_id':1,'name':'SQL 기본 문법 & 조인','category':'데이터베이스','embedding':None}

    def bundle(self, directory):
        path = directory/'skills.jsonl'
        path.write_text(json.dumps(self.skill(),ensure_ascii=False)+'\n',encoding='utf-8')
        manifest = {'format_version':1,'kind':'database_export','embedding_model':'verified-test-model',
                    'tables':[{'table':'public.skill_tags','file':path.name,'rows':1,'sha256':db.digest(path)}]}
        (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        return manifest

    def test_foreign_key_order(self):
        order = db.table_order(db.CATALOG)
        self.assertEqual(len(order),42)
        position = {t:i for i,t in enumerate(order)}
        for table, spec in db.CATALOG.items():
            for fk in spec['fks']:
                self.assertIn(fk['target'],db.CATALOG)
                if fk['target'] != table:
                    self.assertLess(position[fk['target']],position[table])

    def test_real_regulation_integrity_and_foreign_keys(self):
        tables = db.package_rows(db.PILOT)
        self.assertEqual(len(tables['hr_regulation_pilot.regulation_node']),202)
        self.assertEqual(len(tables['hr_regulation_pilot.rag_chunk']),106)
        for table, rows in tables.items():
            for fk in db.CATALOG[table]['fks']:
                targets = {tuple(r[c] for c in fk['target_columns']) for r in tables[fk['target']]}
                for row in rows:
                    key = tuple(row[c] for c in fk['columns'])
                    if all(v is not None for v in key):
                        self.assertIn(key,targets)

    def test_self_parent_before_child(self):
        table = 'hr_regulation_pilot.regulation_node'
        ordered = db.row_order(table,list(reversed(db.package_rows(db.PILOT)[table])))
        seen = set()
        for row in ordered:
            if row['parent_node_id']:
                self.assertIn(row['parent_node_id'],seen)
            seen.add(row['regulation_node_id'])

    def test_parent_cycle_rejected(self):
        rows = [{'regulation_node_id':'a','parent_node_id':'b'}, {'regulation_node_id':'b','parent_node_id':'a'}]
        with self.assertRaisesRegex(ValueError,'cycle'):
            db.row_order('hr_regulation_pilot.regulation_node',rows)

    def test_id_or_name_reassignment_rejected(self):
        original = self.skill()
        for changed in ({**original,'name':'new'}, {**original,'skill_id':9}, {**original,'category':'new'}):
            with self.assertRaisesRegex(ValueError,'conflict'):
                db.validate_skill_identity([original],[changed])
        db.validate_skill_identity([original],[original])

    def test_wrong_vector_dimension_and_nonfinite_rejected(self):
        for vector in ([1.0]*3, [float('nan')]*1536, '[1,2]'):
            with self.assertRaisesRegex(ValueError,'vector'):
                db.validate_rows('public.skill_tags',[{**self.skill(),'embedding':vector}])

    def test_missing_column_and_duplicate_pk_rejected(self):
        bad = self.skill()
        del bad['name']
        with self.assertRaisesRegex(ValueError,'Column set'):
            db.validate_rows('public.skill_tags',[bad])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            db.validate_rows('public.skill_tags',[self.skill(),self.skill()])

    def test_bundle_and_checksum(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            directory = Path(folder)
            self.assertTrue(directory.resolve().is_relative_to(Path(__file__).resolve().parent))
            self.bundle(directory)
            _, rows = db.read_bundle(directory)
            self.assertEqual(rows['public.skill_tags'][0],self.skill())
            (directory/'skills.jsonl').write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Checksum'):
                db.read_bundle(directory)

    def test_manifest_traversal_and_unknown_table_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            directory = Path(folder)
            self.assertTrue(directory.resolve().is_relative_to(Path(__file__).resolve().parent))
            manifest = self.bundle(directory)
            manifest['tables'][0]['file'] = '../outside.jsonl'
            (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'escapes'):
                db.read_bundle(directory)
            manifest['tables'][0]['table'] = 'auth.users'
            (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                db.read_bundle(directory)

    def test_migrations_repeat_without_reexecution_and_checksum_drift_fails(self):
        class Result:
            def __init__(self, rows=()): self.rows=rows
            def fetchall(self): return self.rows
        class Connection:
            def __init__(self): self.applied={}; self.sql=[]
            def execute(self, sql, params=None, **kwargs):
                self.sql.append(sql)
                if sql.startswith('SELECT version,sha256'):
                    return Result([{'version':k,'sha256':v} for k,v in self.applied.items()])
                if sql.startswith('INSERT INTO growpath_meta.schema_migrations'):
                    self.applied[params[0]]=params[1]
                return Result()
        conn = Connection()
        with patch.object(db,'check_schema'):
            first = db.migrate(conn)
            second = db.migrate(conn)
            self.assertEqual(len(first['applied']),4)
            self.assertEqual(second['applied'],[])
            conn.applied[first['applied'][0]] = 'modified'
            with self.assertRaisesRegex(ValueError,'migration changed'):
                db.migrate(conn)

    def test_bootstrap_requires_actual_skills_before_migration(self):
        with patch.object(db,'read_bundle',return_value=({},{})), patch.object(db,'migrate') as migrate:
            with self.assertRaisesRegex(ValueError,'real skill_tags'):
                db.bootstrap(None,Path('.'))
            migrate.assert_not_called()

    def test_bootstrap_rejects_partial_regulation_bundle_before_migration(self):
        tables = {'public.skill_tags':[self.skill()], 'hr_regulation_pilot.institution':[]}
        with patch.object(db,'read_bundle',return_value=({},tables)), patch.object(db,'migrate') as migrate:
            with self.assertRaisesRegex(ValueError,'Partial regulation'):
                db.bootstrap(None,Path('.'))
            migrate.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
