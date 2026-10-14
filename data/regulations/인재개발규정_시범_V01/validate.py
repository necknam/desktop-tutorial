"""가공 결과의 원문 연결, 경계·누락 및 임시 DB 적재를 검증한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


def validate_package(output: Path, schema: dict, profile: dict) -> dict:
    from preprocess import data_sql, read_json, schema_sql

    records = {name: read_json(output / 'data' / f'{name}.json') for name in schema['tables']}
    pages = read_json(output / 'raw' / 'pages.json')
    tables = read_json(output / 'raw' / 'tables.json')
    checks = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            raise AssertionError(f'검증 실패: {name}')
        checks.append({'check': name, 'result': 'pass'})

    check('22쪽 모두 텍스트 보존', len(pages) == profile['expected_pages'] and all(p['text'] for p in pages))
    nodes = records['regulation_node']
    by_id = {n['regulation_node_id']: n for n in nodes}
    node_by_label = {(n['node_type'], n['label']): n for n in nodes}
    expected = {'제' + n + '조' if '의' not in n else
                '제' + n.split('의')[0] + '조의' + n.split('의')[1] for n in profile['expected_articles']}
    current_articles = [n for n in nodes if n['node_type'] == 'article']
    check('본문 41개 조항 누락·중복 없음', {n['label'] for n in current_articles} == expected and
          len(current_articles) == len(expected))
    check('부칙의 제1조를 본문 제1조로 오인하지 않음', sum(n['label'] == '제1조' for n in current_articles) == 1)
    check('별표 3개·서식 1개 보존', Counter(n['node_type'] for n in nodes)['annex'] == 3 and
          Counter(n['node_type'] for n in nodes)['form'] == 1)
    all_lines = {line['line_id']: line for p in pages for line in p['lines'] if not line['is_footer']}
    primary_types = {'document', 'chapter', 'article', 'supplement', 'supplement_article', 'annex', 'form'}
    owners = Counter(line['line_id'] for n in nodes if n['node_type'] in primary_types
                     for line in n['source_locator']['lines'])
    check('쪽번호만 제외하고 모든 행을 정확히 1개 기본 노드에 배정',
          set(owners) == set(all_lines) and all(count == 1 for count in owners.values()))
    check('항·표를 포함한 모든 원문 위치가 실제 행에 연결됨', all(
        x['line_id'] in all_lines and x['page'] == all_lines[x['line_id']]['page'] and
        x['bbox'] == all_lines[x['line_id']]['bbox']
        for n in nodes for x in n['source_locator']['lines']))
    check('부모 노드와 자식의 개정본 일치', all(
        not n['parent_node_id'] or by_id[n['parent_node_id']]['regulation_version_id'] == n['regulation_version_id']
        for n in nodes))
    article5 = node_by_label[('article', '제5조')]
    article27 = node_by_label[('article', '제27조')]
    check('페이지를 넘는 조문 연결: 제5조 1~2쪽 / 제27조 8~9쪽',
          article5['source_locator']['pdf_pages'] == [1, 2] and article27['source_locator']['pdf_pages'] == [8, 9])
    delayed = [n for n in nodes if n['effective_from_override'] == '2028-01-01']
    check('예외 시행일은 제10조 제3항에만 적용', len(delayed) == 1 and
          delayed[0]['label'] == '제3항' and by_id[delayed[0]['parent_node_id']]['label'] == '제10조')
    check('일반 시행일과 개정일 구분', records['regulation_version'][0]['effective_from'] == '2025-01-01'
          and records['regulation_version'][0]['amendment_date'] == '2024-12-26')
    table19 = next(t for t in tables if t['page'] == 19)
    check('별표1 시간 배열 보존', table19['rows'][-1][1:] == ['32','48','64','80','112','48','48','80','48','48'])
    check('별표1 예외·최대시간·40% 주석 보존', all(text in node_by_label[('annex','별표 1')]['body_text']
          for text in ['200시간', '40%', '청원경찰', '10월 31일']))
    check('현재 표와 과거 부칙 표 구분', len(tables) == 6 and next(t for t in tables if t['page'] == 14)
          ['regulation_node_id'] in by_id and by_id[next(t for t in tables if t['page'] == 14)
                                                  ['regulation_node_id']]['source_locator']['section_kind'] == 'supplement')
    chunk_sources = records['rag_chunk_source']
    source_by_chunk = {r['rag_chunk_id']: r for r in chunk_sources}
    check('검색 후보와 원문 노드·행 모두 연결', all(
        c['rag_chunk_id'] in source_by_chunk and
        source_by_chunk[c['rag_chunk_id']]['regulation_node_id'] in by_id and
        all(line['line_id'] in all_lines for line in source_by_chunk[c['rag_chunk_id']]['source_span']['lines'])
        for c in records['rag_chunk']))
    check('검색 후보 내용 해시 일치', all(hashlib.sha256(c['chunk_text'].encode('utf-8')).hexdigest() ==
          c['content_hash'] for c in records['rag_chunk']))
    check('임베딩 미생성과 검색 미활성화 명시', all(c['embedding_model'] is None and
          c['token_count'] is None and c['search_status'] != 'active' for c in records['rag_chunk']))
    check('별표2·3 검색 후보 보류', all(c['search_status'] == 'needs_review' for c in records['rag_chunk']
          if by_id[source_by_chunk[c['rag_chunk_id']]['regulation_node_id']]['label'] in ('별표 2', '별표 3')))
    check('다운로드 시각은 사용자 제공값 / 종료일은 미확인',
          records['regulation_file'][0]['retrieved_at'] == profile['retrieved_at'] and
          records['regulation_version'][0]['effective_to'] is None)
    check('필드 집합·필수값 일치', all(
        set(row) == {c['n'] for c in table['cols']} and
        all(row[c['n']] is not None for c in table['cols'] if c['nn'])
        for name, table in schema['tables'].items() for row in records[name]))
    check('VARCHAR 최대 길이 준수', all(
        len(row[c['n']]) <= int(c['t'][8:-1]) for name, table in schema['tables'].items()
        for row in records[name] for c in table['cols']
        if c['t'].startswith('VARCHAR(') and row[c['n']] is not None))
    # SQLite 임시 메모리 DB: SQL 타입은 다르므로 PostgreSQL 서버 검증으로 간주하지 않는다.
    database = sqlite3.connect(':memory:')
    database.execute('PRAGMA foreign_keys=ON')
    database.executescript(schema_sql(schema, dialect='sqlite'))
    load = data_sql(schema, records, dialect='sqlite')
    database.executescript(load)
    first_counts = {name: database.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                    for name in schema['tables']}
    database.executescript(load)
    second_counts = {name: database.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                     for name in schema['tables']}
    check('외래키 제약·참조 무결성', database.execute('PRAGMA foreign_key_check').fetchall() == [])
    check('동일 데이터 반복 적재 시 중복 없음', first_counts == second_counts ==
          {name: len(rows) for name, rows in records.items()})
    check('임시 DB에 원문 및 JSON 문자열 손상 없이 적재',
          database.execute('SELECT body_text FROM regulation_node WHERE regulation_node_id=?',
                           (article5['regulation_node_id'],)).fetchone()[0] == article5['body_text'] and
          json.loads(database.execute('SELECT source_locator FROM regulation_node WHERE regulation_node_id=?',
                                      (article5['regulation_node_id'],)).fetchone()[0]) == article5['source_locator'])
    database.close()
    return {
        'result': 'pass', 'scope': '자료 구조·누락·원문 위치·로컬 임시 DB 검사. 사람의 규정 검토 완료는 아님.',
        'page_count': len(pages), 'article_count': len(current_articles),
        'supplement_count': sum(n['node_type'] == 'supplement' for n in nodes),
        'table_count': len(tables), 'chunk_count': len(records['rag_chunk']),
        'table_rows': first_counts, 'node_type_counts': dict(Counter(n['node_type'] for n in nodes)),
        'checks': checks, 'unverified': ['PostgreSQL/Supabase 서버에서 실행', '실제 검색 품질',
                                        '사용자 최종 원문 검토', '브라우저 화면 시각 검증'],
    }


if __name__ == '__main__':
    from preprocess import BASE, read_json, save_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BASE)
    args = parser.parse_args()
    result = validate_package(args.output.resolve(), read_json(BASE / 'schema.json'), read_json(BASE / 'profile.json'))
    save_json(args.output / 'report' / 'validation.json', result)
    print(f"{len(result['checks'])}개 검사 통과 / PostgreSQL·Supabase 실행은 미검증")
