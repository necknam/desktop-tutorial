"""Fail closed on unknown availability. No generated evidence and no DB mutations."""
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.common.catalog_quality import missing_fields, certification_availability

PRIMARY_KEYS = {'courses': 'course_id', 'books': 'isbn', 'certifications': 'cert_id',
                'jobs': 'job_id', 'majors': 'major_id'}
MAX_EVIDENCE_AGE_DAYS = 30


def timestamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('Asia/Seoul'))
    except (ValueError, TypeError):
        return None


def public_url(value, hosts):
    try:
        url = urlsplit(str(value or ''))
        return url.scheme == 'https' and url.hostname in hosts and not url.username and not url.password
    except ValueError:
        return False


def exclusion_reason(kind, fields, source, raw, fetched_at, *, now=None):
    now = now or datetime.now(timezone.utc)
    checked = timestamp(fetched_at)
    if checked is None or not 0 <= (now - checked).total_seconds() <= MAX_EVIDENCE_AGE_DAYS * 86400:
        return 'missing_or_expired_availability_evidence'
    if fields.get('content_status') not in (None, 'ready'):
        return 'not_ready'
    if missing_fields(kind, fields):
        return 'insufficient_factual_content'
    if not isinstance(raw, dict):
        return 'missing_official_detail'
    if kind == 'certifications':
        if certification_availability(fields, now.astimezone(ZoneInfo('Asia/Seoul')).date()) != 'available':
            return 'retired_or_retiring_or_uncertain'
        if source != 'API_MS_CERT':
            # KDATA subject/pass-score data does not establish current exam availability.
            return 'no_current_exam_availability_evidence'
        item = raw.get('merged_certification') or {}
        if not item:
            return 'missing_current_exam_detail'
        notices = ' '.join(str(record.get(key) or '') for record in
                          [raw.get('certification') or {}, item, *(raw.get('exam_records') or [])]
                          for key in ('title', 'summary', 'subtitle'))
        if certification_availability({'description': notices}, now.date()) != 'available':
            return 'retired_or_retiring_or_uncertain'
        providers = item.get('providers') or []
        if not any(isinstance(p, dict) and public_url(p.get('examUrl'),
                   {'learn.microsoft.com', 'certiport.pearsonvue.com', 'www.pearsonvue.com', 'www.certiport.com'})
                   for p in providers):
            return 'no_current_exam_booking_path'
        return None
    if kind == 'books':
        detail = raw.get('detail_record') or {}
        evidence = raw.get('availability_evidence') or {}
        libraries = evidence.get('libraries') or []
        if str(detail.get('isbn13')) != str(fields.get('isbn')):
            return 'book_detail_identity_mismatch'
        if (evidence.get('endpoint') != 'https://data4library.kr/api/libSrchByBook'
                or str(evidence.get('isbn')) != str(fields.get('isbn'))
                or not timestamp(evidence.get('checked_at'))
                or not 0 <= (now - timestamp(evidence['checked_at'])).total_seconds() <= 30 * 86400
                or not any(isinstance(lib, dict) and lib.get('libCode') and lib.get('libName') for lib in libraries)):
            return 'no_current_library_holding_evidence'
        return None
    if kind == 'courses':
        if source == 'API_MS_LEARN':
            if (raw.get('type') == 'module' and raw.get('uid') and raw.get('units')
                    and public_url(raw.get('url'), {'learn.microsoft.com'})):
                return None
            return 'missing_published_learning_module'
        if source == 'API_KMOOC':
            dates = [timestamp(raw.get(k)) for k in ('enrollment_start', 'enrollment_end', 'study_end')]
            if all(dates) and dates[0] <= now <= dates[1] and now <= dates[2] and raw.get('public_yn') == 'Y':
                return None
            return 'course_not_confirmed_open'
        return 'unsupported_availability_source'
    if kind == 'jobs' and source == 'API_CAREERNET':
        return None if (raw.get('detail_record') or {}).get('baseInfo', {}).get('job_cd') else 'missing_current_job_detail'
    if kind == 'majors' and source == 'API_CAREERNET_MAJOR':
        return None if (raw.get('detail_record') or {}).get('main_subject') else 'missing_current_major_detail'
    return 'unsupported_availability_source'


def eligible_keys(conn):
    """Recheck saved source evidence at retrieval time; legacy rows fail closed."""
    sources = {}
    for row in conn.execute('''SELECT resource_type,entity_key,source,payload,fetched_at
                               FROM growpath_meta.catalog_api_sources ORDER BY fetched_at DESC''').fetchall():
        sources.setdefault((row['resource_type'], row['entity_key']), []).append(row)
    eligible, report = set(), {}
    for kind, pk in PRIMARY_KEYS.items():
        reasons, count = Counter(), 0
        for record in conn.execute(f"SELECT to_jsonb(t)-'embedding' AS record FROM public.{kind} t").fetchall():
            fields = record['record']
            key = (kind, str(fields[pk]))
            failures = [exclusion_reason(kind, fields, s['source'], s['payload'], s['fetched_at'])
                        for s in sources.get(key, [])]
            count += 1
            if failures and None in failures:
                eligible.add(key)
            else:
                reasons[failures[0] if failures else 'missing_api_source_record'] += 1
        report[kind] = {'total': count, 'usable': sum(k[0] == kind for k in eligible),
                        'excluded': dict(reasons)}
    return eligible, report
