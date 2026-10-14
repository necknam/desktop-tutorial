"""Refresh five official API catalogs and the existing compatible resource vector index."""
import argparse
import json
import logging
from pathlib import Path
import sys

from src.pipelines.real_data_ingestor import RealDataIngestor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-env',default='SUPABASE_DB_URL')
    parser.add_argument('--dry-run',action='store_true',help='API collection and validation only; no DB writes or embedding calls')
    parser.add_argument('--max-pages',type=int,default=None,help='Override paths.yaml ingestion.max_pages (1..1000); library.max_pages is a separate per-keyword cap')
    parser.add_argument('--allow-partial',action='store_true',help='Explicitly keep failed categories unchanged while updating successful ones')
    parser.add_argument('--skip-embeddings',action='store_true',help='Defer paid embedding refresh; stale vectors remain blocked by RAG')
    parser.add_argument('--report',type=Path,help='Optional JSON execution report')
    args=parser.parse_args()
    if args.max_pages is not None and not 1<=args.max_pages<=1000:parser.error('--max-pages must be between 1 and 1000')
    logging.basicConfig(level=logging.INFO,format='[%(levelname)s] %(message)s')
    try:
        result=RealDataIngestor().execute_full_pipeline(dry_run=args.dry_run,max_pages=args.max_pages,
            allow_partial=args.allow_partial,skip_embeddings=args.skip_embeddings,db_env=args.db_env)
    except Exception as exc:
        # Do not expose credential-bearing HTTP/DB exceptions.
        result={'status':'FAILED','error_type':type(exc).__name__,
                'message':str(exc) if isinstance(exc,ValueError) else 'API 키·DB 스키마·임베딩 연결을 확인하세요. DB 갱신 트랜잭션은 롤백됩니다.'}
    output=json.dumps(result,ensure_ascii=False,indent=2,default=str)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(output+'\n',encoding='utf-8')
    return 0 if result['status'] in ('READY','SUCCESS') else 2


if __name__=='__main__':
    sys.exit(main())
