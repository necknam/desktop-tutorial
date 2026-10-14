"""Read-only DNS, API authentication/response and catalog-schema diagnosis. Never prints keys."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import socket
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import psycopg
from dotenv import dotenv_values, load_dotenv
import yaml

ALLOWED = {'apis.data.go.kr','api.odcloud.kr','data4library.kr','www.data4library.kr',
           'www.career.go.kr','learn.microsoft.com'}


def resolve(host):
    try:
        socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        return {'status':'PASS'}
    except OSError as exc:
        return {'status':'DNS_FAILED','error_type':type(exc).__name__,'errno':exc.errno}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=args.project_root.resolve()
    for name in ('httpx','httpx2','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
    configured=dotenv_values(root/'.env')
    load_dotenv(root/'.env',override=False)
    cfg=yaml.safe_load((root/'config/paths.yaml').read_text(encoding='utf-8'))['api']
    def key(*names):
        return unquote(next((os.environ[n].strip() for n in names if os.getenv(n,'').strip()),''))
    def api_url(value):
        value=value.replace('http://','https://',1)
        u=urlsplit(value)
        if u.scheme!='https' or u.hostname not in ALLOWED or u.username or u.password or u.query:
            raise ValueError('Unsupported endpoint; credentials were not sent')
        return value
    plans=[
        ('kmooc',cfg['kmooc']['base_url'],{'serviceKey':key('KMOOC_SERVICE_KEY','DATA_GO_KR_API_KEY'),'_type':'json','page':1,'size':1},'items'),
        ('kdata',cfg['kdata']['base_url'],{'serviceKey':key('DATA_GO_KR_API_KEY'),'page':1,'perPage':1},'data'),
        ('university',cfg['university']['base_url'],{'serviceKey':key('DATA_GO_KR_API_KEY'),'page':1,'perPage':1},'data'),
        ('books',urljoin(cfg['library']['base_url'],'srchBooks'),{'authKey':key('LIBRARY_AUTH_KEY','LIBRARY_NARU_API_KEY'),'title':'SQL','pageNo':1,'pageSize':1,'format':'json'},'response.docs'),
        ('jobs',cfg['careernet'].get('jobs_url','https://www.career.go.kr/cnet/front/openapi/jobs.json'),{'apiKey':key('CAREERNET_API_KEY'),'searchAptdCodes':'104740','pageIndex':1},'jobs'),
        ('majors',cfg['careernet'].get('major_url',cfg['careernet']['base_url']),{'apiKey':key('CAREERNET_API_KEY'),'svcType':'api','contentType':'json','gubun':'univ_list','svcCode':'MAJOR','thisPage':1,'perPage':1,'searchTitle':'컴퓨터'},'dataSearch.content'),
        ('microsoft_public','https://learn.microsoft.com/api/catalog/',{'locale':'ko-kr','type':'certifications'},'certifications'),
    ]
    plans=[(n,api_url(u),p,f) for n,u,p,f in plans]
    dburl=os.getenv('SUPABASE_DB_URL','')
    dbhost=urlsplit(dburl).hostname if dburl else None
    hosts=set(urlsplit(p[1]).hostname for p in plans)|{'localhost','example.com'}
    if dbhost:hosts.add(dbhost)
    with ThreadPoolExecutor(max_workers=8) as pool:
        dns=dict(zip(sorted(hosts),pool.map(resolve,sorted(hosts))))
    names=['DATA_GO_KR_API_KEY','KMOOC_SERVICE_KEY','LIBRARY_AUTH_KEY','LIBRARY_NARU_API_KEY','CAREERNET_API_KEY','SUPABASE_DB_URL']
    report={'database_changes':False,'paid_llm_or_embedding_called':False,'dns':dns,
        'key_configuration':{n:{'present':bool(os.getenv(n,'').strip()),
            'environment_matches_env_file':os.getenv(n)==configured.get(n),
            'outer_whitespace':os.getenv(n,'')!=os.getenv(n,'').strip()} for n in names},'apis':{}}
    for name,url,params,field in plans:
        info={'endpoint':url}
        if dns[urlsplit(url).hostname]['status']!='PASS':info['status']='SKIPPED_DNS_FAILED'
        elif any(not v for k,v in params.items() if 'key' in k.lower()):info['status']='MISSING_KEY'
        else:
            try:
                response=httpx.get(url,params=params,timeout=20,follow_redirects=False)
                info['http_status']=response.status_code
                if response.status_code!=200:
                    info['status']='HTTP_ERROR'
                    if response.status_code in (401,403):info['check']='API authorization/key/service approval'
                    elif response.status_code==404:info['check']='Endpoint path'
                    elif response.status_code==429:info['check']='API request quota'
                else:
                    data=response.json()
                    for part in field.split('.'):
                        data=data.get(part) if isinstance(data,dict) else None
                    info['status']='PASS' if isinstance(data,list) else 'API_ERROR_OR_RESPONSE_SCHEMA_CHANGED'
                    if isinstance(data,list):info['returned_rows']=len(data)
            except Exception as exc:
                info.update(status='REQUEST_FAILED',error_type=type(exc).__name__)
        report['apis'][name]=info
    if not dburl:report['database']={'status':'MISSING_SUPABASE_DB_URL'}
    elif dns[dbhost]['status']!='PASS':report['database']={'status':'SKIPPED_DNS_FAILED'}
    else:
        try:
            with psycopg.connect(dburl,connect_timeout=10,sslmode='require') as conn:
                conn.execute('SET TRANSACTION READ ONLY')
                counts={};missing={}
                for table in ('courses','books','certifications','jobs','majors'):
                    counts[table]=conn.execute('SELECT count(*) FROM public.'+table).fetchone()[0]
                    present={r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,)).fetchall()}
                    required={'source_url','source_verified_at','content_status'}
                    if table!='courses':required.add('description')
                    missing[table]=sorted(required-present)
                report['database']={'connection':'PASS','status':'CONTENT_COLUMNS_MISSING' if any(missing.values()) else 'PASS',
                    'counts':counts,'missing_content_columns':missing}
        except Exception as exc:
            report['database']={'status':'FAILED','error_type':type(exc).__name__,'sqlstate':getattr(exc,'sqlstate',None)}
    output=json.dumps(report,ensure_ascii=False,indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output+'\n',encoding='utf-8')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'DIAGNOSTIC_FAILED','error_type':type(exc).__name__}))
        raise SystemExit(1)
