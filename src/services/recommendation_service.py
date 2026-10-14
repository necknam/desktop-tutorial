"""Vector rank within real DB skill-resource relationships, never a global fallback."""
import logging
import math
from contextlib import nullcontext
from urllib.parse import urlsplit

from src.db import resource_vectors as vectors
from src.common.catalog_quality import certification_availability
from src.common.catalog_eligibility import eligible_keys
from src.db.db_client import serializable

logger = logging.getLogger('RECOMMENDATION_SERVICE')
LIMITS = {'courses': 12, 'certifications': 6, 'books': 6}
MIN_SIMILARITY = 0.20  # Initial retrieval policy; evaluate recall before changing.


def normalize_course_url(raw_url, title='', platform=''):
    """Keep only actual absolute HTTP(S) URLs. Never invent a vendor or homepage."""
    if not isinstance(raw_url, str):
        return None
    value = raw_url.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


class RecommendationService:
    normalize_course_url = staticmethod(normalize_course_url)

    def retrieve_grounding_context(self, target_skills, target_job_title='',
                                   assessment_summary=None, project_summary='', *, conn=None):
        try:
            with (nullcontext(conn) if conn is not None else vectors.connect('SUPABASE_DB_URL')) as connection:
                if conn is None:
                    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return self._retrieve(connection, target_skills, target_job_title,
                                      assessment_summary, project_summary)
        except ValueError:
            raise
        except Exception as exc:
            logger.error('학습 자료 검색 실패 (%s)', type(exc).__name__)
            raise RuntimeError('학습 자료 검색 실패: DB 연결 및 임베딩 API 상태를 확인하세요. 결과를 저장하지 않았습니다.') from None

    def _retrieve(self, conn, target_skills, job_title, assessment, project):
        rows = vectors.sources(conn)
        skills = {v['record']['name']: v['record'] for k, v in rows.items() if k[0] == 'skills'}
        if (not isinstance(target_skills, list) or not target_skills
                or any(not isinstance(s, str) or s not in skills for s in target_skills)):
            raise ValueError('목표 스킬을 DB의 실제 스킬 태그로 선택하세요.')
        targets = {skills[name]['skill_id']: skills[name] for name in target_skills}
        scores = {}
        if assessment is not None:
            if (assessment.get('schema_version') != 'skill_self_report_v1'
                    or assessment.get('assessment_method') != 'self_report'):
                raise ValueError('저장된 5점 자기보고 진단 입력이 필요합니다.')
            for item in assessment.get('skills', []):
                sid, value = item.get('skill_id'), item.get('mean_score')
                if (type(sid) is not int or sid not in targets or sid in scores
                        or item.get('skill') != targets[sid]['name']
                        or type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 5):
                    raise ValueError('진단 스킬/점수와 DB 목표 스킬이 일치하지 않습니다.')
                scores[sid] = value
            if set(scores) != set(targets):
                raise ValueError('목표 스킬의 진단 결과가 누락되었습니다.')
        skill_query = '\n'.join(targets[i]['name'] for i in sorted(targets))
        query = f'직무: {job_title}\n프로젝트: {project}\n학습 목표 스킬:\n{skill_query}'
        # Check provenance before paying for a query embedding.
        usable, eligibility_report = eligible_keys(conn)
        previous = vectors.stored(conn)
        relevant = {k: v for k, v in rows.items() if
                    (k[0] == 'skills' and v['record']['skill_id'] in targets) or
                    (k[0] != 'skills' and k in usable and set(v['record']['linked_skill_ids']) & set(targets)
                     and (k[0] != 'certifications' or (v['record'].get('content_status') == 'ready'
                         and len(str(v['record'].get('test_subjects') or '').strip()) >= 20
                         and certification_availability(v['record']) == 'available')))}
        if any(not vectors.is_current(v, previous.get(k)) for k, v in relevant.items()):
            raise ValueError('학습 자원 벡터가 없거나 오래되었습니다. python -m src.db.resource_vectors embed 를 실행하세요.')
        query_vector = vectors.embed_texts([query])[0]
        similarities = vectors.similarities(conn, rows, relevant, query_vector)
        ranked_skills = []
        for sid, skill in targets.items():
            sim = similarities[('skills', str(sid))]
            # A self-report gap is a prioritization hint, not an objective proficiency grade.
            gap = (5 - scores[sid]) / 4 if sid in scores else 0
            ranked_skills.append({'skill_id': sid, 'name': skill['name'], 'similarity': sim,
                                  'self_report_mean': scores.get(sid), 'priority': 0.8 * sim + 0.2 * gap})
        ranked_skills.sort(key=lambda s: (-s['priority'], s['skill_id']))
        context = {'target_job': job_title, 'target_skills': list(dict.fromkeys(target_skills)),
                   'job_profile': None, 'courses': [], 'certifications': [], 'books': []}
        coverage = {sid: {} for sid in targets}
        for kind, limit in LIMITS.items():
            candidates = []
            for key, source in relevant.items():
                if key[0] != kind:
                    continue
                row = serializable(source['record'])
                matched = sorted(set(row['linked_skill_ids']) & set(targets))
                for sid in matched:
                    coverage[sid][kind] = coverage[sid].get(kind, 0) + 1
                sim = similarities[key]
                if sim < MIN_SIMILARITY:
                    continue
                gap = max(((5 - scores[sid]) / 4 if sid in scores else 0) for sid in matched)
                row['matched_skill_ids'] = matched
                row['retrieval'] = {'similarity': sim, 'ranking_score': 0.8 * sim + 0.2 * gap,
                                    'source_sha256': source['hash']}
                if kind == 'courses':
                    row['source_url'] = row.get('url')
                    row['url'] = normalize_course_url(row.get('url'))
                    row['url_status'] = 'db_absolute_url' if row['url'] else 'missing_or_non_absolute'
                candidates.append(row)
            pk = vectors.SPECS[kind][1]
            candidates.sort(key=lambda r: (-r['retrieval']['ranking_score'], str(r[pk])))
            # Cover priority skills once before filling remaining slots by vector rank.
            picked, picked_ids = [], set()
            for skill in ranked_skills:
                if any(skill['skill_id'] in r['matched_skill_ids'] for r in picked):
                    continue
                candidate = next((r for r in candidates if skill['skill_id'] in r['matched_skill_ids']), None)
                if candidate and len(picked) < limit:
                    picked.append(candidate)
                    picked_ids.add(str(candidate[pk]))
            for row in candidates:
                if len(picked) == limit:
                    break
                if str(row[pk]) not in picked_ids:
                    picked.append(row)
                    picked_ids.add(str(row[pk]))
            context[kind] = picked
        if job_title:
            job = conn.execute('SELECT job_id,title,ncs_units FROM public.jobs WHERE title=%s ORDER BY job_id LIMIT 1',
                               (job_title,)).fetchone()
            context['job_profile'] = dict(job) if job else None
        coverage_rows = []
        for sid, skill in targets.items():
            coverage_rows.append({'skill_id': sid, 'name': skill['name'],
                'linked_counts': {kind: coverage[sid].get(kind, 0) for kind in LIMITS},
                'retrieved_counts': {kind: sum(sid in r['matched_skill_ids'] for r in context[kind]) for kind in LIMITS}})
        context['resource_rag'] = {
            'schema_version': 'db_resource_rag_v1', 'embedding_model': vectors.MODEL,
            'dimensions': vectors.DIMENSIONS, 'input_version': vectors.INPUT_VERSION,
            'query': query, 'matched_skills': ranked_skills, 'coverage': coverage_rows,
            'limits': LIMITS, 'minimum_similarity': MIN_SIMILARITY,
            'ranking_policy': '0.8*cosine_similarity + 0.2*self_report_gap; priority-skill coverage first',
            'no_evidence': not any(context[kind] for kind in LIMITS),
            'legacy_vectors_used': False,
            'availability_filter': eligibility_report,
        }
        logger.info('RAG: courses=%s certifications=%s books=%s', *(len(context[k]) for k in LIMITS))
        return context


recommendation_service = RecommendationService()
__all__ = ['RecommendationService', 'recommendation_service', 'normalize_course_url']
