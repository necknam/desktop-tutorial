"""Minimum factual content for recommendation; missing fields are never guessed."""
import re
from datetime import date

CONTENT_FIELDS = {
    'courses': ('syllabus_summary',),
    'books': ('description',),
    'certifications': ('test_subjects',),
    'jobs': ('description',),
    'majors': ('curriculum',),
}
# Explicit children's-book labels only; no arbitrary popularity/rating cutoff.
CHILD_TITLE = re.compile(r'어린이|초등|학습\s*만화|\bWhy\s*\?|\(Job\?\)', re.I)


def missing_fields(kind, row):
    missing = []
    for name in ('title','source_url','source_verified_at'):
        if not row.get(name): missing.append(name)
    if kind == 'courses':
        for name in ('url','org_name'):
            if not row.get(name): missing.append(name)
    if kind == 'books':
        for name in ('isbn','author','publisher'):
            if not row.get(name): missing.append(name)
        if CHILD_TITLE.search(row.get('title') or ''): missing.append('adult_professional_scope')
    if kind == 'certifications':
        if not row.get('provider'): missing.append('provider')
        status = certification_availability(row)
        if status != 'available': missing.append(status)
    if not any(len(str(row.get(n) or '').strip()) >= 20 for n in CONTENT_FIELDS[kind]):
        missing.append('/'.join(CONTENT_FIELDS[kind]))
    return missing


def is_ready(kind, row):
    return row.get('content_status') == 'ready' and not missing_fields(kind, row)


def certification_availability(row, as_of=None):
    """Conservative notice check; unrelated product/support retirements do not match."""
    as_of = as_of or date.today()
    body = ' '.join(str(row.get(k) or '') for k in ('title','description'))
    subject = r'(?:이|본)\s*(?:인증|자격증|시험)'
    english_subject = r'(?:this|the) (?:certification|credential|exam)'
    if re.search(subject+r'[^.。]{0,150}(?:사용\s*중지되었습니다|종료되었습니다|폐지되었습니다)',body,re.I):
        return 'retired'
    if re.search(english_subject+r'[^.。]{0,150}(?:(?:is|are|has been) retired|retired on)',body,re.I):
        return 'retired'
    if re.search(r'\(retired\)|\[retired\]',str(row.get('title') or ''),re.I):return 'retired'
    notices = re.findall(subject+r'[^.。]{0,180}(?:사용\s*중지됩니다|종료될 예정|종료됩니다|폐지됩니다)',body,re.I)
    for notice in notices:
        match = re.search(r'(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일',notice)
        if not match:return 'availability_needs_review'
        try:until=date(*(int(v) for v in match.groups()))
        except ValueError:return 'availability_needs_review'
        return 'retired' if as_of >= until else 'retiring'
    if re.search(english_subject+r'[^.。]{0,180}(?:will retire|will be retired|is retiring|retires on|is scheduled to retire)',body,re.I):
        return 'retiring'
    return 'available'
