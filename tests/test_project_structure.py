"""Offline checks for the structure-only migration; no application imports."""
import ast
import csv
import json
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTests(unittest.TestCase):
    def test_python_syntax_and_internal_import_targets(self):
        for file in [ROOT / 'app.py', ROOT / 'load_data.py', ROOT / 'reset_skills.py',
                     *ROOT.joinpath('src').rglob('*.py'), *ROOT.joinpath('tests').glob('*.py')]:
            with self.subTest(file=str(file.relative_to(ROOT))):
                tree = ast.parse(file.read_text(encoding='utf-8-sig'))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or not node.module:
                        continue
                    self.assertFalse(node.module.startswith('src.core'))
                    self.assertFalse(node.module.startswith('db.'))
                    if node.module.startswith('src.'):
                        module = ROOT.joinpath(*node.module.split('.'))
                        self.assertTrue(module.with_suffix('.py').exists() or (module/'__init__.py').exists(), node.module)

    def test_config_and_export_root_resolution(self):
        config = ROOT / 'src/common/config_loader.py'
        export = ROOT / 'src/pipelines/export_finetuning_dataset.py'
        self.assertEqual(config.resolve().parent.parent.parent, ROOT)
        self.assertEqual(export.resolve().parent.parent.parent, ROOT)
        for name in ('paths.yaml', 'rules.yaml'):
            self.assertTrue((ROOT / 'config' / name).is_file())
        self.assertTrue((ROOT / 'data/processed').is_dir())
        self.assertTrue((ROOT / 'data/exports').is_dir())

    def test_actual_skill_snapshot_preserved(self):
        with (ROOT/'data/reference/skill_tags.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = [{**r, 'skill_id': int(r['skill_id'])} for r in csv.DictReader(f)]
        tree = ast.parse((ROOT/'reset_skills.py').read_text(encoding='utf-8-sig'))
        declared = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'STANDARD_SKILLS' for t in n.targets))
        self.assertEqual(rows, declared)
        self.assertEqual(len(rows), 36)

    def test_regulation_files_and_source_paths(self):
        package = ROOT/'data/regulations/인재개발규정_시범_V01'
        schema = json.loads((package/'schema.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(len(schema['tables']), 9)
        for table in schema['tables']:
            self.assertTrue((package/'data'/f'{table}.json').is_file())
        files = json.loads((package/'data/regulation_file.json').read_text(encoding='utf-8-sig'))
        for row in files:
            self.assertTrue((package/row['storage_key']).is_file(), row['storage_key'])
        for name in ('01_schema.sql', '02_data.sql'):
            self.assertTrue((package/'sql'/name).is_file())

    def test_legacy_export_entry_dispatches_without_running_export(self):
        # Replace only the export implementation to test command routing without DB/API calls.
        stub = types.ModuleType('src.pipelines.export_finetuning_dataset')
        calls = []
        stub.main = lambda: calls.append('main')
        stub.GrowPathFlawlessExporter = object
        stub.InMemoryVectorStore = object
        previous_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {stub.__name__: stub}):
                runpy.run_path(str(ROOT/'src/processed/export_finetuning_dataset.py'), run_name='__main__')
            self.assertEqual(calls, ['main'])
        finally:
            sys.path[:] = previous_path


if __name__ == '__main__':
    unittest.main(verbosity=2)
