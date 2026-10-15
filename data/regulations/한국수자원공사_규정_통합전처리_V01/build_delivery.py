"""Rebuild one regulation handoff from frozen input rows and reviewed corrections.

This program writes local JSON/CSV files only. It does not create or access a DB.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NS = uuid.UUID('c4e00b74-f728-4df1-8766-291864902b0c')


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def uid(key: str) -> str:
    return str(uuid.uuid5(NS, 'unified-reviewed-v1:' + key))


def locator(source: dict) -> dict:
    page = source.get('pdf_page')
    pages = source.get('pdf_pages') or ([page] if page else [])
    lines = [{'line_id': key, 'page': page, 'bbox': bbox}
             for key, bbox in zip(source.get('line_ids', []), source.get('line_bboxes', []))]
    result = {'coordinate_system': 'PDF point; 원문 보정 자료의 문자 좌표', 'pdf_pages': pages,
            'printed_pages': pages, 'lines': lines, 'section_kind': 'annex',
            'correction_source_locator': copy.deepcopy(source)}
    for key in ['source_images', 'image_storage_base']:
        if key in source:
            result[key] = copy.deepcopy(source[key])
    return result


def requirement_content(req: dict) -> str:
    """Keep course names, areas, a printed dash, and a blank distinct."""
    state = req['source_state']
    if state == 'course_list':
        courses = []
        for course in req['courses']:
            text = course['course_name']
            if course['education_areas']:
                text += ' [분야: ' + ' / '.join(course['education_areas']) + ']'
            courses.append(text)
        if not courses:
            raise ValueError('과정 목록이 비어 있음: ' + req['requirement_key'])
        return ' / '.join(courses)
    if state == 'source_blank':
        return '[원문 공란]'
    if state == 'source_dash':
        return '- [원문 기호]'
    if state == 'separately_defined' and req['content_text']:
        return req['content_text']
    raise ValueError('교육 기준 상태 확인 필요: ' + req['requirement_key'])


def normalize_article_labels(records: dict) -> list:
    """Repair generated locators only when the original heading proves the label."""
    replacements = {}
    changes = []
    for node in records['regulation_node']:
        match = re.fullmatch(r'제(\d+)의(\d+)조', node['label'] or '')
        if node['node_type'] != 'article' or match is None:
            continue
        label = '제' + match[1] + '조의' + match[2]
        if not re.sub(r'\s+', '', node['body_text']).startswith(label):
            raise ValueError('조번호와 원문 제목 대조 필요: ' + node['regulation_node_id'])
        replacements[(node['regulation_version_id'], node['label'])] = label
        changes.append({'regulation_node_id': node['regulation_node_id'], 'old_label': node['label'],
                        'corrected_label': label, 'pdf_pages': node['source_locator']['pdf_pages']})
        node['label'] = label
        node['review_note'] = (node['review_note'] or '') + ' 원문 제목과 대조하여 조번호 위치 표시의 조·의 순서를 보정.'
    def corrected_path(path, version_id):
        return ' > '.join(replacements.get((version_id, part), part) for part in path.split(' > '))
    for node in records['regulation_node']:
        node['path'] = corrected_path(node['path'], node['regulation_version_id'])
    for chunk in records['rag_chunk']:
        original = chunk['context_path']
        corrected = corrected_path(original, chunk['regulation_version_id'])
        if corrected != original:
            if not chunk['chunk_text'].startswith(original + '\n'):
                raise ValueError('검색 조각의 위치 머리말 대조 필요: ' + chunk['rag_chunk_id'])
            chunk['context_path'] = corrected
            chunk['chunk_text'] = corrected + chunk['chunk_text'][len(original):]
            chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    return changes


def build():
    records = copy.deepcopy(read(ROOT / 'provenance/base_tables.json'))
    config = read(ROOT / 'delivery_config.json')
    stamp = config['prepared_at']
    ledger = {'appended_rows': Counter(), 'replaced_nodes': [], 'superseded_chunks': [],
              'reference_links_added': [], 'normalized_search_statuses': [],
              'source_gap_annotations': [], 'deleted_scope_annotations': [], 'article_label_corrections': [],
              'promotion_context_connections': [], 'annual_leave_context_connections': [],
              'flexible_work_context_connections': [], 'salary_context_connections': [],
              'salary_annex_boundary_corrections': [], 'leave_context_connections': [],
              'supplement_continuations_corrected': []}
    nodes = {n['regulation_node_id']: n for n in records['regulation_node']}
    next_order = defaultdict(int)
    for node in records['regulation_node']:
        next_order[node['regulation_file_id']] = max(next_order[node['regulation_file_id']], node['display_order'] + 1)

    def add_node(key, parent, label, title, text, source, kind='table_row'):
        file_id = parent['regulation_file_id']
        node = {'regulation_node_id': uid(key), 'regulation_version_id': parent['regulation_version_id'],
                'regulation_file_id': file_id, 'parent_node_id': parent['regulation_node_id'],
                'node_type': kind, 'label': label, 'title': title, 'path': parent['path'] + ' > ' + title,
                'display_order': next_order[file_id], 'body_text': text, 'source_locator': locator(source),
                'effective_from_override': None, 'effective_to_override': None,
                'extraction_status': 'manual_corrected_table',
                'review_note': '이미지 대조 보정. 원문 명시값과 사용자 안내 방향을 구분. 개인별 자동 적용 없음.'}
        next_order[file_id] += 1
        records['regulation_node'].append(node)
        nodes[node['regulation_node_id']] = node
        ledger['appended_rows']['regulation_node'] += 1
        return node

    def add_chunk(node):
        text = node['path'] + '\n' + node['body_text']
        chunk_id = uid(node['regulation_node_id'] + ':chunk')
        records['rag_chunk'].append({'rag_chunk_id': chunk_id, 'regulation_version_id': node['regulation_version_id'],
            'chunk_text': text, 'chunk_type': 'annex', 'context_path': node['path'],
            'content_hash': sha(text.encode('utf-8')), 'chunking_version': 'unified-manual-v1',
            'index_revision': 'unified-v1', 'embedding_model': None, 'search_status': 'needs_review',
            'created_at': stamp, 'token_count': None})
        records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk_id + ':source'),
            'rag_chunk_id': chunk_id, 'regulation_node_id': node['regulation_node_id'], 'source_order': 1,
            'source_role': 'primary', 'source_span': copy.deepcopy(node['source_locator'])})
        ledger['appended_rows']['rag_chunk'] += 1
        ledger['appended_rows']['rag_chunk_source'] += 1

    def add_condition(node, key, text):
        records['regulation_scope'].append({'regulation_scope_id': uid('scope:' + key),
            'regulation_node_id': node['regulation_node_id'], 'scope_type': 'annex_condition',
            'condition_text': text, 'condition_json': None, 'applies_to_node_id': None,
            'review_status': 'pending_review'})
        ledger['appended_rows']['regulation_scope'] += 1

    def replace_annex(node_id, text):
        node = nodes[node_id]
        ledger['replaced_nodes'].append({'node_id': node_id,
            'original_body_sha256': sha(node['body_text'].encode('utf-8')),
            'corrected_body_sha256': sha(text.encode('utf-8')),
            'original_body_preserved_in': 'provenance/base_tables.json'})
        node['body_text'] = text
        node['extraction_status'] = 'manual_corrected_table'
        node['review_note'] = (node['review_note'] or '') + ' 통합본에서 원문 이미지 대조 보정 내용으로 교체. 당초 추출은 provenance/base_tables.json에 보존.'

    promotion = read(ROOT / '보정/일반직_승진검토_V01/승진_검토데이터.json')
    for rule in promotion['minimum_durations']:
        records['regulation_scope'].append({'regulation_scope_id': uid('scope:' + rule['rule_key']),
            'regulation_node_id': rule['source_regulation_node_id'], 'scope_type': 'promotion_minimum_duration',
            'condition_text': rule['source_text'], 'condition_json': {
                'structure_kind': 'source_values_only', 'source_values': copy.deepcopy(rule['source_values']),
                'derived_values': copy.deepcopy(rule['derived_values']), 'source_locator': copy.deepcopy(rule['source_locator']),
                'employee_eligibility_decision': None}, 'applies_to_node_id': None,
            'review_status': 'source_text_verified_by_codex'})
        ledger['appended_rows']['regulation_scope'] += 1
    context = promotion['minimum_context']
    chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == context['target_chunk_id'])
    for order, node_id in enumerate(context['context_node_ids'], 2):
        source_node = nodes[node_id]
        chunk['chunk_text'] += '\n\n관련 원문 ' + source_node['path'] + ':\n' + source_node['body_text']
        records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':promotion-context:' + node_id),
            'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': node_id, 'source_order': order,
            'source_role': 'context', 'source_span': copy.deepcopy(source_node['source_locator'])})
        ledger['appended_rows']['rag_chunk_source'] += 1
        ledger['promotion_context_connections'].append({'rag_chunk_id': chunk['rag_chunk_id'], 'context_node_id': node_id})
    chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))

    leave_context = promotion['leave_reference_context']
    chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == leave_context['target_chunk_id'])
    source_node = nodes[leave_context['context_node_id']]
    chunk['chunk_text'] += '\n\n관련 원문 ' + source_node['path'] + ':\n' + source_node['body_text']
    records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':leave-context:' + source_node['regulation_node_id']),
        'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': source_node['regulation_node_id'], 'source_order': 2,
        'source_role': 'context', 'source_span': copy.deepcopy(source_node['source_locator'])})
    ledger['appended_rows']['rag_chunk_source'] += 1
    chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))

    # Keep old flattened education and job-classification tables traceable.
    # traceability but prevent both parses from entering the answer context.
    annex1_id = 'b7adcb91-3b9f-59fe-a068-8b82b2c5c8fd'
    annex2_id = 'b636b617-1ddd-5d98-be44-9e725925e174'
    annex3_id = '16332bc1-75f8-595b-b771-e5a30fd650fd'
    jobclass_id = '66fea751-a1ad-5d3d-a824-136223046289'
    remote_annex_id = 'c826c33b-76d8-52b3-958b-5c768deb9deb'
    superseded_nodes = {annex1_id, annex2_id, annex3_id, jobclass_id, remote_annex_id,
                        '456583b1-143b-57e8-95ec-e7f6766b74b4', '3dc297f0-3c9e-558d-bce7-f9cfc7d3229e',
                        '4f2289a8-4c2f-51a1-aca7-bb86c367f7bc', 'cf6547d0-2c8d-58df-ba21-80f9c8e78cfd',
                        '42ce0205-7a40-5387-81b4-035af462f8d1', 'e5abcf31-f2a9-5603-b4e5-0f84a0e5e8d8'}
    while True:
        more = {n['regulation_node_id'] for n in records['regulation_node'] if n['parent_node_id'] in superseded_nodes}
        if more <= superseded_nodes:
            break
        superseded_nodes |= more
    superseded_ids = {s['rag_chunk_id'] for s in records['rag_chunk_source'] if s['regulation_node_id'] in superseded_nodes}
    for chunk in records['rag_chunk']:
        if chunk['rag_chunk_id'] in superseded_ids:
            chunk['search_status'] = 'superseded'
            ledger['superseded_chunks'].append(chunk['rag_chunk_id'])
        elif chunk['search_status'] == 'pending_review':
            chunk['search_status'] = 'needs_review'
            ledger['normalized_search_statuses'].append(chunk['rag_chunk_id'])

    for path in ['보정/직제규정_정원표_V01/정원표_보정데이터.json',
                 '보정/직제규정_기구_분장_V01/기구_분장_보정데이터.json']:
        correction = read(ROOT / path)
        for name, rows in correction['rag_row_candidates'].items():
            new_rows = copy.deepcopy(rows)
            for row in new_rows:
                for key in ['source_locator', 'source_span']:
                    if key in row:
                        row[key]['image_storage_base'] = str(Path(path).parent).replace('\\', '/')
            records[name].extend(new_rows)
            ledger['appended_rows'][name] += len(new_rows)
    nodes.update({n['regulation_node_id']: n for n in records['regulation_node']})

    jobclass = read(ROOT / '보정/직제규정시행세칙_직종직렬_V01/직종직렬_보정데이터.json')
    job_parent = nodes[jobclass_id]
    job_body = ['별표4 직급별 직종의 운영 및 직렬 구분 (제3조제2항 관련)']
    job_nodes = []
    job_notes = []
    for row in jobclass['rows']:
        text = '대상 구분: ' + row['job_family'] + '\n직급: ' + row['source_grade_text']
        if row['source_merged_category_series_text'] is not None:
            text += '\n직종·직렬 병합 칸 원문: ' + row['source_merged_category_series_text']
        else:
            text += '\n직종: ' + row['job_category'] + '\n직렬: ' + ', '.join(row['job_series'])
        node = add_node('orgbylaw:annex4:' + row['row_key'], job_parent, row['row_key'],
                        '일반직 ' + row['source_grade_text'] + (' ' + row['job_category'] if row['job_category'] else ''),
                        text, row['source_locator'])
        add_chunk(node)
        job_nodes.append(node)
        job_body.append(text)
    for note in jobclass['notes']:
        node = add_node('orgbylaw:annex4:' + note['note_id'], job_parent, note['note_id'],
                        '직종·직렬 정의 ' + note['note_id'], note['source_text'], note['source_locator'], 'annex_note')
        add_chunk(node)
        job_notes.append(node)
        job_body.append(note['source_text'])
    for node in job_nodes:
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == uid(node['regulation_node_id'] + ':chunk'))
        for order, note in enumerate(job_notes, 2):
            chunk['chunk_text'] += '\n\n관련 원문 주석:\n' + note['body_text']
            records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':context:' + note['label']),
                'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': note['regulation_node_id'],
                'source_order': order, 'source_role': 'context', 'source_span': copy.deepcopy(note['source_locator'])})
            ledger['appended_rows']['rag_chunk_source'] += 1
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    replace_annex(jobclass_id, '\n\n'.join(job_body))
    job_parent['source_locator']['pdf_pages'] = [46]
    job_parent['source_locator']['lines'] = [l for l in job_parent['source_locator']['lines'] if l['page'] == 46]
    job_parent['source_locator']['source_images'] = jobclass['rows'][0]['source_locator']['source_images']
    job_parent['source_locator']['image_storage_base'] = '보정/직제규정시행세칙_직종직렬_V01'

    annex1 = read(ROOT / '보정/인재개발규정_별표1_V01/별표1_보정데이터.json')
    parent1 = nodes[annex1_id]
    body1 = ['별표1 학습총량제 직급별 연간 기준학습 시간 (제7조제1항 관련)']
    hour_nodes = []
    note_nodes = []
    for row in annex1['rows']:
        text = ('대상 구분: ' + row['source_job_family_text'] + '\n직급·구분: ' + row['source_grade_text'] +
                '\n연간 기준학습시간(부서자체/개인학습 포함): ' + str(row['annual_hours']) + '시간')
        node = add_node('talent:annex1:' + row['row_key'], parent1, row['row_key'],
                        row['source_job_family_text'] + ' ' + row['source_grade_text'], text, row['source_locator'])
        add_chunk(node)
        hour_nodes.append(node)
        body1.append(text)
    for note in annex1['notes']:
        node = add_node('talent:annex1:' + note['note_id'], parent1, note['note_id'],
                        '별표1 주석 ' + note['note_id'], note['source_text'], note['source_locator'], 'annex_note')
        add_chunk(node)
        add_condition(node, 'talent:annex1:' + note['note_id'], note['source_text'])
        note_nodes.append(node)
        body1.append(note['source_text'])
    for node in hour_nodes:
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == uid(node['regulation_node_id'] + ':chunk'))
        for order, note in enumerate(note_nodes, 2):
            chunk['chunk_text'] += '\n\n관련 원문 주석:\n' + note['body_text']
            records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':context:' + note['label']),
                'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': note['regulation_node_id'],
                'source_order': order, 'source_role': 'context', 'source_span': copy.deepcopy(note['source_locator'])})
            ledger['appended_rows']['rag_chunk_source'] += 1
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    replace_annex(annex1_id, '\n\n'.join(body1))

    annex2 = read(ROOT / '보정/인재개발규정_별표2_V01/별표2_보정데이터.json')
    parent2 = nodes[annex2_id]
    body2 = ['별표2 필수교육체계 - 원문 이미지 대조 보정']
    requirement_note_links = []
    condition_nodes = {}
    for req in annex2['requirements']:
        content = requirement_content(req)
        text = f'대상 구분: {req["job_family"]}\n직급·구분: {req["grade_label"]}\n역량: {req["competency_domain"]}\n원문 기준: {content}'
        if req['condition_ids']:
            text += '\n관련 원문 주석: ' + ', '.join(req['condition_ids'])
        title = req['job_family'] + ' ' + req['grade_label'] + ' ' + req['competency_domain']
        node = add_node('talent:annex2:' + req['requirement_id'], parent2, req['requirement_key'], title,
                        text, req['source_locator'])
        add_chunk(node)
        requirement_note_links.append((node['regulation_node_id'], req['condition_ids']))
        body2.append(text)
    for condition in annex2['conditions']:
        text = condition['source_locator']['original_extracted_text']
        key = condition['condition_id']
        node = add_node('talent:annex2:' + key, parent2, key, '주석 ' + key + ' ' + condition['title'],
                        text, condition['source_locator'], 'annex_note')
        add_chunk(node)
        add_condition(node, 'talent:annex2:' + key, text)
        condition_nodes[key] = node
        body2.append(key + ': ' + text)
    for requirement_node_id, condition_ids in requirement_note_links:
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == uid(requirement_node_id + ':chunk'))
        for order, key in enumerate(condition_ids, 2):
            condition_node = condition_nodes[key]
            chunk['chunk_text'] += '\n\n관련 원문 주석 ' + key + ':\n' + condition_node['body_text']
            records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':context:' + key),
                'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': condition_node['regulation_node_id'],
                'source_order': order, 'source_role': 'context', 'source_span': copy.deepcopy(condition_node['source_locator'])})
            ledger['appended_rows']['rag_chunk_source'] += 1
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    for comparison in annex2['change_comparison']:
        content = requirement_content({**comparison, 'content_text': None, 'requirement_key': comparison['comparison_id']})
        text = (comparison['comparison_context'] + '\n' + comparison['phase_label'] + '\n' +
                comparison['job_family'] + ' ' + comparison['grade_label'] + ' ' + comparison['competency_domain'] + '\n' + content)
        node = add_node('talent:annex2:' + comparison['comparison_id'], parent2, comparison['phase_label'],
                        comparison['comparison_context'] + ' ' + comparison['phase_label'] + ' ' + comparison['grade_label'],
                        text, comparison['source_locator'], 'table_row')
        add_chunk(node)
        body2.append(text)
    replace_annex(annex2_id, '\n\n'.join(body2))

    annex3 = read(ROOT / '보정/인재개발규정_별표3_V01/별표3_보정데이터.json')
    parent3 = nodes[annex3_id]
    fields = {g['group_id']: g for g in annex3['field_groups']}
    stages = {s['stage_id']: s for s in annex3['stages']}
    body3 = ['별표3 통합직무 교육체계 - 원문 이미지 대조 보정']
    header = '\n'.join(g['label'] + ': ' + ' / '.join(g['job_fields']) for g in annex3['field_groups'])
    body3.append(header)
    progression = annex3['progression']
    progression_text = progression['source_locator']['original_extracted_text']
    progression_node = add_node('talent:annex3:progression', parent3, '학습 진행 표시', '학습 진행 표시',
                                progression_text, progression['source_locator'], 'annex_note')
    add_chunk(progression_node)
    body3.append(progression_text)
    for block in annex3['learning_blocks']:
        stage = stages[block['stage_id']]
        lines = [stage['label'], '시기: ' + stage['source_timing_text'], block['label'],
                 '방법: ' + block['source_method_text']]
        if block['source_duration_text']:
            lines.append('기간: ' + block['source_duration_text'])
        topics = [t for t in annex3['learning_topics'] if t['block_id'] == block['block_id']]
        for group in [None] + block['source_group_ids']:
            relevant = [t for t in topics if t['group_id'] == group]
            if relevant:
                lines.append('공통 학습 내용' if group is None else fields[group]['label'] + ': ' + ' / '.join(fields[group]['job_fields']))
                lines.extend('- ' + t['topic_text'] for t in relevant)
        lines.extend('평가 표시: ' + e['source_label'] for e in annex3['evaluations'] if e['block_id'] == block['block_id'])
        text = '\n'.join(lines)
        node = add_node('talent:annex3:' + block['block_id'], parent3, block['label'],
                        stage['label'] + ' ' + block['label'], text, block['source_locator'])
        add_chunk(node)
        body3.append(text)
    for condition in annex3['conditions']:
        text = condition['source_locator']['original_extracted_text']
        node = add_node('talent:annex3:' + condition['condition_id'], parent3, condition['condition_id'],
                        '주석 ' + condition['condition_id'], text, condition['source_locator'], 'annex_note')
        add_chunk(node)
        add_condition(node, 'talent:annex3:' + condition['condition_id'], text)
        body3.append(text)
    replace_annex(annex3_id, '\n\n'.join(body3))

    ledger['article_label_corrections'] = normalize_article_labels(records)
    annual = read(ROOT / '보정/취업규칙_연차검토_V01/연차_검토데이터.json')
    for fact in annual['source_facts']:
        records['regulation_scope'].append({'regulation_scope_id': uid('scope:' + fact['rule_key']),
            'regulation_node_id': fact['source_regulation_node_id'], 'scope_type': 'annual_leave_source_criterion',
            'condition_text': fact['source_text'], 'condition_json': {
                'structure_kind': 'source_values_only', 'source_values': copy.deepcopy(fact['source_values']),
                'source_paragraph': fact['source_paragraph'], 'source_locator': copy.deepcopy(fact['source_locator']),
                'employee_leave_days': None}, 'applies_to_node_id': None,
            'review_status': 'source_text_verified_by_codex'})
        ledger['appended_rows']['regulation_scope'] += 1
    for connection in annual['context_connections']:
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == connection['target_chunk_id'])
        order = max(s['source_order'] for s in records['rag_chunk_source'] if s['rag_chunk_id'] == chunk['rag_chunk_id'])
        for node_id in connection['context_node_ids']:
            source_node = nodes[node_id]
            if source_node['regulation_version_id'] != chunk['regulation_version_id']:
                raise ValueError('Annual leave context belongs to a different version: ' + node_id)
            order += 1
            chunk['chunk_text'] += '\n\n관련 원문 ' + source_node['path'] + ':\n' + source_node['body_text']
            records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':annual-context:' + node_id),
                'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': node_id, 'source_order': order,
                'source_role': 'context', 'source_span': copy.deepcopy(source_node['source_locator'])})
            ledger['appended_rows']['rag_chunk_source'] += 1
            ledger['annual_leave_context_connections'].append({'rag_chunk_id': chunk['rag_chunk_id'], 'context_node_id': node_id})
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    remote = read(ROOT / '보정/유연근무제_원격직무_V01/원격직무_보정데이터.json')
    remote_parent = nodes[remote['source_regulation_node_id']]
    remote_body = ['[별표6] 원격근무 가능직무 (제28조제2항 관련)']
    for row in remote['rows']:
        node = add_node('flex:remote:' + row['row_key'], remote_parent, row['row_key'],
                        row['group'] + ' / ' + ', '.join(row['organization_labels']),
                        row['text'], row['source_locator'])
        add_chunk(node)
        add_condition(node, 'flex:remote:' + row['row_key'],
                      '적용대상(원문): ' + ' / '.join(row['applicable_labels']))
        remote_body.append(row['text'])
    replace_annex(remote['source_regulation_node_id'], '\n\n'.join(remote_body))
    for connection in remote['context_connections']:
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == connection['target_chunk_id'])
        order = max(s['source_order'] for s in records['rag_chunk_source'] if s['rag_chunk_id'] == chunk['rag_chunk_id'])
        for node_id in connection['context_node_ids']:
            source_node = nodes[node_id]
            if source_node['regulation_version_id'] != chunk['regulation_version_id']:
                raise ValueError('Flexible work context belongs to a different version: ' + node_id)
            order += 1
            chunk['chunk_text'] += '\n\n관련 원문 ' + source_node['path'] + ':\n' + source_node['body_text']
            records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk['rag_chunk_id'] + ':flex-context:' + node_id),
                'rag_chunk_id': chunk['rag_chunk_id'], 'regulation_node_id': node_id, 'source_order': order,
                'source_role': 'context', 'source_span': copy.deepcopy(source_node['source_locator'])})
            ledger['appended_rows']['rag_chunk_source'] += 1
            ledger['flexible_work_context_connections'].append({'rag_chunk_id': chunk['rag_chunk_id'], 'context_node_id': node_id})
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
    flexible_types = read(ROOT / '보정/유연근무제_유형별_V01/유형별_검토데이터.json')
    for rule in flexible_types['rules']:
        source_node = nodes[rule['source_regulation_node_id']]
        if (source_node['label'] != rule['source_article'] or
                source_node['source_locator']['pdf_pages'] != rule['source_pdf_pages'] or
                not all(term in source_node['body_text'] for term in rule['expected_source_terms'])):
            raise ValueError('유연근무 유형별 원문 대조 필요: ' + rule['rule_key'])
        records['regulation_scope'].append({
            'regulation_scope_id': uid('scope:' + rule['rule_key']),
            'regulation_node_id': source_node['regulation_node_id'],
            'scope_type': 'flexible_work_source_criterion',
            'condition_text': source_node['body_text'],
            'condition_json': {
                'structure_kind': 'source_values_only',
                'source_values': copy.deepcopy(rule['source_values']),
                'source_article': rule['source_article'],
                'source_locator': copy.deepcopy(source_node['source_locator']),
                'employee_eligibility_or_amount': None},
            'applies_to_node_id': None,
            'review_status': flexible_types['review_status']})
        ledger['appended_rows']['regulation_scope'] += 1
    salary_status = read(ROOT / '보정/일반직_신분변경보수_V01/신분변경_검토데이터.json')
    for rule in salary_status['rules']:
        source_node = nodes[rule['source_regulation_node_id']]
        if (source_node['label'] != rule['source_article'] or
                source_node['source_locator']['pdf_pages'] != rule['source_pdf_pages'] or
                not all(term in source_node['body_text'] for term in rule['expected_source_terms'])):
            raise ValueError('신분변경 보수 원문 대조 필요: ' + rule['rule_key'])
        records['regulation_scope'].append({
            'regulation_scope_id': uid('scope:' + rule['rule_key']),
            'regulation_node_id': source_node['regulation_node_id'],
            'scope_type': 'salary_status_source_criterion',
            'condition_text': source_node['body_text'],
            'condition_json': {
                'structure_kind': 'source_values_only',
                'source_values': copy.deepcopy(rule['source_values']),
                'source_article': rule['source_article'],
                'source_locator': copy.deepcopy(source_node['source_locator']),
                'personal_salary_amount': None},
            'applies_to_node_id': None,
            'review_status': salary_status['review_status']})
        ledger['appended_rows']['regulation_scope'] += 1
    salary = read(ROOT / '보정/일반직_보수검토_V01/보수_보정데이터.json')
    for repair in salary['misparsed_annex_continuations']:
        fragment = nodes[repair['fragment_node_id']]
        parent = nodes[repair['parent_article_node_id']]
        old_path = fragment['path']
        replace_annex(parent['regulation_node_id'], parent['body_text'] + '\n' + fragment['body_text'])
        parent['extraction_status'] = 'manual_corrected_structure'
        line_map = {line['line_id']: line for line in parent['source_locator']['lines'] + fragment['source_locator']['lines']}
        parent['source_locator']['lines'] = sorted(line_map.values(), key=lambda line: (line['page'], line['bbox'][1]))
        parent['source_locator']['pdf_pages'] = sorted(set(parent['source_locator']['pdf_pages'] + fragment['source_locator']['pdf_pages']))
        fragment['node_type'] = 'clause'
        fragment['label'] = '본문 연속부분'
        fragment['title'] = '별표 참조를 포함한 본문 연속부분'
        fragment['parent_node_id'] = parent['regulation_node_id']
        fragment['path'] = parent['path'] + ' > 본문 연속부분'
        fragment['extraction_status'] = 'manual_corrected_structure'
        fragment['review_note'] = '별표 제목이 아닌 부칙 제2조의 연속 문장으로 원문 대조 보정. 원래 추출은 provenance/base_tables.json에 보존.'
        for source in records['rag_chunk_source']:
            if source['source_role'] != 'primary':
                continue
            if source['regulation_node_id'] == parent['regulation_node_id']:
                chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == source['rag_chunk_id'])
                chunk['chunk_text'] = parent['path'] + '\n' + parent['body_text']
                chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
                source['source_span'] = copy.deepcopy(parent['source_locator'])
            elif source['regulation_node_id'] == fragment['regulation_node_id']:
                chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == source['rag_chunk_id'])
                if not chunk['chunk_text'].startswith(old_path + '\n'):
                    raise ValueError('Supplement continuation header mismatch.')
                chunk['context_path'] = fragment['path']
                chunk['chunk_text'] = fragment['path'] + chunk['chunk_text'][len(old_path):]
                chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
        ledger['supplement_continuations_corrected'].append(copy.deepcopy(repair))
    salary_parents = {}
    for parent_key, spec in salary['parents'].items():
        if 'existing_node_id' in spec:
            parent = nodes[spec['existing_node_id']]
            text = spec['source_locator']['original_extracted_text']
            replace_annex(parent['regulation_node_id'], text)
            parent['source_locator'] = locator(spec['source_locator'])
        else:
            parent = add_node(spec['new_node_key'], nodes[spec['document_node_id']],
                              spec['label'], spec['title'], spec['source_locator']['original_extracted_text'],
                              spec['source_locator'], 'annex')
        parent['effective_from_override'] = spec.get('effective_from_override')
        salary_parents[parent_key] = parent

    def salary_context(chunk_id, source_node, reason):
        chunk = next(c for c in records['rag_chunk'] if c['rag_chunk_id'] == chunk_id)
        if any(s['rag_chunk_id'] == chunk_id and s['regulation_node_id'] == source_node['regulation_node_id']
               for s in records['rag_chunk_source']):
            return
        order = max(s['source_order'] for s in records['rag_chunk_source'] if s['rag_chunk_id'] == chunk_id) + 1
        chunk['chunk_text'] += '\n\n관련 원문 ' + source_node['path'] + ':\n' + source_node['body_text']
        records['rag_chunk_source'].append({'rag_chunk_source_id': uid(chunk_id + ':salary-context:' + source_node['regulation_node_id']),
            'rag_chunk_id': chunk_id, 'regulation_node_id': source_node['regulation_node_id'], 'source_order': order,
            'source_role': 'context', 'source_span': copy.deepcopy(source_node['source_locator'])})
        ledger['appended_rows']['rag_chunk_source'] += 1
        ledger['salary_context_connections'].append({'rag_chunk_id': chunk_id,
            'context_node_id': source_node['regulation_node_id'], 'reason': reason})
        chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))

    merged_id = salary['original_merged_annex_node_id']
    for node in records['regulation_node']:
        if node['parent_node_id'] == merged_id and node['node_type'] == 'table':
            pdf_pages = node['source_locator']['pdf_pages']
            parent_key = 'duty_general' if pdf_pages == [28] else 'duty_operating' if pdf_pages == [29] else None
            if parent_key is None:
                continue
            old_path = node['path']
            node['parent_node_id'] = salary_parents[parent_key]['regulation_node_id']
            node['path'] = salary_parents[parent_key]['path'] + ' > ' + node['label']
            node['review_note'] = (node['review_note'] or '') + ' 원문 별표3의2·3의3 구분에 맞게 상위 항목을 보정.'
            ledger['salary_annex_boundary_corrections'].append({'regulation_node_id': node['regulation_node_id'],
                'old_path': old_path, 'corrected_path': node['path']})
            chunk_ids = {s['rag_chunk_id'] for s in records['rag_chunk_source']
                         if s['regulation_node_id'] == node['regulation_node_id'] and s['source_role'] == 'primary'}
            for chunk in records['rag_chunk']:
                if chunk['rag_chunk_id'] in chunk_ids:
                    if not chunk['chunk_text'].startswith(old_path + '\n'):
                        raise ValueError('Salary table context header does not match the original path.')
                    chunk['context_path'] = node['path']
                    chunk['chunk_text'] = node['path'] + chunk['chunk_text'][len(old_path):]
                    chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))

    for row in salary['money_rows']:
        parent = salary_parents[row['parent_key']]
        title = row['row_key']
        node = add_node('salary:' + row['row_key'], parent, row['row_key'], title, row['text'], row['source_locator'])
        add_chunk(node)
        records['regulation_scope'].append({'regulation_scope_id': uid('scope:salary:' + row['row_key']),
            'regulation_node_id': node['regulation_node_id'], 'scope_type': 'salary_source_amount',
            'condition_text': row['text'], 'condition_json': {'structure_kind': 'source_values_only',
                'amount_kind': row['amount_kind'], 'grading_kind': row['grading_kind'],
                'source_labels': copy.deepcopy(row['source_labels']), 'source_values': copy.deepcopy(row['source_values']),
                'derived_values': copy.deepcopy(row['derived_values']), 'actual_employee_salary': None},
            'applies_to_node_id': None, 'review_status': 'source_text_verified_by_codex'})
        ledger['appended_rows']['regulation_scope'] += 1
        chunk_id = uid(node['regulation_node_id'] + ':chunk')
        context_ids = ['04899d55-0842-5aec-bb8d-e2c936c09982', 'e858acaf-28c5-515e-afe6-3225542cf1be'] if row['parent_key'] == 'base_annex' else [salary['annex_application_date_source_node_id']]
        if row['parent_key'] == 'position_annex':
            context_ids.append(parent['regulation_node_id'])
        for context_id in context_ids:
            salary_context(chunk_id, nodes[context_id], 'amount_definition_or_date')

    for row in salary['overtime_rows']:
        node = add_node('salary:' + row['row_key'], nodes['9a368017-f9d9-53e0-b0b8-8ae459456733'],
                        row['row_key'], row['source_grade_labels'], row['text'], row['source_locator'])
        add_chunk(node)
        records['regulation_scope'].append({'regulation_scope_id': uid('scope:salary:' + row['row_key']),
            'regulation_node_id': node['regulation_node_id'], 'scope_type': 'overtime_payment_source_limit',
            'condition_text': row['text'], 'condition_json': {'structure_kind': 'source_values_only',
                'source_grade_labels': row['source_grade_labels'], 'source_monthly_payment_hours': row['source_monthly_payment_hours'],
                'actual_employee_payment': None}, 'applies_to_node_id': None, 'review_status': 'source_text_verified_by_codex'})
        ledger['appended_rows']['regulation_scope'] += 1
        salary_context(uid(node['regulation_node_id'] + ':chunk'), nodes['9a368017-f9d9-53e0-b0b8-8ae459456733'], 'monthly_and_weekly_rules')

    salary_context('14145863-4614-5f1d-8d6b-6b508b252b99', nodes['04899d55-0842-5aec-bb8d-e2c936c09982'], 'definitions_and_calculation')
    salary_context('8c8c9265-2e00-5844-9554-66c99f90fd92', nodes['16f9ceae-39cf-56d7-81d9-460a5b971bc6'], 'payment_and_period')
    salary_context('43436812-ed78-54e9-93e4-35827a88ce6f', salary_parents['duty_general'], 'general_staff_job_grade_ranges')
    salary_context('43436812-ed78-54e9-93e4-35827a88ce6f', salary_parents['duty_operating'], 'operating_staff_year_ranges')
    leave = read(ROOT / '보정/취업규칙_경조사_장기재직_V01/휴가_보정데이터.json')
    for prefix in ['event', 'outside']:
        parent = nodes[leave[prefix + '_parent_id']]
        text = leave[prefix + '_parent_source']['original_extracted_text']
        replace_annex(parent['regulation_node_id'], text)
        parent['source_locator'] = locator(leave[prefix + '_parent_source'])
        if prefix == 'event':
            parent['effective_from_override'] = leave['event_effective_from_override']
    for group in ['event', 'outside', 'long_service']:
        parent = nodes[leave[group + '_parent_id']]
        for row in leave[group + '_rows']:
            node = add_node('leave:' + row['row_key'], parent, row['row_key'], row['row_key'],
                            row['text'], row['source_locator'], 'clause' if group == 'long_service' else 'table_row')
            add_chunk(node)
            if group == 'long_service':
                node['effective_from_override'] = leave['long_service_source_application_date']
                records['rag_chunk'][-1]['chunk_type'] = 'article'
            values = copy.deepcopy(row.get('source_values') or {
                'service_group_text': row['service_group_text'], 'days': row['source_days'],
                'grant_frequency_text': row['source_frequency_text']})
            records['regulation_scope'].append({'regulation_scope_id': uid('scope:leave:' + row['row_key']),
                'regulation_node_id': node['regulation_node_id'], 'scope_type': 'leave_source_criterion',
                'condition_text': row['text'], 'condition_json': {'structure_kind': 'source_values_only',
                    'source_values': values, 'individual_leave_grant_or_deduction': None},
                'applies_to_node_id': None, 'review_status': 'source_text_verified_by_codex'})
            ledger['appended_rows']['regulation_scope'] += 1
            context_ids = ['93c6f83d-c653-5c28-8095-4a7bb90318a4', '7a0ac4bc-353e-5a59-8b78-e2c124a4c66c',
                           leave['event_date_source_node_id'], parent['regulation_node_id']] if group == 'event' else (
                ['ec5826a3-c8ce-5d81-a57a-4d0b2019f526'] if group == 'outside' else
                [leave['long_service_application_source_node_id'], 'ad736e89-249d-55fb-8b03-aba3acf20785'])
            chunk_id = uid(node['regulation_node_id'] + ':chunk')
            before = len(ledger['salary_context_connections'])
            for context_id in context_ids:
                salary_context(chunk_id, nodes[context_id], 'leave_related_original_text')
            added = ledger['salary_context_connections'][before:]
            ledger['leave_context_connections'].extend(added)
            del ledger['salary_context_connections'][before:]
    names = defaultdict(list)
    for reg in records['regulation']:
        names[re.sub(r'\s+', '', reg['name'])].append(reg['regulation_id'])
    for ref in records['regulation_reference']:
        if ref['reference_type'] != 'regulation' or ref['target_regulation_id'] is not None:
            continue
        target = re.sub(r'\s+', '', ref['target_text']).strip('「」')
        if len(names[target]) == 1:
            ref['target_regulation_id'] = names[target][0]
            ref['resolution_status'] = 'regulation_identified'
            ref['review_note'] += ' 통합 28건에서 규정명 유일 일치. 대상 개정본·조항·준용 범위는 미확정.'
            ledger['reference_links_added'].append(ref['regulation_reference_id'])

    byfile = defaultdict(list)
    for node in records['regulation_node']:
        byfile[node['regulation_file_id']].append(node)
    for group in byfile.values():
        ordered = sorted(group, key=lambda n: (min(n['source_locator'].get('pdf_pages') or [0]), n['display_order']))
        for i, node in enumerate(ordered):
            node['display_order'] = i
    affected_files = {nodes[x]['regulation_file_id'] for x in [annex2_id, annex3_id]}
    affected_files.add('bf01d765-9b8b-563d-a378-6f42aabc01a6')
    affected_files.add('57889d2e-a721-562c-8fcc-516bf9f58bf5')
    for file in records['regulation_file']:
        if file['regulation_file_id'] in affected_files:
            file['parse_status'] = 'extracted_with_manual_corrections'

    org_guidance = copy.deepcopy(read(ROOT / '보정/직제규정_기구_분장_V01/기구_분장_보정데이터.json')['user_reviews'])
    for candidate in org_guidance['department_names']:
        evidence = [n['regulation_node_id'] for n in records['regulation_node'] if
                    n['regulation_file_id'] == 'bf01d765-9b8b-563d-a378-6f42aabc01a6' and
                    candidate['candidate_name'] in n['body_text'] and n['extraction_status'] == 'manual_image_transcribed']
        candidate['normalized_name_applied_to_main_data'] = bool(evidence)
        candidate['applied_regulation_node_ids'] = evidence
    source_gap = read(ROOT / '보정/인재개발규정_제28조_원문확인_V01/제28조_원문확인.json')
    gap_node = nodes[source_gap['source_node_id']]
    gap_node['review_note'] = (gap_node['review_note'] or '') + ' 사용자 원본 확인: 후속 호 누락. 재선정 시 교육경비 회수 세부 기준은 원문 보완 필요.'
    for chunk in records['rag_chunk']:
        if chunk['rag_chunk_id'] in source_gap['related_chunk_ids']:
            chunk['chunk_text'] += ('\n\n전처리 검토 메모(규정 문구와 구분): 제28조제4항에서 예고한 후속 번호 목록은'
                                    ' 원본에서도 누락되어 있음. 재선정 시 교육경비 회수의 세부 기준은 원문 보완 또는 기관 확인 필요.'
                                    ' 제27조의 재직의무 원문만으로 누락된 회수 기준을 확정하지 않음.')
            chunk['content_hash'] = sha(chunk['chunk_text'].encode('utf-8'))
            ledger['source_gap_annotations'].append(chunk['rag_chunk_id'])
    for scope in records['regulation_scope']:
        text = re.sub(r'\s+', '', scope['condition_text'])
        if re.match(r'^제[0-9조의]+(?:\([^)]*\))?<본조삭제', text):
            scope['review_status'] = 'source_deleted_not_applicable'
            ledger['deleted_scope_annotations'].append(scope['regulation_scope_id'])
    guidance = {'source_type': 'answer_handling_with_source_evidence_and_user_reviews',
                'actual_employee_rules_implemented': False,
                 'priority_profile': copy.deepcopy(config['priority_profile']),
                'question_date_policy': config['question_date_policy'],
                'topic_retrieval_policy': config['topic_retrieval_policy'],
                'annex1': annex1['user_guidance'],
                'annex2': annex2['user_guidance'], 'annex3': annex3['user_guidance'],
                'org_duty': org_guidance,
                'source_gaps': [{'source_node_id': source_gap['source_node_id'],
                    'related_chunk_ids': source_gap['related_chunk_ids'], 'source_pdf_pages': [9],
                    'review_status': source_gap['review_status'], 'user_review': source_gap['user_review'],
                    'handling': source_gap['guidance'], 'user_hypothesis': source_gap['user_hypothesis']}]}
    guidance.update(read(ROOT / 'provenance/user_review_decisions.json'))
    guidance['flexible_work_type_details'] = flexible_types['answer_guidance']
    guidance['salary_status_and_leave_details'] = salary_status['answer_guidance']
    return records, dict(ledger), guidance


def validate(records, ledger):
    schema = read(ROOT / 'schema.json')
    original_records = read(ROOT / 'provenance/base_tables.json')
    original_nodes = {n['regulation_node_id']: n for n in original_records['regulation_node']}
    checks = []

    def check(name, condition):
        checks.append({'check': name, 'result': 'pass' if condition else 'fail'})

    check('단일 필드 계약·기관 1개·규정/개정본/파일 28개', schema['schema_name'] is None and
          len(schema['tables']) == 9 and sum(len(t['cols']) for t in schema['tables'].values()) == 85 and
          len(records['institution']) == 1 and records['institution'][0]['external_code'] == 'C0221' and
          all(len(records[t]) == 28 for t in ['regulation', 'regulation_version', 'regulation_file']))
    for table, spec in schema['tables'].items():
        columns = spec['cols']
        pk = next(c['n'] for c in columns if c['pk'])
        check(table + ' 필드·필수값·PK', all(set(row) == {c['n'] for c in columns} and
              all(row[c['n']] is not None or not c['nn'] for c in columns) for row in records[table]) and
              len({r[pk] for r in records[table]}) == len(records[table]))
    for src, field, dest, dest_field in schema['foreign_keys']:
        dest_values = {r[dest_field] for r in records[dest]}
        check(src + '.' + field + ' 외래키', all(r[field] is None or r[field] in dest_values for r in records[src]))
    check('원문 PDF28건 해시 보존', all(sha((ROOT / f['storage_key']).read_bytes()) == f['file_hash'] for f in records['regulation_file']))
    check('외부 비교자료를 규정 원문과 검색 출처에서 제외', all(
          f['storage_key'].startswith('source/') and 'external_sources' not in f['storage_key'] and
          f['source_type'] in {'alio_public', 'alio_user_supplied'}
          for f in records['regulation_file']))
    pages = {f['regulation_file_id']: len(read(ROOT / f'raw/pages/{f["regulation_file_id"]}.json')) for f in records['regulation_file']}
    check('원문861쪽과 항목 쪽수 연결', sum(pages.values()) == 861 and all(
          all(0 < p <= pages[n['regulation_file_id']] for p in n['source_locator']['pdf_pages']) for n in records['regulation_node']))
    check('검색 조각 해시·미활성 상태', all(sha(c['chunk_text'].encode('utf-8')) == c['content_hash'] and
          c['embedding_model'] is None and c['search_status'] in {'needs_review', 'not_for_answer', 'superseded'}
          for c in records['rag_chunk']))
    check('기존 1401개 검색 조각 보존', {c['rag_chunk_id'] for c in original_records['rag_chunk']} <=
          {c['rag_chunk_id'] for c in records['rag_chunk']})
    check('인재개발 별표2·3의 잘못된 자동 표 조각 대체 표시', bool(ledger['superseded_chunks']) and all(
          c['search_status'] == 'superseded' for c in records['rag_chunk'] if c['rag_chunk_id'] in ledger['superseded_chunks']))
    check('보정 본문11개의 원래 본문 해시 보존', len(ledger['replaced_nodes']) == 11 and all(
           sha(original_nodes[e['node_id']]['body_text'].encode('utf-8')) == e['original_body_sha256']
          for e in ledger['replaced_nodes']))
    check('직제 별표1·2·4 보정과 원문 페이지 연결', {n['label'] for n in records['regulation_node'] if
          n['regulation_file_id'] == 'bf01d765-9b8b-563d-a378-6f42aabc01a6' and n['node_type'] == 'annex'} >= {'별표1', '별표2', '별표4'})
    check('조건 원문을 자동 직원 판정으로 변환하지 않음', all(r['condition_json'] is None and r['applies_to_node_id'] is None
          for r in records['regulation_scope'] if r['scope_type'] == 'annex_condition'))
    check('2028년 교육대상자 선정 조항의 예외 시행일 유지', next(n for n in records['regulation_node'] if
          n['regulation_node_id'] == 'acb377a1-9f28-5330-a764-f4da13d290ab')['effective_from_override'] == '2028-01-01')
    node_map = {n['regulation_node_id']: n for n in records['regulation_node']}
    annex2 = read(ROOT / '보정/인재개발규정_별표2_V01/별표2_보정데이터.json')
    check('교육 과정명·분야·공란·원문 기호가 출력에 보존됨', all(
        requirement_content(req) in node_map[uid('talent:annex2:' + req['requirement_id'])]['body_text']
        and '원문 기준: None' not in node_map[uid('talent:annex2:' + req['requirement_id'])]['body_text']
        for req in annex2['requirements']))
    annex1 = read(ROOT / '보정/인재개발규정_별표1_V01/별표1_보정데이터.json')
    check('기준학습시간10열·주석4개·40% 원문 보존', len(annex1['rows']) == 10 and len(annex1['notes']) == 4
        and [r['annual_hours'] for r in annex1['rows']] == [32,48,64,80,112,48,48,80,48,48]
        and all(str(r['annual_hours']) + '시간' in node_map[uid('talent:annex1:' + r['row_key'])]['body_text']
                for r in annex1['rows'])
        and '40%' in node_map[uid('talent:annex1:S1-C02')]['body_text'])
    check('누락 원문 검토 메모를 검색 문장과 안내 기준에 연결', bool(ledger['source_gap_annotations']) and all(
        '전처리 검토 메모(규정 문구와 구분)' in c['chunk_text'] for c in records['rag_chunk']
        if c['rag_chunk_id'] in ledger['source_gap_annotations']))
    check('조의 순서 보정이 원문 제목과 일치하고 원문 내용·ID를 보존', bool(ledger['article_label_corrections']) and all(
        node_map[e['regulation_node_id']]['label'] == e['corrected_label'] and
        re.sub(r'\s+', '', node_map[e['regulation_node_id']]['body_text']).startswith(e['corrected_label']) and
        node_map[e['regulation_node_id']]['body_text'] == original_nodes[e['regulation_node_id']]['body_text']
        for e in ledger['article_label_corrections']))
    check('일반직 직종·직렬 원문6행과 병합 칸 보존',
        '전산' in node_map[uid('orgbylaw:annex4:J4-R06')]['body_text'] and
        '직종: 특수기술' in node_map[uid('orgbylaw:annex4:J4-R06')]['body_text'] and
        '직종: 행정' in node_map[uid('orgbylaw:annex4:J4-R03')]['body_text'] and
        '직종·직렬 병합 칸 원문: 행정, 기술' in node_map[uid('orgbylaw:annex4:J4-R02')]['body_text'] and
        all(node_map[uid('orgbylaw:annex4:J4-R' + str(i).zfill(2))]['source_locator']['pdf_pages'] == [46] for i in range(1,7)))
    promotion_scopes = [s for s in records['regulation_scope'] if s['scope_type'] == 'promotion_minimum_duration']
    check('승진 최소기간7개 원문값과 개인 판정 미구현 보존', len(promotion_scopes) == 7 and all(
        s['condition_json']['structure_kind'] == 'source_values_only' and
        s['condition_json']['employee_eligibility_decision'] is None and s['applies_to_node_id'] is None and
        s['condition_text'] in node_map[s['regulation_node_id']]['body_text'] for s in promotion_scopes))
    check('최소 승진기간과 승진 원칙·방법·제한의 출처 연결',
        {e['context_node_id'] for e in ledger['promotion_context_connections']} == {
            'bf3ccb52-c795-574e-8095-eab5e38a64d4', 'd7f8bdc1-48ac-58dd-b4a7-82f665ced7d3', '981142fb-5140-5892-ae05-a6c95c82bcee'})
    annual_scopes = [s for s in records['regulation_scope'] if s['scope_type'] == 'annual_leave_source_criterion']
    check('연차 기준6개 원문 보존과 개인 일수 미계산', len(annual_scopes) == 6 and all(
        s['condition_json']['structure_kind'] == 'source_values_only' and
        s['condition_json']['employee_leave_days'] is None and s['applies_to_node_id'] is None and
        s['condition_text'] in node_map[s['regulation_node_id']]['body_text'] and
        s['condition_json']['source_paragraph'] != '③' for s in annual_scopes))
    check('연차 부여·기간 계산·저축 원문의 출처 연결', len(ledger['annual_leave_context_connections']) == 3 and
        {e['context_node_id'] for e in ledger['annual_leave_context_connections']} == {
            'ad736e89-249d-55fb-8b03-aba3acf20785', '7a0ac4bc-353e-5a59-8b78-e2c124a4c66c',
            '84344702-e2b1-5c41-b293-91c7744e8a92'})
    check('원격근무 가능직무6행의 표 보정과 원문 쪽수 연결', all(
        uid('flex:remote:REMOTE-JOB-' + str(i).zfill(2)) in node_map and
        node_map[uid('flex:remote:REMOTE-JOB-' + str(i).zfill(2))]['source_locator']['pdf_pages'] == [18]
        for i in range(1, 7)))
    check('유연근무 신청·승인과 유형별 조건의 출처 연결',
        len(ledger['flexible_work_context_connections']) == 8 and
        'c826c33b-76d8-52b3-958b-5c768deb9deb' in {
            e['context_node_id'] for e in ledger['flexible_work_context_connections']})
    flexible_types = read(ROOT / '보정/유연근무제_유형별_V01/유형별_검토데이터.json')
    flexible_scopes = [s for s in records['regulation_scope']
                       if s['scope_type'] == 'flexible_work_source_criterion']
    check('유연근무 유형별11개 원문 조건과 개인 판정 미구현',
        len(flexible_scopes) == len(flexible_types['rules']) == 11 and all(
            s['condition_json']['structure_kind'] == 'source_values_only' and
            s['condition_json']['employee_eligibility_or_amount'] is None and
            s['applies_to_node_id'] is None and
            s['condition_text'] == node_map[s['regulation_node_id']]['body_text']
            for s in flexible_scopes))
    compressed = next(s for s in flexible_scopes if s['regulation_node_id'] == 'b2119213-9c57-520c-a105-1ca39c7dcc8c')
    check('집약근무 신청자격과 별표4 직무 예시 구분',
        compressed['condition_json']['source_values']['applicant_groups'] == ['업무지원직', '해외사업장 근무자'] and
        compressed['condition_json']['source_values']['job_examples_alone_establish_eligibility'] is False and
        node_map['894e60e7-afcd-5cb3-8859-8c97f2cc1d65']['source_locator']['pdf_pages'] == [16])
    salary_status = read(ROOT / '보정/일반직_신분변경보수_V01/신분변경_검토데이터.json')
    status_scopes = [s for s in records['regulation_scope']
                     if s['scope_type'] == 'salary_status_source_criterion']
    check('신분변경·휴직 보수8개 원문 조건과 개인 금액 미계산',
        len(status_scopes) == len(salary_status['rules']) == 8 and all(
            s['condition_json']['structure_kind'] == 'source_values_only' and
            s['condition_json']['personal_salary_amount'] is None and
            s['applies_to_node_id'] is None and
            s['condition_text'] == node_map[s['regulation_node_id']]['body_text']
            for s in status_scopes))
    leave_pay = next(s for s in status_scopes if s['regulation_node_id'] == '5f5aef8e-0b0c-5a4a-81a4-ec6d280919d4')
    check('휴직 보수 일반 원칙과 70·50퍼센트 예외 구분',
        leave_pay['condition_json']['source_values']['illness_or_infertility_base_pay_percent_first_year'] == 70 and
        leave_pay['condition_json']['source_values']['illness_or_infertility_base_pay_percent_second_year'] == 50 and
        leave_pay['condition_json']['source_values']['same_reason_illness_and_infertility_periods_combined'] is True)
    salary = read(ROOT / '보정/일반직_보수검토_V01/보수_보정데이터.json')
    salary_scopes = [s for s in records['regulation_scope'] if s['scope_type'] == 'salary_source_amount']
    check('보수 원문값39개와 고정액·상하한액·개인 금액 미확정 구분', len(salary_scopes) == 39 and all(
        s['condition_json']['structure_kind'] == 'source_values_only' and
        s['condition_json']['actual_employee_salary'] is None and s['applies_to_node_id'] is None
        for s in salary_scopes) and sum(s['condition_json']['amount_kind'] == 'fixed_amount' for s in salary_scopes) == 8)
    check('천원 원문 단위와 원 환산·원문 금액 대조', all(
        (row['derived_values']['amount_krw'] == row['source_values']['amount'] * 1000 and
         row['amount_source_text'] in row['source_locator']['original_extracted_text'])
        if row['amount_kind'] == 'fixed_amount' else
        (row['derived_values']['upper_krw'] == row['source_values']['upper'] * 1000 and
         row['derived_values']['lower_krw'] == row['source_values']['lower'] * 1000 and
         row['upper_source_text'] in row['source_locator']['original_extracted_text'] and
         row['lower_source_text'] in row['source_locator']['original_extracted_text'])
        for row in salary['money_rows']))
    check('별표3·3의2·3의3 원문 페이지와 자동 표 상위 항목 보정',
        node_map['3dc297f0-3c9e-558d-bce7-f9cfc7d3229e']['source_locator']['pdf_pages'] == [27] and
        node_map[uid('salary:annex3-2')]['source_locator']['pdf_pages'] == [28] and
        node_map[uid('salary:annex3-3')]['source_locator']['pdf_pages'] == [29] and
        len(ledger['salary_annex_boundary_corrections']) == 3)
    check('별표 적용일과 운영직2025·2026 표기 구분 보존',
        all(node_map[node_id]['effective_from_override'] == '2025-01-01' for node_id in
            ['3dc297f0-3c9e-558d-bce7-f9cfc7d3229e', uid('salary:annex3-2'), uid('salary:annex3-3')]) and
        {r['source_values']['application_year'] for r in salary['money_rows'] if r['parent_key'] == 'duty_operating'} == {2025, 2026})
    check('통상근무자 월 지급한도9·15시간과 개인 금액 미계산',
        [r['source_monthly_payment_hours'] for r in salary['overtime_rows']] == [9, 15] and all(
        s['condition_json']['actual_employee_payment'] is None for s in records['regulation_scope']
        if s['scope_type'] == 'overtime_payment_source_limit'))
    leave = read(ROOT / '보정/취업규칙_경조사_장기재직_V01/휴가_보정데이터.json')
    check('경조사8행·출산20괄호25·입양 주석 보존', len(leave['event_rows']) == 8 and
        leave['event_rows'][2]['printed_days'] == '20 (25)' and
        all(uid('leave:' + row['row_key']) in node_map for row in leave['event_rows']) and
        '입양특례법' in node_map[uid('leave:EVENT-LEAVE-08')]['body_text'])
    check('경조사 적용일과 원문 주석·기간 계산의 연결',
        node_map[leave['event_parent_id']]['effective_from_override'] == '2025-01-06' and
        node_map[leave['event_parent_id']]['source_locator']['pdf_pages'] == [27] and
        len([e for e in ledger['leave_context_connections'] if e['context_node_id'] == '7a0ac4bc-353e-5a59-8b78-e2c124a4c66c']) == 8)
    check('사무외출 병가·연차·15분과 제26조 예외 연결',
        [r['source_values']['offset_balance'] for r in leave['outside_rows']] == ['병가', '연차'] and
        all(r['source_values']['usage_unit_minutes'] == 15 for r in leave['outside_rows']) and
        len([e for e in ledger['leave_context_connections'] if e['context_node_id'] == 'ec5826a3-c8ce-5d81-a57a-4d0b2019f526']) == 2)
    check('장기재직5·7일·1회·시행당시 적용례와 개인 판정 미구현',
        [r['source_days'] for r in leave['long_service_rows']] == [5, 7] and
        all(node_map[uid('leave:' + r['row_key'])]['effective_from_override'] == '2025-07-22' for r in leave['long_service_rows']) and
        all(s['condition_json']['individual_leave_grant_or_deduction'] is None for s in records['regulation_scope']
            if s['scope_type'] == 'leave_source_criterion'))
    check('부칙 본문의 별표 참조 오분리2건과 원문 연속 문장 복원',
        len(ledger['supplement_continuations_corrected']) == 2 and all(
        node_map[repair['fragment_node_id']]['node_type'] == 'clause' and
        node_map[repair['fragment_node_id']]['parent_node_id'] == repair['parent_article_node_id'] and
        node_map[repair['fragment_node_id']]['body_text'] in node_map[repair['parent_article_node_id']]['body_text']
        for repair in ledger['supplement_continuations_corrected']))
    return {'result': 'pass' if all(c['result'] == 'pass' for c in checks) else 'fail', 'check_count': len(checks),
            'checks': checks, 'counts': {name: len(rows) for name, rows in records.items()},
            'search_statuses': dict(Counter(c['search_status'] for c in records['rag_chunk'])),
            'unverified': ['실제 DB 적재', '임베딩·실제 RAG 검색/답변', '직원별 적용 및 점수 계산', '대기 원문과 상세 표 의미']}


def priority_source_checks(records: dict) -> dict:
    nodes = {n['regulation_node_id']: n for n in records['regulation_node']}
    files = {f['regulation_file_id']: f for f in records['regulation_file']}
    specs = [
        ('필수교육', '일반직 4급의 직무역량 필수교육은 무엇인가?',
         ['84a1a35f-19e0-5b32-b937-a12d0c5ee399', '50681d96-4f4d-512f-81d4-af5157748f9a'],
         ['직무이해(Ⅱ)', '수자원(기본)', '수도(기본)', 'Green·Digital·Global(기본)', '경영(기본)'],
         '직급별 기준과 관련 주석을 함께 확인. 개인 이력이 없으면 개인의 이수 의무를 확정하지 않음.'),
        ('교육 이수기한', '리더교육을 언제까지 이수해야 하는가?',
         ['fb1ff624-1de3-5383-ad6b-194583b1c0c6', 'a00b163b-6769-5b86-ac9b-096d07413fc9'],
         ['10월', '31일'], '임명·호칭 변경일과 휴직·복귀·승인 기록이 없으면 정확한 기한 확인 필요.'),
        ('승진 관련 교육', '마지막 승진일이 공란이면 해당 교육이 면제되는가?',
         ['50681d96-4f4d-512f-81d4-af5157748f9a'], ['2019', '2010'],
         '미승진자의 공란과 기록 미확인을 구분. 공란만으로 교육 면제를 확정하지 않음.'),
        ('교육훈련평정', '교육 이력 정보가 부족하면 교육훈련평정이 0점인가?',
         ['cb821674-e14e-55e5-9b7d-46d87e608fcc', '8bb918d8-52b2-59bd-9b88-5463d2fee074',
          'a85a82a1-3d31-5a66-a6b9-499b466eba98', '52f40d18-c375-5987-a172-5d8925d69c0f'],
         ['0.6', '0.4', '총학습시간', '필수교육'],
         '정보 부족은 0점으로 계산하지 않고 점수 확인 필요. 평정대상·인정시간·대상 과정 수 등을 확인.'),
        ('연간 기준학습시간', '일반직 5급의 기준학습시간과 담당부서 학습의무 비율은?',
         [uid('talent:annex1:S1-R05'), uid('talent:annex1:S1-C01'), uid('talent:annex1:S1-C02')],
         ['112시간', '200시간', '40%'], '원문 비율 보존. 112시간의 40%인 44.8시간을 예시로 계산하되 임의 반올림 없음.'),
        ('부서와 교육 분야', '디지털관리처·인재경영실 소속만으로 신입교육 분야를 정할 수 있는가?',
         ['5228c34d-1fed-5d1f-a44f-29b2ec514900', '61b58b93-b9a8-5c6b-8876-b1292a5c7e5a', uid('talent:annex3:S3-C02')],
         ['디지털관리처', '인재경영실', '직무분류체계'],
         '부서와 교육 분야를 자동 연결하지 않음. 기관 직무분류·교육 대상 안내와 직원 정보를 대조.'),
        ('직종·직렬', '일반직 3~8급의 전산·행정 직렬은 어떤 직종으로 기재되어 있는가?',
         [uid('orgbylaw:annex4:J4-R06'), uid('orgbylaw:annex4:J4-R03')],
         ['3~8급', '직종: 특수기술', '전산', '직종: 행정'],
         '원문 직급 범위와 직종·직렬을 보존. 가상 직원의 예비 직무 설정과 실제 직원의 인사기록을 구분.'),
        ('평정점수 자리수', '평정점수의 소수점 자릿수 처리 기준은?',
         ['b235be36-28ea-52ee-8b7b-b969557db260'], ['3자리', '4자리수', '절사'],
         '원문 점수 처리 기준을 보존. 이 기준을 학습시간의 반올림 기준으로 사용하지 않음.'),
        ('최소 승진기간', '4급으로 3년 근무하면 승진이 확정되는가?',
         ['f80ca70b-6800-59aa-8536-acd61cc23e8f', 'bf3ccb52-c795-574e-8095-eab5e38a64d4',
          'd7f8bdc1-48ac-58dd-b4a7-82f665ced7d3', '981142fb-5140-5892-ae05-a6c95c82bcee'],
         ['4급에서 3급 : 3년', '5급에서 4급 : 2년', '결원', '승진대상에서 제외'],
         '최소 기간 충족과 실제 승진 여부를 구분. 결원·평정·승진 제한·임용방법 등을 함께 확인.'),
        ('전산직렬 현장근무 예외', '전산직렬 직원은 현장근무기간 요건에 어떤 예외가 있는가?',
         ['576c2d2f-665d-5e30-900e-80334524228f', 'f80ca70b-6800-59aa-8536-acd61cc23e8f',
          '981142fb-5140-5892-ae05-a6c95c82bcee'], ['전산직렬', '1년 6개월', '2년 6개월', '제1항'],
         '전산직렬이 확인되면 제48조의2제1항 현장근무기간 요건의 예외를 안내. 다른 승진 조건의 면제로 확대하지 않음.'),
        ('휴직과 승진기간', '휴직기간을 모두 승진소요최저연수에 더해야 하는가?',
         ['981142fb-5140-5892-ae05-a6c95c82bcee', '91ba6090-f334-5064-a470-c31205adb08a'],
         ['휴직기간', '국외 교육훈련', '제31조'],
         '휴직 사유·기간과 원문 예외를 확인. 정보가 부족하면 개인의 승진기간 확인 필요.'),
        ('경력직 현장근무 예외', '경력직 채용자의 현장근무기간 예외는 계속 유지되는가?',
         ['576c2d2f-665d-5e30-900e-80334524228f'], ['경력직으로 채용된 직원', '채용시 직급', '4,5급을 동일직급'],
         '채용 유형·채용 당시 직급·현재 직급을 확인하여 채용 직급 범위로 안내. 모든 이후 직급의 면제로 확대하지 않음.'),
        ('연차 부여 원문', '연차휴가는 모든 직원에게 일률적으로 15일인가?',
         ['ad736e89-249d-55fb-8b03-aba3acf20785'], ['80퍼센트', '15일', '1개월간', '25일'],
         '출근율·계속 근로기간 등의 원문 조건과 상한을 보존. 개인 부여·잔여 연차는 실제 이력 확인 필요.'),
        ('휴가기간 계산 예외', '30일 이상 휴가는 항상 토요일·공휴일을 차감 일수에 포함하는가?',
         ['7a0ac4bc-353e-5a59-8b78-e2c124a4c66c'], ['30일', '연차유급휴가', '그러하지 아니하다'],
         '제1·2항과 연차유급휴가 예외를 함께 보존·안내. 개인 차감 일수는 휴가 종류·기간 확인 후 안내.'),
        ('연차저축 별도 절차', '남은 연차휴가를 저축하는 신청 절차는 무엇인가?',
         ['84344702-e2b1-5c41-b293-91c7744e8a92'], ['이월·저축', '보상비', '노사가 합의'],
         '원문 저축 기준을 보존. 사용자가 별도 자료 미보유를 확인했으므로 상세 대상·절차는 기관 자료 확인 대기.'),
        ('유연근무 신청·승인', '일반직 팀원이 유연근무를 신청하면 자동으로 승인되는가?',
         ['86336958-bb53-569c-8f53-053cf171981c', 'bb449b33-dbd2-5d22-a31e-238f40b7a927',
          '0b43fcea-6580-5e23-91b9-e57322523ca5'], ['업무공백', '승인하여야', '매월 말일까지', '근무시간선택제'],
         '신청과 승인을 구분하고 업무공백·부서운영 조건과 원문 승인 기준을 함께 보존. 부서장 아닌 팀원 계획만으로 신청 가능 여부를 확정하지 않음.'),
        ('유연근무 유형별 기간', '모든 유연근무는 반드시 1개월 단위인가?',
         ['47f281ae-98c5-53e5-b0bf-f3c47a7c353a', '2d80c04a-844e-58ae-b53c-d18bfa5e08fa'],
         ['1개월 단위', '1주일 단위', '1일 단위', '09:00~18:00'],
         '실시기간의 일반 기준과 원격근무 유형별 기간을 구분. 신청 단위와 신청 마감일을 같은 의미로 사용하지 않음.'),
        ('시간선택제 근무·보수·재직기간', '시간선택제로 전환하면 급여와 승진 재직기간이 모두 같은 비율로 줄어드는가?',
         ['98da1a34-4717-501a-992d-92037dc59344', '87c11209-8839-5c90-94c3-067fbfe99e5b',
          '7c5fa879-5e49-52d3-922b-8f07fb55caa2', '15cf9b6f-3c9f-5e6a-9cda-f5c798162947'],
         ['15시간', '35시간', '근무시간에 비례', '최초 1년', '100%'],
         '근무시간·보수·연차·재직기간의 별도 기준을 구분. 임산부 단축근로의 급여 예외와 채용형 시간선택제의 재직기간 예외를 함께 확인.'),
        ('집약근무 신청자격', '본사 일반직 전산 업무가 별표4 예시에 있으므로 집약근무를 신청할 수 있는가?',
         ['b2119213-9c57-520c-a105-1ca39c7dcc8c', '894e60e7-afcd-5cb3-8859-8c97f2cc1d65',
          'd94d4016-3876-595d-a411-17d35eaeace1'],
         ['업무지원직', '해외사업장 근무자', '전산시스템 운영', '11시간 이하'],
         '제23조 신청자격을 먼저 확인. 별표4의 직무 예시는 신청자격을 확대하지 않으며 개인의 실제 직군·근무지를 확인.'),
        ('근무시간선택제 공동근무시간', '주 5일과 격주 4일 근무의 공동근무시간 조건이 같은가?',
         ['01c13945-fa42-5b77-aabf-e2de892c470e'],
         ['주 5일 근무', '격주 4일 근무', '또는', '및', '매월 초일부터 말일까지'],
         '주 5일은 두 시간대 중 하나, 격주 4일은 두 시간대 모두 포함. 근무시간·월 정산과 임금피크 예외를 함께 확인.'),
        ('육아기 원격근무 조건', '자녀가 있으면 매일 원격근무를 할 수 있는가?',
         ['2d80c04a-844e-58ae-b53c-d18bfa5e08fa', 'd07456fb-1c69-52d5-8c9f-94edc3100327',
          'b97770f3-0b5d-5e5a-915a-3504fa0a5af3'],
         ['1일은 반드시 사무실에 출근', '만 8세 이하', '초등학교 2학년 이하', '분장업무의 적합성'],
         '자녀 나이·학년과 직무 적합성, 주1회 사무실 출근, 부서장 승인을 확인. 월 시간의 숫자는 이 조문에서 임의로 만들지 않음.'),
        ('유연근무 해제', '전보나 휴직 후 기존 유연근무 승인이 자동 유지되는가?',
         ['ec84e75b-e42b-5c7f-acbb-6a9155903844'],
         ['실시기간의 만료', '전보발령', '휴직'],
         '제7조의 당연 해제 사유와 실제 전보·휴직 이력을 확인. 새로운 승인 여부를 추정하지 않음.'),
        ('원격근무 가능직무', '디지털관리처 또는 인재경영실 소속이면 원격근무가 가능한가?',
         [uid('flex:remote:REMOTE-JOB-01'), uid('flex:remote:REMOTE-JOB-04'), '2d80c04a-844e-58ae-b53c-d18bfa5e08fa'],
         ['프로그램 개발 프로젝트 관리', '시스템 유지보수', '보안위험', '부적합'],
         '별표6의 실제 직무·조직 구분·적용대상과 제28조의 효율성·보안·장소·고객접점 조건 및 승인을 대조. 부서명만으로 원격근무 가능 여부를 확정하지 않음.'),
        ('월 수당한도·주간 제한', '일반직4급은 한 달에 15시간만 시간외근무할 수 있는가?',
         ['9a368017-f9d9-53e0-b0b8-8ae459456733', uid('salary:OVERTIME-02')],
         ['15h', '주 12시간', '예산 범위'],
         '월 수당 지급한도와 주간 근무 제한을 구분. 제3항 특별한 경우의 초과 실시 문구도 함께 확인.'),
        ('연봉 용어와 산정', '연봉월액과 기본월봉은 같은 금액인가?',
         ['04899d55-0842-5aec-bb8d-e2c936c09982', 'e858acaf-28c5-515e-afe6-3225542cf1be'],
         ['연봉월액', '기본월봉', '표준가산급'],
         '제2조 정의와 제11조 산정 문구를 함께 보존. 표준가산급을 별도 지급액으로 임의 추가하지 않으며 개인 금액·세후 입금액은 확인 필요.'),
        ('보수 계산기간', '이번 달 실적급여는 어느 달의 실적인가?',
         ['96400497-2138-5e39-a7ce-7dcccce085a3', '16f9ceae-39cf-56d7-81d9-460a5b971bc6'],
         ['21일', '공휴일', '전월 1일부터'],
         '매월 지급일과 연봉·실적급여의 계산기간을 구분하여 원문으로 안내.'),
        ('신분변경과 일할 계산', '월 중 복직·승진·전보하면 모두 발령일부터 같은 방식으로 연봉을 계산하는가?',
         ['e805dbcf-5dd4-5644-893b-4121ee4df85d'],
         ['복직', '발령일로부터', '15일 이전', '16일 이후', '직무등급'],
         '복직·징계 등 발령일 일할 기준과 승진·직무등급 변경의 15일 경계를 구분. 퇴직월 예외는 별도로 확인.'),
        ('징계처분별 연봉', '직위해제·정직·감봉 때 연봉이 모두 똑같이 줄어드는가?',
         ['3174d853-4ee6-5ba8-9c9f-1ff82c70708f'],
         ['직위해제', '기준급만', '정직', '60분의 1', '소급'],
         '처분별 지급 항목과 예외, 무효·취소 시 소급 지급을 구분. 처분 사유·기간·확정 상태 없이 개인 금액을 계산하지 않음.'),
        ('휴직 사유별 보수', '휴직 중에는 언제나 연봉이 없거나 기준급의 70퍼센트인가?',
         ['5f5aef8e-0b0c-5a4a-81a4-ec6d280919d4'],
         ['지급하지 아니하되', '70%', '50%', '공상 치료', '국외 교육훈련', '난임'],
         '일반 미지급 원칙과 사유·기간별 예외를 구분. 성과급과 실적급여는 별도 기준으로 안내.'),
        ('휴직·징계 성과급', '휴직 기간에도 성과급을 정상 금액으로 받는가?',
         ['5c56482e-fc9c-538a-9dbc-b743fee5ef90', 'dd303071-d7a6-5281-ba9a-ae0385cb2205'],
         ['지급제한일수', '59/60', '휴직', '일반직 2급(을)', '퇴직월'],
         '경영평가·내부평가급과 급여성 성과급의 대상·산식을 구분. 직급·근무일수·처분 이력 없이 개인 금액을 계산하지 않음.'),
        ('직무급 범위와 금액 단위', '일반직 직무등급A의 표 금액이 개인의 확정 연봉인가?',
         [uid('salary:DUTY-일반직-SOURCE-A'), 'd5539adb-e927-5a27-9547-696d321ec56f'],
         ['17,352', '14,520', '천원/년', '17,352,000원'],
         '상하한 범위·천원 단위와 원 환산을 구분. 직무등급 연결 자료가 없어 직원별 직무급과 전체 연봉은 확정하지 않음.'),
        ('경조사휴가 일수', '배우자 출산과 본인 입양의 휴가일수는?',
         [uid('leave:EVENT-LEAVE-03'), uid('leave:EVENT-LEAVE-08'), '4f2289a8-4c2f-51a1-aca7-bb86c367f7bc'],
         ['20 (25)', '둘 이상의 자녀', '입양특례법', 'K-water'],
         '표의 일수·괄호 조건·주석을 함께 보존. 개인 신청기한·분할 사용은 별도 가이드와 사유·발생일 확인 필요.'),
        ('경조사 기간과 가산', '경조사휴가에 토요일·공휴일과 장거리 이동을 어떻게 반영하는가?',
         ['93c6f83d-c653-5c28-8095-4a7bb90318a4', '7a0ac4bc-353e-5a59-8b78-e2c124a4c66c'],
         ['300km', '1일', '토요일', '연차유급휴가'],
         '제31조의2 거리·해외근무 가산과 제33조의3 기간 계산 원문을 함께 보존. 개인 일정은 실제 사유·기간·근무지 정보 확인 필요.'),
        ('장기재직휴가 적용례', '20년 재직자에게 장기재직휴가5일과7일을 항상 더하는가?',
         [uid('leave:LONG-10-20'), uid('leave:LONG-20'), '4efad793-01af-5e6b-a959-26bd34edf145'],
         ['5일', '7일', '1회', '2025년 7월 22일', '별도로 부여하지 않는다'],
         '2025-07-22 시행 당시 적용례와 원문 구간·횟수를 함께 안내. 이후 구간 변경 재부여는 사용 이력 및 기관 운영 기준 확인.'),
        ('사무외출 차감', '병원 진료 등 사무외출은 어떤 휴가에서 차감하는가?',
         [uid('leave:OUTSIDE-01'), uid('leave:OUTSIDE-02'), 'ec5826a3-c8ce-5d81-a57a-4d0b2019f526'],
         ['병가', '연차', '15분', '진단서'],
         '사무외출·공무외출을 구분하고 별표2의 차감 기준과 제26조 진단서 예외를 함께 확인. 개인 잔여일수는 계산하지 않음.')
    ]
    cases = []
    for topic, question, ids, terms, handling in specs:
        evidence = []
        for node_id in ids:
            node = nodes[node_id]
            evidence.append({'regulation_node_id': node_id,
                'source_file_name': files[node['regulation_file_id']]['file_name'],
                'pdf_pages': node['source_locator']['pdf_pages'], 'body_sha256': sha(node['body_text'].encode('utf-8'))})
        text = '\n'.join(nodes[i]['body_text'] for i in ids)
        passed = all(term in text for term in terms)
        cases.append({'topic': topic, 'representative_question': question, 'source_evidence': evidence,
            'expected_source_terms': terms, 'accepted_answer_handling': handling,
            'result': 'source_connection_pass' if passed else 'fail', 'actual_employee_inputs_available': False,
            'actual_search_answer_test_run': False})
    for case in cases:
        if case['topic'] in {'평정점수 자리수', '연차 부여 원문', '유연근무 신청·승인', '유연근무 유형별 기간',
                            '시간선택제 근무·보수·재직기간', '집약근무 신청자격', '근무시간선택제 공동근무시간',
                            '육아기 원격근무 조건', '유연근무 해제', '원격근무 가능직무',
                            '연봉 용어와 산정', '보수 계산기간', '신분변경과 일할 계산',
                            '징계처분별 연봉', '휴직 사유별 보수', '휴직·징계 성과급', '직무급 범위와 금액 단위'}:
            case['source_transcription_handling'] = case.pop('accepted_answer_handling')
            case['handling_review_status'] = 'source_text_verified_by_codex'
        elif case['topic'] in {'경조사휴가 일수', '경조사 기간과 가산', '사무외출 차감'}:
            case['source_transcription_handling'] = case.pop('accepted_answer_handling')
            case['handling_review_status'] = 'source_text_verified_by_codex'
        elif case['topic'] == '연차저축 별도 절차':
            case['material_availability_handling'] = case.pop('accepted_answer_handling')
            case['handling_review_status'] = 'material_availability_confirmed_by_user'
    return {'method': '원문 항목·쪽수·내용 해시와 필요한 과정명·수치·산식의 정적 대조',
        'priority_profile': copy.deepcopy(read(ROOT / 'delivery_config.json')['priority_profile']),
        'result': 'pass' if all(c['result'] == 'source_connection_pass' for c in cases) else 'fail',
        'case_count': len(cases), 'cases': cases, 'actual_rag_search_test_run': False, 'actual_employee_evaluation_run': False}


def main():
    records, ledger, guidance = build()
    report = validate(records, ledger)
    if report['result'] != 'pass':
        write(ROOT / 'report/validation.json', report)
        raise RuntimeError([c['check'] for c in report['checks'] if c['result'] == 'fail'])
    priority = priority_source_checks(records)
    priority['cases'][3]['eligibility_handling'] = guidance['education_assessment_eligibility']
    priority['cases'][3]['assessment_basis_handling'] = guidance['education_assessment_basis']
    priority['cases'][3]['components_exception_handling'] = guidance['education_assessment_components_exception']
    if priority['result'] != 'pass':
        raise RuntimeError('우선 상담 근거 검사 실패: ' + str([c['topic'] for c in priority['cases'] if c['result'] == 'fail']))
    schema = read(ROOT / 'schema.json')
    for name, rows in records.items():
        write(ROOT / f'data/{name}.json', rows)
        with (ROOT / f'data/{name}.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=[c['n'] for c in schema['tables'][name]['cols']])
            writer.writeheader()
            for row in rows:
                writer.writerow({k: r'\N' if v is None else ('true' if v else 'false') if isinstance(v, bool)
                                 else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for k, v in row.items()})
    write(ROOT / 'report/integration_ledger.json', ledger)
    write(ROOT / 'answer_guidance.json', guidance)
    write(ROOT / 'report/validation.json', report)
    write(ROOT / 'report/general_staff_priority_checks.json', priority)
    manifest = read(ROOT / 'report/manifest.json')
    manifest['counts'] = report['counts']
    manifest['priority_profile'] = copy.deepcopy(read(ROOT / 'delivery_config.json')['priority_profile'])
    manifest['superseded_chunks'] = len(ledger['superseded_chunks'])
    manifest['article_labels_corrected_from_original_headings'] = len(ledger['article_label_corrections'])
    manifest['source_gap_chunks_annotated'] = len(ledger['source_gap_annotations'])
    manifest['deleted_applicability_records_marked'] = len(ledger['deleted_scope_annotations'])
    manifest['validation']['structure_checks'] = report['check_count']
    manifest['validation']['general_staff_source_cases'] = priority['case_count']
    manifest['build_code_sha256'] = sha(Path(__file__).read_bytes())
    write(ROOT / 'report/manifest.json', manifest)
    write(ROOT / 'report/rebuild_check.json', {'result': 'not_run_for_current_output',
        'build_code_sha256': manifest['build_code_sha256'], 'note': '다시 생성한 현재 파일에 대한 반복 생성 일치 검사 결과로 갱신해야 함.'})
    print(json.dumps({'result': report['result'], 'checks': report['check_count'], 'counts': report['counts'],
                      'superseded_chunks': len(ledger['superseded_chunks']),
                      'new_reference_links': len(ledger['reference_links_added'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
