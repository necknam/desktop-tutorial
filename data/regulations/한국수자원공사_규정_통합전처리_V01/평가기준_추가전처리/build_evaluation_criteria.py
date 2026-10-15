"""Build regulation-derived criterion rows only; never connect to a database.

Inputs: the V10 regulation directory and this directory's schema.json contract.
Outputs: three JSON/CSV tables plus provenance. Employee outcomes are deferred.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, uuid
from pathlib import Path

DETAIL='baed2f1b-2de3-5162-82c4-d52cc29e3d5c'
TRAINING='5565f28d-29da-5ebd-bb8a-b0dc705f26d3'
HR='29b9192e-3b2b-5f15-bb39-62a868b8bc3d'
VERSION='K-water 평가기준 V01'
TABLES=('evaluation_result_schema','evaluation_item_definition','evaluation_schema_source')

def read(p:Path): return json.loads(p.read_text(encoding='utf-8'))
def write(p:Path,obj): p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def stable(*parts): return str(uuid.uuid5(uuid.NAMESPACE_URL,'kwater/evaluation/V01/'+'/'.join(parts)))
def digest(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()

class Builder:
    def __init__(self,base:Path,contract:dict):
        self.base=base;self.contract=contract
        self.nodes={n['regulation_node_id']:n for n in read(base/'data/regulation_node.json')}
        self.versions={v['regulation_version_id']:v for v in read(base/'data/regulation_version.json')}
        self.files={f['regulation_file_id']:f for f in read(base/'data/regulation_file.json')}
        self.regs={r['regulation_id']:r for r in read(base/'data/regulation.json')}
        self.inst=read(base/'data/institution.json')[0]['institution_id']
        self.rows={n:[] for n in TABLES};self.source_keys=set();self.codes={};self.schema_codes={}
    def node(self,label,vid=DETAIL,node_type='article'):
        matches=[n for n in self.nodes.values() if n['regulation_version_id']==vid and n.get('label')==label and n['node_type']==node_type]
        assert len(matches)==1,(label,vid,node_type,len(matches))
        return matches[0]
    def ref(self,schema,node,role='basis',item=None,note=''):
        key=(schema,item,node['regulation_node_id'],role)
        if key in self.source_keys:return
        self.source_keys.add(key)
        f=self.files[node['regulation_file_id']]
        assert f['source_type'] in ('alio_public','alio_user_supplied')
        assert f['storage_key'].startswith('source/')
        row={'evaluation_schema_source_id':stable('source',schema,item or 'schema',node['regulation_node_id'],role),
             'evaluation_result_schema_id':schema,'evaluation_item_definition_id':item,
             'regulation_node_id':node['regulation_node_id'],'source_role':role,
             'review_status':'needs_review','source_note':note or (self.regs[self.versions[node['regulation_version_id']]['regulation_id']]['name']+' '+node['label']+' / PDF '+','.join(map(str,node['source_locator'].get('pdf_pages',[])))+'쪽')}
        self.rows[TABLES[2]].append(row)
    def schema(self,code,name,kind,grades,original_scope,notes,status='partial',calc='partial',rule=None,conditional=False):
        sid=stable('schema',code);self.schema_codes[code]=sid
        condition={'employee_group':'일반직','current_job_series':['행정','전산'],'current_grade_candidates':grades,
            'original_rule_grade_scope':original_scope,'scope_status':'conditional_future_superior' if conditional else 'current_demo_candidates',
            'individual_assignments_created':False,'position_is_separate_from_grade':True,
            'version_applicability':'수집된 개정본의 현재 문언 기준. 과거 평가 적용례는 별도 확인'}
        policy={'score_precision':{'decimal_places':3,'method':'truncate_after_third_decimal','applies_to':'evaluation_scores_only'},
                'not_employee_outcomes':True,'rule':rule or {},'unconfirmed_values_must_not_be_zero':True,
                'source_version_ids':[DETAIL],'historical_effective_dates_not_resolved':True}
        row={'evaluation_result_schema_id':sid,'institution_id':self.inst,'schema_name':name,
            'version_label':VERSION,'evaluation_kind':kind,'applicability_condition':condition,
            'applicability_note':notes,'effective_from':None,'effective_to':None,
            'rule_origin':'published_rule','source_status':status,'review_status':'needs_review',
            'calculation_rule':policy,'calculation_status':calc,'source_note':notes+' 일반 시행일은 V10에 있으나 본 기준의 항목별·과거 적용일은 확정하지 않았다.'}
        self.rows[TABLES[0]].append(row)
        self.ref(sid,self.node('제28조의2'),'precision')
        return sid,row
    def item(self,sid,code,name,maximum=None,parent=None,kind='score',role='leaf',unit='점',weight=None,note='',node=None):
        iid=stable('item',sid,code);self.codes[(sid,code)]=iid
        row={'evaluation_item_definition_id':iid,'evaluation_result_schema_id':sid,'parent_item_id':parent,
            'skill_id':None,'item_code':code,'item_name':name,'value_kind':kind,'aggregation_role':role,
            'score_min':None,'score_max':maximum,'weight_percent':weight,'unit':unit,'scale_note':note or None,
            'display_order':sum(r['evaluation_result_schema_id']==sid for r in self.rows[TABLES[1]])+1,'definition_note':note or None}
        self.rows[TABLES[1]].append(row)
        if node is not None:self.ref(sid,node,'form',iid)
        return iid
    def make(self):
        n28=self.node('제28조');n30=self.node('제30조');n35=self.node('제35조');n42=self.node('제42조')
        # Preserve the original grade ranges separately from this project's choices.
        for code,grades,scope,career,training,conditional in [('COMPREHENSIVE_3_5',['3급','4급','5급'],'3~8급',True,25,False),('COMPREHENSIVE_2EUL',['2급(을)'],'2급(을)',False,10,True)]:
            rule={'method':'component_reference','work_points':50,'career_points':5 if career else None,'training_points':training,
                'adjustment_limit':10,'overall_hard_cap':None,'component_sum_is_not_final_score':True,
                'minimum_promotion_tenure_months':{'2급(을)':30,'3급':30,'4급':36,'5급':24},
                'minimum_tenure_exception':'제28조: 승진소요최저연수 미충족자는 근무성적평정만 실시할 수 있음. 자동 제외 또는 강제 실시로 확정하지 않음.'}
            sid,row=self.schema(code,'종합근무평정 '+('3~5급' if career else '2급(을) 참고'),'종합근무평정',grades,scope,'구성별 배점과 가감 10점 한도를 보존. 가감평정을 포함한 총점 상한은 임의 생성하지 않음.',conditional=conditional,rule=rule)
            self.ref(sid,n28);self.ref(sid,self.node('제23조',HR),'exception')
            root=self.item(sid,'TOTAL','종합근무평정 구성',parent=None,kind='group',role='group',maximum=None,node=n28,note='구성 관계. 하위 배점과 상위 집계의 중복 합산 금지.')
            for c,title,p in [('WORK','근무성적평정',50),('CAREER','경력평정',5),('TRAINING','교육훈련평정',training),('ADJUSTMENT','가감평정',10)]:
                if c=='CAREER' and not career:continue
                self.item(sid,c,title,p,parent=root,node=n28,note='원문 구성 배점' if c!='ADJUSTMENT' else '원문 10한도. 가점·감점과 개별 중복 제한을 함께 판단.')

        bands=[{'grade':'수','lower':45,'lower_inclusive':True,'upper':50,'upper_inclusive':True},
               {'grade':'우','lower':40,'lower_inclusive':True,'upper':45,'upper_inclusive':False},
               {'grade':'미','lower':35,'lower_inclusive':True,'upper':40,'upper_inclusive':False},
               {'grade':'양','lower':30,'lower_inclusive':True,'upper':35,'upper_inclusive':False},
               {'grade':'가','lower':None,'upper':30,'upper_inclusive':False}]
        for code,grades,orig,a,c,conditional in [('WORK_3',['3급'],'3급',30,70,False),('WORK_4_5',['4급','5급'],'4급 이하',20,80,False),('WORK_2EUL',['2급(을)'],'2급(을)',40,60,True)]:
            rule={'method':'store_provided_or_future_synthetic_final_score','maximum_points':50,'achievement_percent':a,'competency_percent':c,
                'grade_bands':bands,'raw_rater_calculation_implemented':False,'self_input_does_not_prove_rating_kind':True,
                'supervisor_adjustment':{'formula':'individual_raw + (company_mean_by_unit - rater_group_mean)','stage_weights':[60,40],'single_rater_percent':100,
                    'audit_department_exception':'감사실: 평정단위별 공사 전체 평균점수 2% 가산, 만점 초과 시 만점. 현재 개인 적용 미정.'} if not conditional else None,
                'grade_mapping_requires_confirmed_kind_scale_and_version':True}
            sid,row=self.schema(code,'근무성적평정 '+('3급' if code=='WORK_3' else '4·5급' if code=='WORK_4_5' else '2급(을) 참고'),'근무성적평정',grades,orig,
                '항목 배점·등급 분류 기준은 확인. 원점수 정규화·기관 세부 평가법·개인 적용은 별도 확인.',status='partial',conditional=conditional,rule=rule)
            source=self.node('제29조의7') if conditional else n30
            self.ref(sid,source,'weight');self.ref(sid,self.node('제29조의8'),'classification')
            if not conditional:
                for label,role in [('제29조','basis'),('제29조의2','exception'),('제29조의4','access'),('제29조의5','retention'),('제32조','basis'),('제34조','basis'),('제35조','formula'),('제35조의2','classification')]:self.ref(sid,self.node(label),role)
                rule['retention_years_from_completion']=5
            root=self.item(sid,'TOTAL','근무성적평정 계',50,kind='group',role='group',node=n28,note='최종 제공 점수의 기준 만점. 원점수와 최종 점수를 구분.')
            ach=self.item(sid,'ACHIEVEMENT','업적평정',50*a/100,parent=root,kind='group',role='group',weight=a,node=source,note='배점은 50점×원문 비율로 환산. 하위 항목과 중복 합산 금지.')
            comp=self.item(sid,'COMPETENCY','역량평정',50*c/100,parent=root,kind='group',role='group',weight=c,node=source,note='배점은 50점×원문 비율로 환산. 기관 세부 척도는 미확인.')
            if conditional:continue
            perf=7.5 if code=='WORK_3' else 5
            for ic,label,points,parent,method in [('WORK_PERFORMANCE','근무실적',perf,ach,'상사평정'),('ORG_CONTRIBUTION','조직기여도',perf,ach,'상사평정'),('COMMON_COMPETENCY','공통 역량',15,comp,'다면평정'),('LEADER_JOB_COMPETENCY' if code=='WORK_3' else 'JOB_COMPETENCY','리더·직무 역량' if code=='WORK_3' else '직무 역량',15 if code=='WORK_3' else 20,comp,'다면평정'),('INTEGRITY','청렴도',5,comp,'다면평정')]:
                self.item(sid,ic,label,points,parent=parent,node=n30,note=method+' / 제30조 원문 배점. 이 값은 개인 점수가 아님.')

        annual={'2급(을)':('S1-R02',48,'G-02'),'3급':('S1-R03',64,'G-03'),'4급':('S1-R04',80,'G-04'),'5급':('S1-R05',112,'G-05')}
        for grade,(hours_label,hours,edu_prefix) in annual.items():
            source=self.node(hours_label,TRAINING,'table_row')
            assert f'{hours}시간' in source['body_text']
            course_nodes={k:self.node(edu_prefix+':'+k,TRAINING,'table_row') for k in ('공통역량','리더역량','직무역량')}
            course_text={k:n['body_text'].split('원문 기준: ',1)[1].split('\n관련 원문 주석:',1)[0] for k,n in course_nodes.items()}
            A=10 if grade=='2급(을)' else 25
            rule={'method':'known_formula_with_unconfirmed_personal_and_recognition_inputs','expression':'A*(0.6*(B/C)+0.4*(D/E))',
                'variables':{'A':{'value':A,'unit':'점'},'B':{'value':None,'unit':'인정시간','meaning':'해당 직급 기간 총학습시간'},
                    'C':{'value':None,'unit':'시간','expression':'annual_baseline_hours * eligible_years_in_grade'},
                    'D':{'value':None,'unit':'이수과정 수'},'E':{'value':None,'unit':'대상과정 수','do_not_infer_count_from_displayed_domains':True}},
                'annual_baseline_hours':hours,'annual_baseline_unit':'시간/년','annual_recognized_hours_limit':200,
                'central_learning_minimum_percent':40,'required_course_text':course_text,'required_course_minimum_duration_days':2,
                'course_catalog_ids':None,'recognition_details_status':'institution_internal_details_unavailable',
                'E_target_course_count_status':'not_confirmed','score_hard_cap':None,'zero_or_missing_C_E':'not_calculated',
                'leader_exclusion':'해당 리더 필수교육 실제 미수료 시 제41조 적용 검토. 기록 부재를 미수료로 바꾸지 않음.',
                'career_hire_leader_deemed_completed':'별표2 C-03: 경력직·고용전환 등 해당직급 임명 당시 리더교육 이수 간주. 개인 채용 유형 확인 필요.',
                'leader_deadline':'제7조④: 2월 말까지 승진은 다음해10월31일, 3월 이후 승진은 다다음해10월31일. 국외파견·휴직은 복귀 다음해10월31일, 사전승인 연장 예외.',
                'new_job_course_cumulative_ratio_applies_from_evaluation_year':2025,
                'required_course_promotion_cutoff':{'2급(을)':'2010-12-01','3급':'2010-12-01','4급':'2019-01-01','5급':None}[grade],
                'previous_job_understanding_II_equivalence':'행정/기술 모두 이수한 경우 개정 직무이해Ⅱ를 모두 이수한 것으로 봄. 해당 이력 확인 필요.',
                'job_family_transition_exception':'제41조②의 기존 직급 학습시간·직무교육 인정 조건은 개인 이력 확인 후 적용.',
                'evaluation_period_note':'정기평정 기준일10월31일·기준일 전1년이라는 일반 문구와 교육 B·C·D의 해당직급 기간 집계를 구분. 교육 누적기간을 임의로1년으로 자르지 않음.'}
            sid,row=self.schema('TRAINING_'+grade,'교육훈련평정 '+grade,'교육훈련평정',[grade],'2급(을) 또는 3급 이하; 기준학습시간은 해당 일반직 직급',
                '산식과 직급별 연간 기준시간은 확인. 개인 인정시간·대상 과정 수 E 및 내부 인정절차는 미확인. 기준배점 A를 점수 자동 상한으로 사용하지 않음.',conditional=grade=='2급(을)',rule=rule)
            row['calculation_rule']['source_version_ids']=[DETAIL,TRAINING]
            for label,role in [('제41조','exception'),('제42조','formula'),('제43조','basis')]:self.ref(sid,self.node(label),role)
            self.ref(sid,self.node('제7조',TRAINING),'basis')
            for label in ('S1-C01','S1-C02','S1-C04','C-02','C-03','C-05','C-07','C-08','C-09'):
                self.ref(sid,self.node(label,TRAINING,'annex_note'),'exception' if label.startswith('C-') else 'basis')
            root=self.item(sid,'TRAINING_SCORE','교육훈련평정 점수',maximum=None,kind='group',role='group',node=n42,note=f'기준배점 A={A}점. 산식 적용 대상과 입력 확인 후 계산. 점수 상한 자동 생성 금지. 하위 교육 기준을 점수로 합산하지 않음.')
            self.item(sid,'ANNUAL_BASELINE_HOURS','연간 기준학습시간',parent=root,kind='hours',unit='시간/년',node=source,note=f'원문 {hours}시간/년. 점수 척도 필드 score_max에 시간을 넣지 않음. 값은 calculation_rule.annual_baseline_hours에 저장.')
            for k,node in course_nodes.items():
                self.item(sid,{'공통역량':'COMMON_REQUIRED_EDUCATION','리더역량':'LEADER_REQUIRED_EDUCATION','직무역량':'JOB_REQUIRED_EDUCATION'}[k],k+' 필수교육',parent=root,kind='group',role='group',unit='과정',node=node,note=course_text[k]+' / 실제 과정 ID·이수·대상 수는 추후 확인.')

        lookup=self.node('별표 6',DETAIL,'annex')
        points=re.findall(r'\b(?:3|4|5)\.\d{3}\b',lookup['body_text']);assert len(points)==36
        rule={'method':'published_lookup_with_tenure_eligibility','basic_points':3.5,'excess_points':1.5,'total_maximum':5,
              'minimum_tenure_months':{'3급':30,'4급':36,'5급':24},'excess_period_limit_months':36,
              'incomplete_months':'not_counted','lookup_after_basic_period':{'months_0':3.5,'months_1_to_36':[float(p) for p in points]},
              'lookup_values_are_total_career_scores':True,'career_below_basic_period_method':'not_automatically_inferred',
              'eligible_tenure_exclusions':'제40조: 휴직·강등·정직·직위해제 원칙 제외와 해당 휴직 예외를 함께 판단.',
              'job_family_transition_and_conversion':'제39조④⑤: 고용전환·직군전환 이력 적용 조건 확인 필요.'}
        sid,row=self.schema('CAREER_3_5','경력평정 3~5급','경력평정',['3급','4급','5급'],'3~8급','기본3.5·초과1.5·총5점과 별표6 수치 확인. 실제 재직기간·휴직 예외와 적용은 추후 직원 이력으로 판단.',rule=rule)
        row['calculation_rule']['source_version_ids']=[DETAIL,HR]
        for label,role in [('제38조','basis'),('제39조','formula'),('제40조','exception')]:self.ref(sid,self.node(label),role)
        self.ref(sid,lookup,'lookup_table');self.ref(sid,self.node('제23조',HR),'basis')
        root=self.item(sid,'CAREER_TOTAL','경력평정 계',5,kind='group',role='group',node=self.node('제39조'))
        self.item(sid,'BASIC_CAREER','기본경력평정',3.5,parent=root,node=self.node('제39조'),note='해당직급 임용일부터 승진소요최저연수. 실제 인정 재직기간 확인.')
        self.item(sid,'EXCESS_CAREER','초과경력평정',1.5,parent=root,node=self.node('제39조'),note='기본기간 만료 후3년. 별표6의 값은 기본을 포함한 총점이므로 기본3.5점을 다시 더하지 않음.')

        rule={'method':'conditional_bonus_minus_selected_penalty','adjustment_limit':10,'signed_values_not_employee_outcomes':True,
              'award_rule':'올해의 K-water人 제외한 표창은 유리한1개만 적용. 지식표창 기여도·예산절감 제외 조건 확인.',
              'patent':{'points_per_case':0.5,'group_limit':2,'maximum_contributors':5,'two_person_percent':80,'three_or_more_percent':70,
                  'requires_company_registered_patent':True,'same_case_award_overlap':'patent_bonus_not_recognized'},
              'worksite':{'grade_scope':'3급 이하','monthly_rates':{'가':0.07,'나':0.05,'다':0.03,'라':0.01},'maximum_points':1,
                  'site_category_assignments':None,'safety_officer_min_continuous_months':6,'safety_officer_extra_rate':0.01,
                  'incomplete_months':'not_counted','field_career_multiplier_requirement_months':12,
                  'note':'12개월 조건은 현장근무경력 가산의 문구. 월별 근무가점 전체에 동일하게 덮어쓰지 않음. 현장부서 산업안전 적용례2024년부터.'},
              'qualification':{'selection':'most_favorable_one','current_series_literal_lists':{
                  '행정':['변호사','공인회계사','세무사','감정평가사','공인노무사','AICPA(미국공인회계사)','국제변호사'],
                  '전산':['전자계산기조직응용기사','전자계산기기사'],
                  '공통':['변리사','정보관리기술사','정보처리기사','정수시설운영관리사1~3등급']},
                  'technical_common':['기술사','기능장','건설안전기사','산업안전기사','소방설비기사'],
                  'name_aliases_and_validity_not_assumed':True},
              'penalty':{'selection':'most_unfavorable_one','general_period_years':3,'warning_period_years':1,
                  'reference_date':'승진후보자 명부작성 기준일','current_grade_only':True},
              'personal_applicability':'기록 미제공과 해당 없음 확인을 구분. 가점·감점을 자동0으로 확정하지 않음.'}
        sid,row=self.schema('ADJUSTMENT_2EUL_3_5','가감평정 2급(을)·3~5급 참고','가감평정',['2급(을)','3급','4급','5급'],'2급(을) 및 3~8급, 개별조항의 대상 조건 별도',
            '원문 가점·감점의 크기와 선택·중복 조건을 정리. 개인 포상·자격·현장·징계 적용은 추후 확인. 삭제된 전산가점은 생성하지 않음.',rule=rule)
        self.ref(sid,n28,'basis')
        for label,role in [('제44조','exception'),('제44조의3','exception'),('제44조의6','exception'),('제45조','exception'),('제46조','exception')]:self.ref(sid,self.node(label),role)
        self.ref(sid,self.node('별표 7',DETAIL,'annex'),'lookup_table');self.ref(sid,self.node('별표 8',DETAIL,'annex'),'lookup_table')
        root=self.item(sid,'ADJUSTMENT_TOTAL','가감평정 한도',10,kind='group',role='group',node=n28,note='가감10한도. 하위 항목을 무조건 합산하지 않고 선택·중복·감점 차감 규칙 적용.')
        for code,title,amount in [('KWATER_PERSON_AWARD','올해의 K-water人',0.5),('KNOWLEDGE_TOP_GOLD','지식표창 대상·금상',0.3),('KNOWLEDGE_SILVER','지식표창 은상',0.2),('KNOWLEDGE_BRONZE','지식표창 동상',0.1),('KNOWLEDGE_ENCOURAGEMENT','지식표창 장려상',0.05),('KNOWLEDGE_KING','지식왕',0.3)]:self.item(sid,code,title,amount,parent=root,node=self.node('제44조'),note='가점의 원문 크기. 해당직급 수상·기여도·유리한1개 선택 규칙 확인.')
        self.item(sid,'PATENT_CASE','공사명의 특허권 등록',0.5,parent=root,unit='점/건',node=self.node('제44조의3'),note='0.5점/건, 총2점 범위. 공동수행80%/70%,5명 이하, 동일사안 포상 중복 제외.')
        self.item(sid,'WORKSITE_BONUS','근무가점',1,parent=root,node=self.node('제44조의6'),note='상한1점. 별표8의 월별 기준과 개인 사업장·산업안전 선임 조건 확인. 2급(을)은 이 항목의 현재 대상에 포함하지 않음.')
        for code,title,amount in [('LICENSE_HIGH','자격면허 등 제45조①1호',2),('LICENSE_MIDDLE','자격면허 등 제45조①2호',1),('WATER_OPERATOR_3','정수시설운영관리사3등급',0.5)]:self.item(sid,code,title,amount,parent=root,node=self.node('제45조'),note='직렬별 별표7 적용과 유리한1개 선택. 모든 자격증에 동일 가점을 부여하지 않음.')
        for code,title,amount in [('DEMOTION','강등',3),('SUSPENSION','정직',2),('PAY_REDUCTION','감봉',1),('REPRIMAND','견책',0.5),('NON_PUNISHMENT_WARNING','불문(경고)',0.2),('THREE_WARNINGS','경고3회',0.1),('TWO_SPECIAL_WARNINGS','제66조의2①단서 경고2회',0.03)]:self.item(sid,'PENALTY_'+code,title,amount,parent=root,node=self.node('제46조'),note='양수는 감점 크기이며 합계 시 차감. 해당직급 처분·3년/경고1년 조건 및 불리한1개 선택.')
        for row in self.rows[TABLES[0]]:
            source_ids={r['regulation_node_id'] for r in self.rows[TABLES[2]] if r['evaluation_result_schema_id']==row['evaluation_result_schema_id']}
            row['calculation_rule']['source_version_ids']=sorted({self.nodes[n]['regulation_version_id'] for n in source_ids})
        return self.rows

def export_csv(path:Path,columns:list,rows:list):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=[c['n'] for c in columns]);writer.writeheader()
        for row in rows:
            out={}
            for c in columns:
                value=row[c['n']]
                out[c['n']]='' if value is None else json.dumps(value,ensure_ascii=False,separators=(',',':')) if c['t']=='JSONB' else value
            writer.writerow(out)

def build(base:Path,out:Path):
    contract=read(out/'schema.json');b=Builder(base,contract);data=b.make()
    target=out/'data';target.mkdir(exist_ok=True)
    for name,rows in data.items():
        expected={c['n'] for c in contract['tables'][name]['cols']}
        assert all(set(r)==expected for r in rows),name
        write(target/f'{name}.json',rows);export_csv(target/f'{name}.csv',contract['tables'][name]['cols'],rows)
    used=sorted({r['regulation_node_id'] for r in data[TABLES[2]]})
    catalog=[]
    for nid in used:
        n=b.nodes[nid];v=b.versions[n['regulation_version_id']];f=b.files[n['regulation_file_id']]
        catalog.append({'regulation_node_id':nid,'regulation_version_id':v['regulation_version_id'],'regulation_name':b.regs[v['regulation_id']]['name'],
            'version_label':v['version_label'],'amendment_date':v['amendment_date'],'general_effective_from':v['effective_from'],
            'label':n['label'],'title':n.get('title'),'source_pdf':f['storage_key'],'pdf_pages':n['source_locator'].get('pdf_pages',[]),
            'node_body_sha256':hashlib.sha256(n['body_text'].encode('utf-8')).hexdigest(),'pdf_sha256':digest(base/f['storage_key'])})
    write(out/'manifest.json',{'delivery_component':'regulation_evaluation_criteria_V01','base_delivery_version':'V10',
        'base_delivery_sha256':'c1694906e53f406a74e44c882714fcf8f99a72cc4566f61c9e618ce296ae2e56',
        'record_counts':{n:len(r) for n,r in data.items()},'schema_codes':b.schema_codes,
        'source_nodes':catalog,'human_review_status':'needs_review','employee_outcomes_created':0,
        'external_comparison_sources_included':0,'actual_db_operations':0,
        'scope_note':'현재 일반직 행정·전산3~5급,2급(을)상급자 후보는참고. 기관 내부 인정기준·개인 이력은 미확인으로 보존.',
        'files':{str(p.relative_to(out)).replace('\\','/'):digest(p) for p in sorted(target.iterdir())}})
    print(json.dumps({'record_counts':{n:len(r) for n,r in data.items()},'source_nodes':len(catalog),'output':str(out)},ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--regulation-bundle',type=Path,required=True);ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parent)
    a=ap.parse_args();build(a.regulation_bundle.resolve(),a.output.resolve())
