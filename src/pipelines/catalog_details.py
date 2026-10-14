"""Official API detail adapters. No generated descriptions or synthetic records."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
import re
from collections import Counter
from src.common.catalog_eligibility import exclusion_reason

from src.common.catalog_quality import missing_fields, certification_availability


def clean(value):
    if value is None: return None
    return ' '.join(re.sub(r'<[^>]*>', ' ', unescape(str(value))).split()) or None


def _parallel(records, fetch, workers=4):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, records))


def enrich_books(ing, rows):
    endpoint = ing.config['library'].get('detail_url','https://data4library.kr/api/srchDtlList')
    key = ing._key('LIBRARY_AUTH_KEY','LIBRARY_NARU_API_KEY')
    def fetch(row):
        try:
            data = ing._request(endpoint, {'authKey':key,'isbn13':row['fields']['isbn'],'format':'json'})
            detail = data.get('response',{}).get('detail',[])
            book = next((r['book'] for r in detail if isinstance(r.get('book'),dict)
                         and str(r['book'].get('isbn13') or '') == row['fields']['isbn']), None)
            if book is None: raise ValueError('DETAIL_ID_MISMATCH')
            row['fields']['description'] = clean(book.get('description'))
            row['fields']['source_url'] = endpoint
            row['raw'] = {'list_record':row['raw'], 'detail_record':book}
            # Bibliographic existence alone does not establish access to a copy.
            libraries = []
            for region in ing.config['library'].get('availability_regions', ['11', '31', '25']):
                holding = ing._request('https://data4library.kr/api/libSrchByBook',
                    {'authKey':key,'isbn':row['fields']['isbn'],'region':str(region),
                     'pageNo':1,'pageSize':1,'format':'json'})
                for record in holding.get('response',{}).get('libs',[]):
                    lib = record.get('lib',{}) if isinstance(record,dict) else {}
                    if lib.get('libCode') and lib.get('libName'):
                        libraries.append({k:lib.get(k) for k in ('libCode','libName','homepage')})
                if libraries:break
            if libraries:
                row['raw']['availability_evidence'] = {
                    'endpoint':'https://data4library.kr/api/libSrchByBook',
                    'isbn':row['fields']['isbn'],'region':str(region),'libraries':libraries,
                    'checked_at':datetime.now(timezone.utc).isoformat(),
                    'meaning':'library holding confirmed; immediate loan availability not guaranteed'}
        except Exception as exc:
            row['detail_error'] = type(exc).__name__
        return row
    result = _parallel(rows, fetch)
    ing.reports['library_details'] = {'status':'OK', 'requested':len(rows),
        'with_description':sum(bool(r['fields'].get('description')) for r in result),
        'failed':sum('detail_error' in r for r in result)}
    return result


def enrich_jobs(ing, rows):
    endpoint = ing.config['careernet'].get('job_detail_url','https://www.career.go.kr/cnet/front/openapi/job.json')
    key = ing._key('CAREERNET_API_KEY')
    def fetch(row):
        source_id = row['external_key'].split(':',1)[-1]
        # Even a list response supplies an official work description.
        row['fields']['description'] = clean(row['raw'].get('work'))
        row['fields']['source_url'] = ing.config['careernet'].get('jobs_url')
        try:
            data = ing._request(endpoint, {'apiKey':key,'seq':source_id})
            if str(data.get('baseInfo',{}).get('job_cd')) != source_id:
                raise ValueError('DETAIL_ID_MISMATCH')
            works = [clean(r.get('work')) for r in data.get('workList',[])]
            description = '\n'.join(v for v in works if v)
            if description: row['fields']['description'] = description
            row['fields']['source_url'] = endpoint
            row['raw'] = {'list_record':row['raw'],'detail_record':data}
        except Exception as exc:
            row['detail_error'] = type(exc).__name__
        return row
    result = _parallel(rows, fetch)
    ing.reports['job_details'] = {'status':'OK','requested':len(rows),
        'with_description':sum(bool(r['fields'].get('description')) for r in result),
        'failed':sum('detail_error' in r for r in result)}
    return result


def microsoft_certifications(ing):
    cfg = ing.config.get('ms_learn',{})
    endpoint = cfg.get('base_url','https://learn.microsoft.com/api/catalog/')
    data = ing._request(endpoint, {'locale':cfg.get('locale','ko-kr'),
                                 'type':'certifications,mergedCertifications,exams'})
    if any(not isinstance(data.get(k),list) for k in ('certifications','mergedCertifications','exams')):
        raise ValueError('UNEXPECTED_CERTIFICATION_DETAIL_RESPONSE')
    certs = {r['uid']:r for r in data['certifications'] if isinstance(r,dict) and r.get('uid')}
    merged = {r['uid']:r for r in data['mergedCertifications'] if isinstance(r,dict) and r.get('uid')}
    exams = {r['uid']:r for r in data['exams'] if isinstance(r,dict) and r.get('uid')}
    result=[];unavailable=[];excluded={'retired':0,'retiring':0,'availability_needs_review':0,'missing_subjects':0,'missing_exam_detail':0}
    for uid in sorted(set(certs)|set(merged)):
        base=certs.get(uid,{})
        item={**base,**merged.get(uid,{})}
        title=clean(item.get('title'))
        description=clean(item.get('summary') or item.get('subtitle'))
        if not title or not description:continue
        # Do not lose a retirement warning when the merged summary overrides subtitle.
        availability_text=' '.join(clean(r.get(k)) or '' for r in (base,item)
                                   for k in ('summary','subtitle'))
        state=certification_availability({'title':title,'description':availability_text})
        if state!='available':
            excluded[state]+=1;unavailable.append({'uid':uid,'status':state});continue
        refs=base.get('exams') or item.get('exams') or []
        linked=[exams[r] for r in refs if isinstance(r,str) and r in exams]
        exam_states=[certification_availability({'title':e.get('title'),'description':clean(e.get('subtitle') or e.get('summary'))}) for e in linked]
        if any(s!='available' for s in exam_states):
            state=next(s for s in ('retired','retiring','availability_needs_review') if s in exam_states)
            excluded[state]+=1;unavailable.append({'uid':uid,'status':state});continue
        # A merged record supplies assessed skills directly. Multi-exam credentials
        # require every referenced exam before calling the assessment scope complete.
        scope=[]
        for raw in item.get('skills') or []:
            if isinstance(raw,str) and clean(raw):scope.append(clean(raw))
        if not scope:
            if not refs or len(linked)!=len(refs):excluded['missing_exam_detail']+=1;continue
            complete=True
            for exam in linked:
                measured=[clean(v) for v in (exam.get('skills') or []) if isinstance(v,str) and clean(v)]
                if not measured:complete=False;break
                scope.extend(measured)
            if not complete:excluded['missing_subjects']+=1;continue
        subjects=' / '.join(dict.fromkeys(scope))
        if len(subjects)<20:excluded['missing_subjects']+=1;continue
        row=ing._record('certifications','API_MS_CERT',uid,{
            'title':title,'provider':'Microsoft','description':description,
            'test_subjects':subjects,'career_outlook':None,'exam_type':None,'pass_score':None,
            'source_url':item.get('url'),'record_source':'API_MS_CERT'},
            {'certification':base,'merged_certification':merged.get(uid),'exam_records':linked,
             'field_evidence':{'test_subjects':'mergedCertifications.skills or all referenced exams.skills',
                               'description':'summary or subtitle'}},
            match_text=title+' '+description+' '+subjects)
        if row and row['matches']:result.append(row)
    ing.reports['ms_certifications']={'status':'OK','raw_count':len(certs),'merged_count':len(merged),
        'exam_count':len(exams),'eligible_count':len(result),'excluded':excluded,'unavailable':unavailable,
        'availability_basis':'explicit retirement notices checked; catalog presence alone does not prove active availability'}
    return result


def refresh_certification_status(conn):
    counts={}
    for row in conn.execute('SELECT * FROM public.certifications').fetchall():
        availability=row.get('content_status') if row.get('content_status') in ('retired','retiring','availability_needs_review') else certification_availability(row)
        status=availability if availability in ('retired','retiring','availability_needs_review') else ('needs_enrichment' if missing_fields('certifications',row) else 'ready')
        if row.get('content_status')!=status:
            conn.execute('UPDATE public.certifications SET content_status=%s WHERE cert_id=%s',(status,row['cert_id']))
            counts[status]=counts.get(status,0)+1
    return counts


def career_majors(ing, title_pattern):
    endpoint = ing.config['careernet'].get('major_url','https://www.career.go.kr/cnet/openapi/getOpenApi.json')
    common = {'apiKey':ing._key('CAREERNET_API_KEY'),'svcType':'api','contentType':'json','gubun':'univ_list'}
    # Subject lists vary by school. Only the official major's own curriculum is attached.
    inventory, seen, raw_count, query_reports = [], set(), 0, []
    keywords=ing.config['careernet'].get('major_keywords') or ['컴퓨터','소프트웨어','인공지능','데이터','정보보안','정보통신','게임']
    for word in keywords:
        query_seen, count = set(), 0
        for page in range(1, ing.max_pages+1):
            data = ing._request(endpoint, {**common,'svcCode':'MAJOR','thisPage':page,'perPage':100,'searchTitle':word})
            items = data.get('dataSearch',{}).get('content')
            if not isinstance(items,list): raise ValueError('UNEXPECTED_MAJOR_RESPONSE')
            fresh = [r for r in items if str(r.get('majorSeq')) not in query_seen]
            if items and not fresh: raise ValueError('MAJOR_PAGINATION_NOT_ADVANCING')
            count+=len(items);raw_count+=len(items)
            for row in fresh:
                identity=str(row.get('majorSeq'));query_seen.add(identity)
                if identity not in seen and title_pattern.search(row.get('mClass') or ''):
                    inventory.append(row)
                seen.add(identity)
            total=int(items[0].get('totalCount',0)) if items else count
            if not items or count>=total:break
        query_reports.append({'keyword':word,'pages_read':page,'raw_count':count,
                              'reported_total':total,'page_limit_reached':count<total})
    def fetch(item):
        seq = str(item['majorSeq'])
        try:
            data = ing._request(endpoint, {**common,'svcCode':'MAJOR_VIEW','majorSeq':seq})
            content = data.get('dataSearch',{}).get('content',[])
            if len(content)!=1: raise ValueError('MAJOR_DETAIL_MISSING')
            detail = content[0]
            if clean(detail.get('major')) != clean(item.get('mClass')):
                raise ValueError('MAJOR_TITLE_MISMATCH')
            curriculum = '\n'.join(' : '.join(v for v in [clean(r.get('SBJECT_NM')),
                clean(r.get('SBJECT_SUMRY'))] if v) for r in detail.get('main_subject',[]))
            return ing._record('majors','API_CAREERNET_MAJOR',seq, {
                'title':clean(detail.get('major')),'description':clean(detail.get('summary')),
                'curriculum':curriculum or None,'source_url':endpoint},
                {'list_record':item,'detail_record':detail})
        except Exception as exc:
            return {'detail_error':type(exc).__name__,'external_key':seq}
    fetched = _parallel(inventory,fetch)
    failures = [r for r in fetched if r is None or 'detail_error' in r]
    ing.reports['careernet_majors'] = {'status':'OK','queries':query_reports,'raw_count':raw_count,
        'page_limit_reached':any(r['page_limit_reached'] for r in query_reports),
        'detail_requested':len(inventory),'detail_failed':len(failures)}
    return [r for r in fetched if r and 'detail_error' not in r]


def finalize(ing, kind, rows):
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        fields = row['fields']
        fields['source_verified_at'] = now
        if kind == 'courses': fields['source_url'] = fields.get('url')
        elif row['source']=='API_ODCLOUD_KDATA':
            fields['provider'] = fields.get('provider') or '한국데이터산업진흥원'
            fields['source_url'] = 'https://www.data.go.kr/data/15148115/fileData.do'
            row['raw'] = {'exam_records':row['raw'],
                'provider_basis':'공공데이터포털 15148115: 한국데이터산업진흥원 운영 데이터자격검정'}
        elif row['source']=='API_ODCLOUD_UNIVERSITY':
            fields['source_url'] = ing.config['university']['base_url']
        fields['content_status'] = 'needs_enrichment' if missing_fields(kind,fields) else 'ready'
        # Link from actual substantive text as well as title; no default or generated skills.
        body = '\n'.join(str(fields.get(k) or '') for k in
                         ('title','syllabus_summary','description','test_subjects','curriculum'))
        row['matches'] = ing.matcher.match(body)
    allowed, rejected = [], Counter()
    for row in rows:
        reason = exclusion_reason(kind, row['fields'], row['source'], row['raw'], row['fetched_at'])
        if reason:rejected[reason] += 1
        else:allowed.append(row)
    ing.reports[kind+'_availability'] = {'accepted':len(allowed),'excluded':dict(rejected)}
    # Do not insert unknown/unusable candidates even as enrichment/review rows.
    return allowed
