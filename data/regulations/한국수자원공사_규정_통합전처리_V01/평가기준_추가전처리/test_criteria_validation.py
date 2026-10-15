"""Offline failure-case checks. All changes below are in-memory copies only."""
import argparse, json, sys, unittest
from copy import deepcopy
from pathlib import Path
from validate_evaluation_criteria import validate_rows

COMPONENT=Path(__file__).resolve().parent
BASE=None
CONTRACT=json.loads((COMPONENT/'schema.json').read_text(encoding='utf-8'))
ROWS={n:json.loads((COMPONENT/'data'/f'{n}.json').read_text(encoding='utf-8')) for n in CONTRACT['tables']}
def work_item(rows):return next(r for r in rows['evaluation_item_definition'] if r['item_code']=='WORK_PERFORMANCE')

class CriterionValidationTests(unittest.TestCase):
    def rejected(self,rows):
        with self.assertRaises(ValueError):validate_rows(rows,CONTRACT,BASE)
    def test_valid_current_criteria(self):
        self.assertTrue(validate_rows(ROWS,CONTRACT,BASE))
    def test_incorrect_grade_allocation(self):
        rows=deepcopy(ROWS);work_item(rows)['score_max']=7.6;self.rejected(rows)
    def test_parent_from_another_schema(self):
        rows=deepcopy(ROWS);item=work_item(rows)
        other=next(r for r in rows['evaluation_item_definition'] if r['evaluation_result_schema_id']!=item['evaluation_result_schema_id'])
        item['parent_item_id']=other['evaluation_item_definition_id'];self.rejected(rows)
    def test_missing_regulation_source(self):
        rows=deepcopy(ROWS);rows['evaluation_schema_source'][0]['regulation_node_id']='not-a-regulation-node';self.rejected(rows)
    def test_score_precision_exceeds_three_places(self):
        rows=deepcopy(ROWS);work_item(rows)['score_max']=7.5001;self.rejected(rows)
    def test_fabricated_required_course_count(self):
        rows=deepcopy(ROWS);s=next(r for r in rows['evaluation_result_schema'] if r['schema_name']=='교육훈련평정 4급')
        s['calculation_rule']['rule']['variables']['E']['value']=4;self.rejected(rows)
    def test_false_human_review_completion(self):
        rows=deepcopy(ROWS);rows['evaluation_result_schema'][0]['review_status']='reviewed';self.rejected(rows)
    def test_employee_results_are_not_part_of_criteria(self):
        rows=deepcopy(ROWS);rows['employee_evaluation_result']=[];self.rejected(rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--regulation-bundle',required=True,type=Path)
    a=p.parse_args();BASE=a.regulation_bundle.resolve();unittest.main(argv=[sys.argv[0]],verbosity=2)
