"""Validate local criterion files and their V10 source links; no DB connection."""
from __future__ import annotations
import argparse, csv, hashlib, json, re
from decimal import Decimal
from pathlib import Path

TABLES=('evaluation_result_schema','evaluation_item_definition','evaluation_schema_source')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def require(ok,message):
    if not ok:raise ValueError(message)

def validate_rows(rows,contract,base):
    checks=[]
    def check(ok,message):require(ok,message);checks.append(message)
    check(set(rows)==set(TABLES),'기준 테이블3개만 포함')
    indexes={}
    for table in TABLES:
        cols=contract['tables'][table]['cols'];names={c['n'] for c in cols}
        pk=next(c['n'] for c in cols if c['pk']);ids=[r.get(pk) for r in rows[table]]
        check(all(isinstance(v,str) and v for v in ids) and len(ids)==len(set(ids)),table+' 기본키 유일성')
        indexes[table]={r[pk]:r for r in rows[table]}
        for row in rows[table]:
            require(set(row)==names,table+' 필드 계약 불일치')
            for col in cols:
                value=row[col['n']];typ=col['t']
                require(not col['nn'] or value is not None,table+'.'+col['n']+' 필수값 누락')
                if value is None:continue
                if typ.startswith('VARCHAR'):
                    limit=int(re.search(r'\d+',typ)[0]);require(isinstance(value,str) and len(value)<=limit,'문자 타입·길이 오류')
                elif typ=='TEXT':require(isinstance(value,str),'TEXT 타입 오류')
                elif typ=='INT':require(isinstance(value,int) and not isinstance(value,bool),'INT 타입 오류')
                elif typ=='JSONB':require(isinstance(value,(dict,list)),'JSONB 구조 오류')
                elif typ=='DATE':require(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value),'DATE 타입 오류')
                elif typ.startswith('NUMERIC'):
                    d=Decimal(str(value));require(d.is_finite() and d.as_tuple().exponent>=-3,'점수 소수점3자리 계약 오류')
                else:raise ValueError('알 수 없는 타입 '+typ)
        checks.append(table+' 전체 컬럼·타입·NULL·점수 정밀도')
    schemas=indexes[TABLES[0]];items=indexes[TABLES[1]]
    insts={r['institution_id'] for r in read(base/'data/institution.json')}
    source_nodes={r['regulation_node_id']:r for r in read(base/'data/regulation_node.json')}
    versions={r['regulation_version_id']:r for r in read(base/'data/regulation_version.json')}
    regs={r['regulation_id']:r for r in read(base/'data/regulation.json')}
    files={r['regulation_file_id']:r for r in read(base/'data/regulation_file.json')}
    check(all(s['institution_id'] in insts for s in schemas.values()),'기관 ID 연결')
    check(all(s['rule_origin']=='published_rule' and s['review_status']=='needs_review' for s in schemas.values()),'공사 규정 출처·사람 검토 대기 유지')
    pairs=[]
    for item in items.values():
        sid=item['evaluation_result_schema_id'];require(sid in schemas,'항목의 양식 없음')
        parent=item['parent_item_id']
        if parent is not None:
            require(parent in items and items[parent]['evaluation_result_schema_id']==sid,'상위 항목의 양식 불일치')
            visited={item['evaluation_item_definition_id']}
            cur=parent
            while cur is not None:
                require(cur not in visited,'상위 항목 순환');visited.add(cur);cur=items[cur]['parent_item_id']
        require(item['skill_id'] is None,'미확인 스킬 대응을 생성함')
        pairs.append((sid,item['item_code']))
    check(len(pairs)==len(set(pairs)),'양식별 항목 코드·부모 양식·순환·미확인 스킬 검사')
    linked_schema=set();linked_item=set()
    for ref in rows[TABLES[2]]:
        sid=ref['evaluation_result_schema_id'];require(sid in schemas,'근거의 양식 없음')
        iid=ref['evaluation_item_definition_id']
        if iid is not None:
            require(iid in items and items[iid]['evaluation_result_schema_id']==sid,'근거 항목의 양식 불일치');linked_item.add(iid)
        nid=ref['regulation_node_id'];require(nid in source_nodes,'원문 근거 없음')
        node=source_nodes[nid];ver=versions[node['regulation_version_id']];reg=regs[ver['regulation_id']];f=files[node['regulation_file_id']]
        require(reg['institution_id']==schemas[sid]['institution_id'],'근거 기관 불일치')
        require(f['source_type'] in ('alio_public','alio_user_supplied') and f['storage_key'].startswith('source/'),'외부 비교 자료 연결 금지')
        require(ref['review_status']=='needs_review','근거 연결을 사람 검토 완료로 오표시함')
        linked_schema.add(sid)
    check(linked_schema==set(schemas) and linked_item==set(items),'모든 기준·항목의 공사 원문 근거 존재')
    for s in schemas.values():
        require(s['calculation_rule']['score_precision']['decimal_places']==3,'점수 정밀도 누락')
        require(s['effective_from'] is None and s['effective_to'] is None,'미확인 항목별 적용일을 확정함')
        require(not s['applicability_condition']['individual_assignments_created'],'개인별 가상 배정 생성 금지')
        actual_versions={source_nodes[r['regulation_node_id']]['regulation_version_id'] for r in rows[TABLES[2]] if r['evaluation_result_schema_id']==s['evaluation_result_schema_id']}
        require(set(s['calculation_rule']['source_version_ids'])==actual_versions,'기준의 근거 개정본 목록 불일치')
    check(True,'점수 정밀도·미확인 적용일·근거 버전·개인 데이터 미생성')
    by_name={s['schema_name']:s for s in schemas.values()}
    expected={'근무성적평정 3급':{'WORK_PERFORMANCE':7.5,'ORG_CONTRIBUTION':7.5,'COMMON_COMPETENCY':15,'LEADER_JOB_COMPETENCY':15,'INTEGRITY':5},
              '근무성적평정 4·5급':{'WORK_PERFORMANCE':5,'ORG_CONTRIBUTION':5,'COMMON_COMPETENCY':15,'JOB_COMPETENCY':20,'INTEGRITY':5}}
    for name,values in expected.items():
        s=by_name[name];sid=s['evaluation_result_schema_id'];actual={i['item_code']:i['score_max'] for i in items.values() if i['evaluation_result_schema_id']==sid and i['aggregation_role']=='leaf'}
        check(actual==values and sum(Decimal(str(v)) for v in actual.values())==50,name+' 제30조 배점·합계50')
        bands=s['calculation_rule']['rule']['grade_bands']
        require([b['grade'] for b in bands]==['수','우','미','양','가'] and [b['lower'] for b in bands]==[45,40,35,30,None],'등급 분류 기준 불일치')
    for grade,hours in [('2급(을)',48),('3급',64),('4급',80),('5급',112)]:
        s=by_name['교육훈련평정 '+grade];rule=s['calculation_rule']['rule'];sid=s['evaluation_result_schema_id']
        require(rule['annual_baseline_hours']==hours,'직급별 기준학습시간 불일치')
        require(rule['annual_recognized_hours_limit']==200 and rule['central_learning_minimum_percent']==40,'별표1 주석 불일치')
        require(rule['variables']['A']['value']==(10 if grade=='2급(을)' else 25),'교육 기준배점 A 불일치')
        require(all(rule['variables'][v]['value'] is None for v in ('B','C','D','E')),'가상 직원 교육 산식 입력을 생성함')
        require(rule['score_hard_cap'] is None and rule['zero_or_missing_C_E']=='not_calculated','미확인 교육 상한·0분모 처리 오류')
        require(next(i for i in items.values() if i['evaluation_result_schema_id']==sid and i['item_code']=='TRAINING_SCORE')['score_max'] is None,'A를 교육점수 하드 상한으로 사용함')
        check(True,'교육 '+grade+' 기준시간·A·미확인 입력·상한 구분')
    rule=by_name['경력평정 3~5급']['calculation_rule']['rule']
    actual=rule['lookup_after_basic_period']['months_1_to_36']
    n=next(n for n in source_nodes.values() if n['regulation_version_id']=='baed2f1b-2de3-5162-82c4-d52cc29e3d5c' and n.get('label')=='별표 6' and n['node_type']=='annex')
    original=list(map(float,re.findall(r'\b(?:3|4|5)\.\d{3}\b',n['body_text'])))
    check(actual==original and len(actual)==36 and [actual[11],actual[23],actual[35]]==[4,4.5,5],'경력 별표6 총점36개·중복합산 방지')
    adjustment=by_name['가감평정 2급(을)·3~5급 참고']['calculation_rule']['rule']
    check(adjustment['worksite']['monthly_rates']=={'가':0.07,'나':0.05,'다':0.03,'라':0.01} and adjustment['penalty']['general_period_years']==3 and adjustment['penalty']['warning_period_years']==1,'근무가점 월별 값·감점 유효기간')
    return checks

def validate(component:Path,base:Path):
    contract=read(component/'schema.json');rows={n:read(component/'data'/f'{n}.json') for n in TABLES}
    checks=validate_rows(rows,contract,base)
    for name in TABLES:
        with (component/'data'/f'{name}.csv').open(encoding='utf-8-sig',newline='') as f: csvrows=list(csv.DictReader(f))
        decoded=[]
        for raw in csvrows:
            row={}
            for col in contract['tables'][name]['cols']:
                v=raw[col['n']];t=col['t']
                row[col['n']]=None if v=='' else json.loads(v) if t=='JSONB' else int(v) if t=='INT' else float(v) if t.startswith('NUMERIC') else v
            decoded.append(row)
        require(decoded==rows[name],'JSON와 CSV 불일치 '+name)
    checks.append('JSON·CSV3쌍 왕복 값 일치')
    manifest=read(component/'manifest.json')
    require(manifest['employee_outcomes_created']==0 and manifest['external_comparison_sources_included']==0,'제외 범위 위반')
    for name,digest in manifest['files'].items():require(hashlib.sha256((component/name).read_bytes()).hexdigest()==digest,'자료 해시 불일치 '+name)
    for source in manifest['source_nodes']:
        require(hashlib.sha256((base/source['source_pdf']).read_bytes()).hexdigest()==source['pdf_sha256'],'원문 PDF 해시 불일치')
    checks.append('데이터·원문 해시·직원 결과 및 외부 비교 자료 제외')
    return {'status':'passed','check_count':len(checks),'checks':checks,'record_counts':{n:len(r) for n,r in rows.items()},
            'human_review_status':'needs_review','actual_db_validation':False,'personal_evaluation_results_created':0}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--regulation-bundle',required=True,type=Path)
    ap.add_argument('--component',type=Path,default=Path(__file__).resolve().parent)
    a=ap.parse_args();result=validate(a.component.resolve(),a.regulation_bundle.resolve())
    (a.component/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
