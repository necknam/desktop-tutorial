"""Fetch official APIs first, upsert atomically, map by evidence, then refresh vectors."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import unescape
import hashlib
import json
import logging
import os
import re
import time
import tempfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from src.common.config_loader import PROJECT_ROOT, get_paths_config
from src.common.skill_matching import SkillMatcher
from src.db import deployment
from src.db import resource_vectors as vector_index
from src.pipelines import catalog_details
from src.common.catalog_quality import missing_fields
from src.common.catalog_eligibility import exclusion_reason

KINDS = ('courses', 'books', 'certifications', 'jobs', 'majors')
LIMITS = {'courses': 120, 'books': 60, 'certifications': 30, 'jobs': 30, 'majors': 60}
TABLES = {'courses': ('course_id', 'course_skill_map'), 'books': ('isbn', 'book_skill_map'),
          'certifications': ('cert_id', 'cert_skill_map'), 'jobs': ('job_id', 'job_skill_map'),
          'majors': ('major_id', 'major_skill_map')}
IT_TITLE = re.compile(r'게임|컴퓨터|소프트웨어|전산|데이터|정보통신|정보보안|정보보호|인공지능|네트워크|프로그래머|정보시스템|(?<![A-Za-z])AI(?![A-Za-z])', re.I)
AUDIT_DDL = '''
CREATE SCHEMA IF NOT EXISTS growpath_meta;
CREATE TABLE IF NOT EXISTS growpath_meta.catalog_api_sources (
    resource_type TEXT NOT NULL,
    source TEXT NOT NULL,
    external_key TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    mapping_evidence JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (resource_type,source,external_key)
);
ALTER TABLE growpath_meta.catalog_api_sources ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON growpath_meta.catalog_api_sources FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON growpath_meta.catalog_api_sources FROM anon;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
  REVOKE ALL ON growpath_meta.catalog_api_sources FROM authenticated;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
  GRANT USAGE ON SCHEMA growpath_meta TO service_role;
  GRANT SELECT,INSERT,UPDATE,DELETE ON growpath_meta.catalog_api_sources TO service_role;
 END IF;
END $$;
'''


class SourceError(RuntimeError):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = details



def text(value, limit=None):
    if value is None:
        return None
    value = re.sub(r'<[^>]*>', ' ', unescape(str(value)))
    value = ' '.join(value.split())
    return (value[:limit] if limit else value) or None


def number(value):
    try:
        if isinstance(value, bool) or value is None: return None
        parsed = int(str(value).replace(',', ''))
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def actual_url(value, base=None):
    if not isinstance(value, str) or not value.strip(): return None
    value = urljoin(base, value.strip()) if base else value.strip()
    try:
        p = urlsplit(value)
        if p.scheme in ('http', 'https') and p.hostname and not p.username and not p.password and len(value) <= 255:
            return value
    except ValueError:
        pass
    return None


class RealDataIngestor:
    def __init__(self, *, client=None, config=None):
        load_dotenv(PROJECT_ROOT / '.env', override=False)
        self.config = config or get_paths_config()['api']
        self.client = client
        self.reports = {}
        self.matcher = None
        self.max_pages = self.config.get('ingestion', {}).get('max_pages', 50)
        self.selection_reports = {}
        self._library_blocked = None
        for name in ('httpx', 'httpx2'):
            logging.getLogger(name).setLevel(logging.WARNING)

    def _library_cache_path(self, endpoint, params):
        # Include the credential in a one-way fingerprint, never in the file content/name.
        identity = json.dumps([endpoint,params],sort_keys=True,ensure_ascii=False)
        digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        return PROJECT_ROOT / 'data/cache/library_api' / (digest + '.json')

    def _library_cache_read(self, path):
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            age = time.time() - float(saved['saved_at'])
            payload = saved['payload']
            body = payload.get('response',{})
            if (0 <= age < 86400 and isinstance(body,dict) and not body.get('error')
                    and (isinstance(body.get('docs'),list) or isinstance(body.get('detail'),list) or isinstance(body.get('libs'),list))):
                return payload
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            pass
        return None

    def _library_cache_write(self, path, payload, params):
        body = payload.get('response',{})
        if (not isinstance(body,dict) or body.get('error')
                or not (isinstance(body.get('docs'),list) or isinstance(body.get('detail'),list) or isinstance(body.get('libs'),list))):
            return
        secrets = [str(v) for k,v in params.items() if 'key' in k.lower() and v]
        def redact(value):
            if isinstance(value,dict):
                return {k:redact(v) for k,v in value.items() if 'key' not in k.lower() and 'token' not in k.lower()}
            if isinstance(value,list):return [redact(v) for v in value]
            if isinstance(value,str):
                for secret in secrets:value=value.replace(secret,'[REDACTED]')
            return value
        temporary = None
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                             prefix='cache-',suffix='.tmp',delete=False) as file:
                temporary = Path(file.name)
                json.dump({'saved_at':time.time(),'payload':redact(payload)},file,ensure_ascii=False)
            os.replace(temporary,path)
        except OSError:
            self.reports['library_cache']={'status':'FAILED','reason':'CACHE_WRITE_FAILED'}
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def _request(self, url, params):
        # Only retry idempotent GET requests; never print credential-bearing exceptions.
        url = url.replace('http://', 'https://', 1)
        parsed = urlsplit(url)
        if parsed.hostname not in {'learn.microsoft.com', 'apis.data.go.kr', 'api.odcloud.kr',
                                   'data4library.kr', 'www.data4library.kr', 'www.career.go.kr'}:
            raise SourceError('UNSUPPORTED_API_HOST')
        endpoint = parsed.scheme + '://' + parsed.hostname + parsed.path
        library = parsed.hostname in {'data4library.kr','www.data4library.kr'}
        cache_path = self._library_cache_path(endpoint,params) if library else None
        if library:
            cached = self._library_cache_read(cache_path)
            if cached is not None:return cached
            if self._library_blocked is not None:
                reason,details = self._library_blocked
                raise SourceError(reason,**details)
        transient_codes = {408,429,500,502,503,504}
        for attempt in range(1,4):
            delay = 2 ** (attempt - 1)
            retryable = False
            try:
                if self.client is not None:
                    response = self.client.get(url, params=params)
                else:
                    with httpx.Client(timeout=httpx.Timeout(35,connect=10),follow_redirects=False) as client:
                        response = client.get(url, params=params)
                if response.status_code != 200:
                    reason = f'HTTP_{response.status_code}'
                    retryable = response.status_code in transient_codes
                    try:
                        retry_after = float(getattr(response,'headers',{}).get('Retry-After','0'))
                        delay = max(delay,min(30,max(0,retry_after)))
                    except (TypeError,ValueError):
                        pass
                else:
                    try:
                        payload = response.json()
                    except ValueError:
                        raise SourceError('INVALID_JSON_RESPONSE',endpoint=endpoint,attempts=attempt) from None
                    if not isinstance(payload,dict):
                        raise SourceError('INVALID_JSON_OBJECT',endpoint=endpoint,attempts=attempt)
                    if library:
                        body = payload.get('response',{})
                        error = body.get('error') if isinstance(body,dict) else None
                        if error:
                            message = str(error).lower()
                            category = 'unspecified'
                            reason = 'API_RESULT_ERROR'
                            if '500' in message and 'ip' in message and '등록' in message:
                                reason = 'LIBRARY_IP_REGISTRATION_REQUIRED'
                                category = 'daily_quota_requires_registered_ip'
                            elif any(s in message for s in ('초과','한도','소진','quota','rate limit','too many')):
                                category = 'quota_or_rate_limit'
                            elif any(s in message for s in ('인증','auth','permission','키')):
                                category = 'authentication_or_permission'
                            details = {'category':category,'endpoint':endpoint,'attempts':attempt}
                            if category != 'unspecified':
                                self._library_blocked = (reason,details)
                                self.reports['library_access']={'status':'FAILED','reason':reason,**details}
                            raise SourceError(reason,**details)
                        self._library_cache_write(cache_path,payload,params)
                    return payload
            except SourceError:
                raise
            except httpx.TransportError as exc:
                reason = type(exc).__name__
                retryable = True
            except Exception as exc:
                raise SourceError(type(exc).__name__,endpoint=endpoint,attempts=attempt) from None
            if not retryable or attempt == 3:
                raise SourceError(reason,endpoint=endpoint,attempts=attempt) from None
            logging.getLogger('LOAD_DATA').warning('%s: %s, retry %s/2 in %.0fs',
                parsed.hostname,reason,attempt,delay)
            time.sleep(delay)

    @staticmethod
    def _key(*names):
        value = next((os.getenv(n, '').strip() for n in names if os.getenv(n, '').strip()), '')
        if not value: raise SourceError('MISSING_KEY:' + '/'.join(names))
        return unquote(value)

    def _record(self, kind, source, external_key, fields, raw, match_text=None):
        if not fields.get('title') or not external_key: return None
        # Do not truncate identifiers/titles and accidentally merge unrelated entities.
        max_title = 200 if kind in ('courses', 'books') else 100
        if len(fields['title']) > max_title: return None
        for field, limit in {'provider':50, 'author':100, 'publisher':100,
                             'url':255, 'org_name':100, 'category_name':50}.items():
            if fields.get(field) is not None and len(str(fields[field])) > limit:
                fields[field] = None
        content = match_text or ' '.join(str(fields.get(k) or '') for k in
            ('title', 'syllabus_summary', 'test_subjects', 'curriculum', 'ncs_units'))
        matches = self.matcher.match(content)
        return {'kind': kind, 'source': source, 'external_key': str(external_key),
                'fields': fields, 'raw': raw, 'matches': matches,
                'fetched_at': datetime.now(timezone.utc).isoformat()}

    def _pages(self, source, url, params, *, page_key='page', size_key='perPage', size=100, list_key='data'):
        result, signatures = [], set()
        for page in range(1, self.max_pages + 1):
            payload = self._request(url, {**params, page_key:page, size_key:size})
            if source == 'kmooc' and str(payload.get('resultCode', '00')) not in ('00', '0', '200'):
                raise SourceError('API_RESULT_ERROR')
            items = payload.get(list_key)
            if source.startswith('careernet'): items = payload.get('jobs', payload.get('dataSearch', {}).get('content'))
            if not isinstance(items, list): raise SourceError('UNEXPECTED_RESPONSE_SCHEMA')
            signature = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
            if items and signature in signatures: raise SourceError('PAGINATION_NOT_ADVANCING')
            signatures.add(signature)
            result.extend(items)
            self.reports[source] = {'status':'OK', 'pages_read':page, 'raw_count':len(result),
                                    'page_limit_reached':page == self.max_pages,
                                    'reported_total': number(payload.get('totalCount')),
                                    'requested_page_size':size,
                                    'reported_page_size':payload.get('header',{}).get('size',payload.get('pageSize'))}
            total = number(payload.get('totalCount'))
            if source.startswith('careernet') and items:
                total = number(payload.get('count',items[0].get('totalCount')))
                self.reports[source]['reported_total'] = total
            if not items or (total is not None and len(result) >= total):
                self.reports[source]['page_limit_reached'] = False
                break
        return result

    def fetch_courses_from_api(self, target_count=120):
        rows = []
        try:
            items = self._pages('kmooc', self.config['kmooc']['base_url'],
                {'serviceKey':self._key('KMOOC_SERVICE_KEY','DATA_GO_KR_API_KEY'),'_type':'json'},
                size_key='size', size=self.config['kmooc'].get('page_size',50), list_key='items')
            for item in items:
                key = text(item.get('id'))
                if not key or len(key) > 44: continue
                row = self._record('courses','API_KMOOC',key, {
                    'course_id':'KMOOC_'+key, 'title':text(item.get('name')),
                    'org_name':text(item.get('org_name')), 'url':actual_url(item.get('url')),
                    'category_name':None, 'is_matchup':None, 'cost':None, 'duration_weeks':None,
                    'recruitment_status':None, 'syllabus_summary':None,
                    'platform':'K-MOOC', 'difficulty':None, 'record_source':'API_KMOOC'}, item)
                if row: rows.append(row)
        except SourceError as exc:
            self.reports['kmooc'] = {'status':'FAILED','reason':str(exc)}
        try:
            ms = self.config.get('ms_learn', {})
            payload = self._request(ms.get('base_url', 'https://learn.microsoft.com/api/catalog/'), {'locale':ms.get('locale','ko-kr'),'type':'modules'})
            modules = payload.get('modules')
            if not isinstance(modules,list): raise SourceError('UNEXPECTED_RESPONSE_SCHEMA')
            self.reports['ms_learn'] = {'status':'OK','raw_count':len(modules)}
            for item in modules:
                uid = text(item.get('uid'))
                if not uid: continue
                levels = item.get('levels', [])
                difficulty = levels[0].upper() if len(levels)==1 and levels[0] in ('beginner','intermediate','advanced') else None
                row = self._record('courses','API_MS_LEARN',uid, {
                    'course_id':'MS_'+hashlib.md5(uid.encode()).hexdigest()[:12],
                    'title':text(item.get('title')), 'org_name':'Microsoft Learn',
                    'url':actual_url(item.get('url'),'https://learn.microsoft.com'),
                    'category_name':None,'is_matchup':None,
                    # Official Learn FAQ confirms module training is free; cloud usage is separate.
                    'cost':0, 'duration_weeks':None,'recruitment_status':None,
                    'syllabus_summary':text(item.get('summary'),1500), 'platform':'MS Learn',
                    'difficulty':difficulty,'record_source':'API_MS_LEARN'}, item)
                if row: rows.append(row)
        except SourceError as exc:
            self.reports['ms_learn'] = {'status':'FAILED','reason':str(exc)}
        return self._select('courses',rows,target_count)

    def _select(self, kind, rows, limit, *, include_unmatched=False):
        eligible = [r for r in rows if include_unmatched or r.get('matches')]
        selected = self.matcher.select(eligible, limit, include_unmatched=include_unmatched)
        self.selection_reports[kind] = {'normalized_count':len(rows), 'eligible_count':len(eligible),
            'selected_count':len(selected), 'limit':limit, 'limit_reached':len(eligible)>len(selected)}
        return selected

    def fetch_books_from_api(self, target_count=300):
        key = self._key('LIBRARY_AUTH_KEY','LIBRARY_NARU_API_KEY')
        cfg = self.config['library']
        base = cfg['base_url']
        if base.endswith('loanItemSrch'):
            base = urljoin(base, 'srchBooks')  # compatibility for pre-patch YAML
        keywords = self.config.get('ingestion',{}).get('book_keywords') or [
            '컴퓨터 비전','자연어 처리','생성형 AI','머신러닝','데이터베이스','파이썬','쿠버네티스','소프트웨어','정보보안']
        size = cfg.get('page_size',50)
        page_limit = min(self.max_pages, cfg.get('max_pages', self.max_pages))
        rows, seen, queries = [], set(), []
        for word in keywords:
            count, signatures = 0, set()
            for page in range(1, page_limit+1):
                try:
                    response = self._request(base, {'authKey':key,'title':word,'pageNo':page,
                        'pageSize':size,'format':'json'}).get('response',{})
                    if not isinstance(response,dict):raise SourceError('UNEXPECTED_RESPONSE_SCHEMA')
                    if 'error' in response:
                        # Classify errors without echoing API text that may contain a key.
                        message = str(response['error']).lower()
                        category = 'unspecified'
                        if any(s in message for s in ('초과','한도','quota','rate limit','too many')):
                            category = 'quota_or_rate_limit'
                        elif any(s in message for s in ('인증','auth','permission','키')):
                            category = 'authentication_or_permission'
                        raise SourceError('API_RESULT_ERROR',category=category)
                    docs = response.get('docs')
                    if not isinstance(docs,list): raise SourceError('UNEXPECTED_RESPONSE_SCHEMA')
                    signature=hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest()
                    if docs and signature in signatures:raise SourceError('PAGINATION_NOT_ADVANCING')
                except SourceError as exc:
                    exc.details.update(keyword=word,page=page)
                    self.reports['library']={'status':'FAILED','reason':str(exc),**exc.details,
                        'completed_queries':queries,'discarded_candidate_count':len(rows)}
                    raise
                signatures.add(signature);count+=len(docs)
                for wrapper in docs:
                    item=wrapper.get('doc',{})
                    isbn=text(item.get('isbn13') or item.get('isbn'))
                    if not isbn or not re.fullmatch(r'[0-9Xx-]{10,20}',isbn) or isbn in seen:continue
                    seen.add(isbn)
                    row=self._record('books','API_DATA4LIBRARY',isbn, {
                        'isbn':isbn,'title':text(item.get('bookname')),'author':text(item.get('authors')),
                        'publisher':text(item.get('publisher')),'loan_count':number(item.get('loan_count')),
                        'kdc_code':text(item.get('class_no'),10),'record_source':'API_DATA4LIBRARY'},item,
                        match_text=text(item.get('bookname')))
                    if row:rows.append(row)
                total=number(response.get('numFound'))
                complete=not docs or (total is not None and count>=total)
                if complete:break
            queries.append({'keyword':word,'pages_read':page,'raw_count':count,
                'reported_total':total,'page_limit_reached':not complete})
        self.reports['library']={'status':'OK','raw_count':sum(q['raw_count'] for q in queries),
            'queries':queries,'endpoint':'srchBooks','title_search':True,
            'page_limit_reached':any(q['page_limit_reached'] for q in queries),
            'unused_legacy_settings':['kdc_code'] if cfg.get('kdc_code') else []}
        detailed = catalog_details.enrich_books(self,self._select('books',rows,target_count))
        if self._library_blocked is not None:
            reason,details = self._library_blocked
            raise SourceError(reason,**details)
        return detailed

    def fetch_jobs_from_api(self, target_count=30):
        configured=self.config['careernet']['base_url']
        endpoint=self.config['careernet'].get('jobs_url') or (
            'https://www.career.go.kr/cnet/front/openapi/jobs.json'
            if configured.endswith('getOpenApi.json') else configured)
        items, seen = [], set()
        queries = self.config['careernet'].get('job_queries') or [
            {'searchAptdCodes':'104740'},{'searchThemeCode':'102420'},{'searchThemeCode':'102421'}]
        for i, query in enumerate(queries):
            fetched=self._pages('careernet_'+str(i),endpoint,
                {'apiKey':self._key('CAREERNET_API_KEY'),**query},
                page_key='pageIndex',size_key='pageSize',size=10)
            for item in fetched:
                if item.get('job_cd') not in seen:
                    seen.add(item.get('job_cd'));items.append(item)
        rows=[]
        for item in items:
            title=text(item.get('job_nm') or item.get('job'))
            if not title: continue
            key='jobs:'+str(item['job_cd']) if item.get('job_cd') is not None else None
            row=self._record('jobs','API_CAREERNET',key, {'title':title,
                # CareerNet summaries are not NCS competency units. Keep them in the raw audit only.
                'ncs_units':None,'record_source':'API_CAREERNET'},item,
                match_text=title+' '+(text(item.get('work')) or ''))
            if row:rows.append(row)
        return catalog_details.enrich_jobs(self,self._select('jobs',rows,target_count,include_unmatched=True))

    def fetch_certifications_from_api(self,target_count=30):
        items=self._pages('kdata',self.config['kdata']['base_url'],{'serviceKey':self._key('DATA_GO_KR_API_KEY')})
        groups={}
        for item in items:
            title=next((text(v) for k,v in item.items() if re.sub(r'[\sㆍ_·/()（）]','',k) in
                        ('자격증명','종목명','자격명','자격종목') and text(v)),None)
            if title:groups.setdefault(title,[]).append(item)
        rows=[]
        for title,variants in groups.items():
            subjects=sorted({text(v) for item in variants for k,v in item.items() if k.startswith('과목명') and text(v)})
            scores={number(i.get('합격점수')) for i in variants}
            types=sorted({text(i.get('필기실기구분')) for i in variants if text(i.get('필기실기구분'))})
            providers={text(i.get('시행기관') or i.get('발급기관')) for i in variants}
            row=self._record('certifications','API_ODCLOUD_KDATA',title, {
                'title':title,'provider':next(iter(providers)) if len(providers)==1 else None,
                'test_subjects':' / '.join(subjects) or None,'career_outlook':None,
                'exam_type':' / '.join(types) or None,
                'pass_score':next(iter(scores)) if len(scores)==1 else None,
                'record_source':'API_ODCLOUD_KDATA'},variants)
            if row:rows.append(row)
        try:
            rows.extend(catalog_details.microsoft_certifications(self))
        except Exception as exc:
            self.reports['ms_certifications']={'status':'FAILED','reason':type(exc).__name__}
        return self._select('certifications',rows,target_count,include_unmatched=True)

    def fetch_majors_from_api(self,target_count=60):
        items=self._pages('university',self.config['university']['base_url'],
                          {'serviceKey':self._key('DATA_GO_KR_API_KEY')},size=1000)
        groups={}
        for item in items:
            title=text(item.get('학부_과(전공)명') or item.get('학과명') or item.get('전공명'))
            if title and IT_TITLE.search(title):groups.setdefault(title,[]).append(item)
        rows=[]
        for title,variants in groups.items():
            # Major classification is not a curriculum. Preserve the raw source separately.
            row=self._record('majors','API_ODCLOUD_UNIVERSITY',title,{'title':title,'curriculum':None},variants,match_text=title)
            if row:rows.append(row)
        try:
            detailed=catalog_details.career_majors(self,IT_TITLE)
            # Prefer detailed canonical majors over same-title institution inventory rows.
            titles={r['fields']['title'] for r in detailed}
            rows=detailed+[r for r in rows if r['fields']['title'] not in titles]
        except Exception as exc:
            self.reports['careernet_majors']={'status':'FAILED','reason':type(exc).__name__}
        return self._select('majors',rows,target_count,include_unmatched=True)

    def collect(self,skills,*,max_pages=None,limits=None):
        if not skills:raise ValueError('기존 skill_tags 원본을 먼저 적재하세요. 스킬을 초기화하지 않습니다.')
        self.matcher=SkillMatcher(skills)
        settings=self.config.get('ingestion',{})
        self.max_pages=max_pages if max_pages is not None else settings.get('max_pages',50)
        if type(self.max_pages) is not int or not 1<=self.max_pages<=1000:
            raise ValueError('max_pages must be between 1 and 1000')
        self.reports={};self.selection_reports={};self._library_blocked=None
        limits=limits or {**LIMITS,**settings.get('limits',{})}
        if any(type(limits.get(k)) is not int or limits[k]<1 for k in KINDS):
            raise ValueError('All catalog limits must be positive integers')
        rows={};failures={}
        for kind in KINDS:
            method=getattr(self,'fetch_'+kind+'_from_api')
            try:
                rows[kind]=catalog_details.finalize(self,kind,method(limits[kind]))
                if not rows[kind]:failures[kind]='NO_MATCHED_ROWS'
                elif not any(r['fields']['content_status']=='ready' for r in rows[kind]):
                    failures[kind]='NO_RECOMMENDABLE_CONTENT'
            except SourceError as exc:
                rows[kind]=[];failures[kind]=str(exc)
                self.reports[kind+'_failure']={'status':'FAILED','reason':str(exc),**exc.details}
                logging.getLogger('LOAD_DATA').error('%s collection failed: %s',kind,str(exc))
            logging.getLogger('LOAD_DATA').info('%s: %s개 수집',kind,len(rows[kind]))
        return {'rows':rows,'failures':failures,'sources':self.reports,
                'skill_identity':skills,'unresolved_rule_names':self.matcher.unresolved_rules,
                'selection':self.selection_reports,
                'quality':{k:{'ready':sum(r['fields'].get('content_status')=='ready' for r in group),
                    'needs_enrichment':sum(r['fields'].get('content_status')!='ready' for r in group),
                    'missing_fields':dict(Counter(f for r in group for f in missing_fields(k,r['fields'])))}
                    for k,group in rows.items()},
                'collection_complete':not failures and not any(r.get('page_limit_reached') or r.get('status')=='FAILED' or r.get('failed',0)>0 or r.get('detail_failed',0)>0 for r in self.reports.values())
                    and not any(r['limit_reached'] for r in self.selection_reports.values())}

    def apply(self,conn,batch,*,embed=True,embedding_client=None,allow_partial=False):
        if batch['failures'] and not allow_partial:
            raise ValueError('API 수집 실패 또는 매칭 자료 없음: '+', '.join(batch['failures'])+'. 기존 DB는 변경하지 않았습니다.')
        if not any(batch['rows'].values()):raise ValueError('적재할 API 데이터가 없습니다.')
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(763250131)')
            current=conn.execute('SELECT skill_id,name,category FROM public.skill_tags ORDER BY skill_id').fetchall()
            if current!=batch['skill_identity']:raise ValueError('수집 중 스킬 태그가 변경되었습니다. 다시 실행하세요.')
            deployment.apply_ingestion_schema(conn)
            conn.execute(AUDIT_DDL)
            # Persist newly observed retirement notices for older rows, even though
            # the API adapter deliberately withholds those rows from new ingestion.
            for notice in batch['sources'].get('ms_certifications', {}).get('unavailable', []):
                if notice.get('status') not in ('retired','retiring','availability_needs_review'):
                    continue
                conn.execute("""UPDATE public.certifications c SET content_status=%s
                    FROM growpath_meta.catalog_api_sources s
                    WHERE s.resource_type='certifications' AND s.source='API_MS_CERT'
                      AND s.external_key=%s AND s.entity_key=c.cert_id::text""",
                    (notice['status'], notice['uid']))
            stats={}
            for kind in KINDS:
                source_records=batch['rows'][kind]
                records=[r for r in source_records if r['fields'].get('content_status')=='ready'
                         and exclusion_reason(kind,r['fields'],r['source'],r['raw'],r['fetched_at']) is None]
                if not records:continue
                pk,map_table=TABLES[kind]
                # Synchronize explicit integer-ID allocation with all table writers; keep existing IDs.
                conn.execute(f'LOCK TABLE public.{kind} IN SHARE ROW EXCLUSIVE MODE')
                projection = '*' if kind == 'certifications' else f'{pk},title'
                existing=conn.execute(f'SELECT {projection} FROM public.{kind}').fetchall()
                excluded_cert_ids = {old[pk] for old in existing if kind == 'certifications'
                    and (old.get('content_status') in ('retired','retiring','availability_needs_review')
                         or catalog_details.certification_availability(old) != 'available')}

                titles={}
                for old in existing:titles.setdefault(old['title'],[]).append(old[pk])
                next_id=max((r[pk] for r in existing),default=0)+1 if kind in ('jobs','majors','certifications') else None
                # No sequence mutation here; the CLI syncs it after successful embedding.
                registry={(r['source'],r['external_key']):r['entity_key'] for r in conn.execute(
                    'SELECT source,external_key,entity_key FROM growpath_meta.catalog_api_sources WHERE resource_type=%s',(kind,)).fetchall()}
                counts={'inserted':0,'updated':0,'mapping_links':0,'withheld_for_enrichment':len(source_records)-len(records)}
                old_ids={r[pk] for r in existing}
                used=set()
                for record in records:
                    fields=dict(record['fields']);key=(record['source'],record['external_key'])
                    entity_id=registry.get(key)
                    if kind in ('jobs','majors','certifications'):
                        if entity_id is not None:entity_id=int(entity_id)
                        else:
                            matches=titles.get(fields['title'],[])
                            if len(matches)>1:raise ValueError(f'{kind}: 같은 이름의 기존 ID가 여러 개입니다. 중복을 확인하세요.')
                            if matches:entity_id=matches[0]
                            else:entity_id=next_id;next_id+=1
                    else:
                        if entity_id is not None and entity_id!=fields[pk]:raise ValueError('원본 ID가 기존 출처와 다릅니다.')
                        entity_id=fields[pk]
                    if kind == 'certifications' and entity_id in excluded_cert_ids:
                        counts['withheld_retired_or_retiring'] = counts.get('withheld_retired_or_retiring', 0) + 1
                        continue
                    if entity_id in used:raise ValueError('API 원본 ID 충돌을 발견했습니다. 적재를 중단합니다.')
                    used.add(entity_id)
                    fields[pk]=entity_id
                    fields['last_batch_updated']=datetime.now(timezone.utc)
                    names=list(fields)
                    params=[fields[n] for n in names]
                    assignments=','.join(f'{n}=EXCLUDED.{n}' for n in names if n!=pk)
                    conn.execute(f'''INSERT INTO public.{kind} ({','.join(names)}) VALUES ({','.join(['%s']*len(names))})
                        ON CONFLICT ({pk}) DO UPDATE SET {assignments}''',params)
                    # Replace links only for refreshed records. Unfetched rows and their links remain intact.
                    conn.execute(f'DELETE FROM public.{map_table} WHERE {pk}=%s',(entity_id,))
                    for sid in record['matches']:
                        conn.execute(f'INSERT INTO public.{map_table} ({pk},skill_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',(entity_id,sid))
                    conn.execute('''INSERT INTO growpath_meta.catalog_api_sources
                        (resource_type,source,external_key,entity_key,payload,mapping_evidence,fetched_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (resource_type,source,external_key)
                        DO UPDATE SET entity_key=EXCLUDED.entity_key,payload=EXCLUDED.payload,
                        mapping_evidence=EXCLUDED.mapping_evidence,fetched_at=EXCLUDED.fetched_at''',
                        (kind,*key,str(entity_id),Jsonb(record['raw']),Jsonb(record['matches']),record['fetched_at']))
                    counts['updated' if entity_id in old_ids else 'inserted']+=1
                    counts['mapping_links']+=len(record['matches'])
                stats[kind]=counts
            certification_status_changes=catalog_details.refresh_certification_status(conn)
            vector_result=vector_index.embed(conn,client=embedding_client) if embed else vector_index.status(conn)
            return {'status':'PARTIAL' if batch['failures'] else 'SUCCESS','tables':stats,
                    'sources':batch['sources'],'skipped':batch['failures'], 'vectors':vector_result,
                    'certification_status_changes':certification_status_changes,
                    'quality':batch.get('quality',{}),'selection':batch.get('selection',{}),'collection_complete':batch.get('collection_complete',False),
                    'skills_changed':False,'regulations_changed':False,'user_data_changed':False,
                    'vector_scope':'existing skill/course/book/certification index; existing input format preserved',
                    'database_changes':True,'original_embedding_columns_changed':False}

    def execute_full_pipeline(self,*,dry_run=False,max_pages=None,allow_partial=False,skip_embeddings=False,db_env='SUPABASE_DB_URL'):
        with deployment.connect(db_env) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            skills=conn.execute('SELECT skill_id,name,category FROM public.skill_tags ORDER BY skill_id').fetchall()
        batch=self.collect(skills,max_pages=max_pages)
        if batch['failures'] and not dry_run and not allow_partial:
            return {'status':'FAILED','error_type':'SourceError','database_changes':False,
                    'embedding_api_called':False,'message':'수집 실패로 저장을 시작하지 않았습니다. failures와 sources를 확인하세요.',
                    'counts':{k:len(v) for k,v in batch['rows'].items()},
                    'failures':batch['failures'],'sources':batch['sources'],
                    'quality':batch.get('quality',{}),'selection':batch.get('selection',{}),
                    'collection_complete':batch.get('collection_complete',False)}
        if dry_run:
            return {'status':'READY' if not batch['failures'] else 'INCOMPLETE','database_changes':False,
                    'embedding_api_called':False,'counts':{k:len(v) for k,v in batch['rows'].items()},
                    'sources':batch['sources'],'failures':batch['failures'],
                    'quality':batch.get('quality',{}),'selection':batch.get('selection',{}),'collection_complete':batch.get('collection_complete',False),
                    'unresolved_rule_names':batch['unresolved_rule_names'],
                    'skill_coverage':dict(Counter(sid for rows in batch['rows'].values() for r in rows for sid in r['matches']))}
        with deployment.connect(db_env) as conn:
            result=self.apply(conn,batch,embed=not skip_embeddings,allow_partial=allow_partial)
            # Advance the legacy serial sequence under the table lock only on actual application.
            # Tests call apply() in a rollback transaction and do not touch non-transactional sequences.
            if 'certifications' in result['tables']:
                seq=conn.execute("SELECT pg_get_serial_sequence('public.certifications','cert_id') AS name").fetchone()['name']
                if seq:
                    top=conn.execute('SELECT max(cert_id) AS id FROM public.certifications').fetchone()['id']
                    # nextval never moves backwards; do not lower a sequence already ahead of max(id).
                    current=conn.execute('SELECT nextval(%s::regclass) AS id',(seq,)).fetchone()['id']
                    if top and current<top:conn.execute('SELECT setval(%s::regclass,%s,true)',(seq,top))
        return result

    def clear_all_tables(self):
        raise RuntimeError('전체 삭제 기능을 제거했습니다. load_data.py로 증분 갱신하세요.')

    def ingest_and_map_all(self):
        return self.execute_full_pipeline()

    def map_skills_to_entities(self):
        raise RuntimeError('무근거 전체 재매핑을 중단했습니다. load_data.py로 API 원본과 함께 갱신하세요.')


real_data_ingestor=RealDataIngestor()
__all__=['RealDataIngestor','real_data_ingestor']
