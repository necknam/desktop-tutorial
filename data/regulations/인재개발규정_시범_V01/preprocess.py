"""인재개발규정 2024-12-26 개정본 전용 전처리. DB·외부 API에 접속하지 않는다."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber

BASE = Path(__file__).resolve().parent
NAMESPACE = uuid.UUID('c4e00b74-f728-4df1-8766-291864902b0c')
CHAPTER = re.compile(r'^제(\d+)장\s*(.*)$')
ARTICLE = re.compile(r'^제(\d+)조(의\d+)?\s*\(([^)]+)\)')
SUPPLEMENT = re.compile(r'^부칙\s*\((\d{4})\.(\d{2})\.(\d{2})\.\)')
ANNEX = re.compile(r'^【별표\s*(\d+)】')
FORM = re.compile(r'^\(별지\s*제(\d+)호\s*서식\)')
FOOTER = re.compile(r'^-\s*\d+\s*-$')
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def stable_id(key: str) -> str:
    # 같은 논리 키에는 매번 같은 ID를 부여해 재처리 시 중복을 방지한다.
    return str(uuid.uuid5(NAMESPACE, key))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pdf(source: Path, output: Path, profile: dict) -> tuple[list, list]:
    pages, tables = [], []
    with pdfplumber.open(source) as document:
        if len(document.pages) != profile['expected_pages']:
            raise ValueError('페이지 수가 시범 프로필과 다릅니다. 새 자료를 별도로 확인해 주세요.')
        for number, page in enumerate(document.pages, 1):
            lines = []
            for index, line in enumerate(page.extract_text_lines(
                return_chars=False, x_tolerance=2, y_tolerance=3
            ), 1):
                lines.append({
                    'line_id': f'p{number:02d}l{index:03d}', 'page': number,
                    'text': line['text'],
                    'bbox': [round(line[k], 3) for k in ('x0', 'top', 'x1', 'bottom')],
                    'is_footer': bool(FOOTER.fullmatch(line['text'].strip())),
                })
            if not lines:
                raise ValueError(f'{number}쪽의 텍스트를 추출하지 못했습니다. OCR 검토가 필요합니다.')
            pages.append({
                'page': number, 'width': page.width, 'height': page.height,
                'text': page.extract_text(x_tolerance=2, y_tolerance=3),
                'image_object_count': len(page.images), 'lines': lines,
            })
            for index, table in enumerate(page.find_tables(), 1):
                tables.append({
                    'table_key': f'p{number:02d}-table-{index}', 'page': number,
                    'bbox': [round(v, 3) for v in table.bbox], 'rows': table.extract(),
                    'status': 'needs_review' if number in profile['table_review_required_pages']
                    else 'machine_extracted',
                    'note': '빈 셀과 None(병합·미추출)은 다름. 병합 셀을 임의로 채우지 않음.',
                })
            if number in set(profile['visually_checked_table_pages'] + [18]):
                target = output / 'source' / f'page-{number:02d}.png'
                target.parent.mkdir(parents=True, exist_ok=True)
                page.to_image(resolution=120).save(target, format='PNG')
    return pages, tables


def locate(lines: list[dict]) -> dict:
    return {
        'coordinate_system': 'PDF point; 원점=페이지 왼쪽 위',
        'pdf_pages': sorted({line['page'] for line in lines}),
        'printed_pages': sorted({line['page'] for line in lines}),
        'lines': [{'line_id': line['line_id'], 'page': line['page'], 'bbox': line['bbox']}
                  for line in lines],
    }


def create_nodes(pages: list, tables: list, profile: dict, ids: dict) -> tuple[list, dict]:
    nodes, by_key = [], {}

    def new_node(key: str, node_type: str, label: str, title: str | None,
                 parent: dict | None, lines: list, origin: str) -> dict:
        path = f"{parent['path']} > {label}" if parent else label
        node = {
            'regulation_node_id': stable_id(f"{ids['version']}:{key}"),
            'regulation_version_id': ids['version'], 'regulation_file_id': ids['file'],
            'parent_node_id': parent['regulation_node_id'] if parent else None,
            'node_type': node_type, 'label': label, 'title': title, 'path': path,
            'display_order': len(nodes) + 1, 'body_text': '', 'source_locator': {},
            'effective_from_override': None, 'effective_to_override': None,
            'extraction_status': 'machine_extracted', 'review_note': None,
            '_key': key, '_lines': lines.copy(), '_origin': origin,
        }
        nodes.append(node)
        by_key[key] = node
        return node

    root = new_node('document', 'document', profile['regulation_name'], None, None, [], 'metadata')
    chapter, supplement, current = None, None, root
    phase = 'body'
    for page in pages:
        for line in page['lines']:
            text = line['text'].strip()
            if line['is_footer']:
                continue
            if line['page'] == 1 and (text == profile['regulation_name'] or '22차/개정' in text):
                root['_lines'].append(line)
                continue
            if match := SUPPLEMENT.match(text):
                date = '-'.join(match.groups())
                phase = 'supplement'
                supplement = new_node(f'supplement:{date}', 'supplement',
                                      f'부칙({date})', None, root, [line], phase)
                current = supplement
            elif match := ANNEX.match(text):
                number = match.group(1)
                phase = 'annex'
                current = new_node(f'annex:{number}', 'annex', f'별표 {number}',
                                   profile['annexes'][number], root, [line], phase)
            elif match := FORM.match(text):
                phase = 'form'
                current = new_node(f'form:{match.group(1)}', 'form',
                                   f'별지 제{match.group(1)}호서식', profile['form_title'], root, [line], phase)
            elif phase == 'body' and (match := CHAPTER.match(text)):
                chapter = new_node(f'chapter:{match.group(1)}', 'chapter', f'제{match.group(1)}장',
                                   match.group(2), root, [line], phase)
                current = chapter
            elif phase in ('body', 'supplement') and (match := ARTICLE.match(text)):
                number = match.group(1) + (match.group(2) or '')
                key = f'article:{number}' if phase == 'body' else f"{supplement['_key']}:article:{number}"
                current = new_node(key, 'article' if phase == 'body' else 'supplement_article',
                                   f'제{number}조' if '의' not in number else
                                   f"제{number.split('의')[0]}조의{number.split('의')[1]}",
                                   match.group(3), chapter if phase == 'body' else supplement, [line], phase)
            else:
                current['_lines'].append(line)

    # ① 등 항 번호가 있는 조문만 항으로 나눈다. 상위 조문에는 통합 원문도 보존한다.
    for article in nodes.copy():
        if article['node_type'] not in ('article', 'supplement_article'):
            continue
        groups, current_group = [], None
        for index, line in enumerate(article['_lines']):
            text = line['text']
            head = ARTICLE.match(text) if index == 0 else None
            tail = text[head.end():].lstrip() if head else text.lstrip()
            if tail and tail[0] in CIRCLED:
                current_group = (str(CIRCLED.index(tail[0]) + 1), [])
                groups.append(current_group)
            if current_group:
                current_group[1].append(line)
        for number, lines in groups:
            new_node(f"{article['_key']}:paragraph:{number}", 'paragraph', f'제{number}항',
                     None, article, lines, article['_origin'])

    for table in tables:
        x0, top, x1, bottom = table['bbox']
        page_lines = pages[table['page'] - 1]['lines']
        related_lines = [line for line in page_lines if
                         line['bbox'][1] < bottom and line['bbox'][3] > top]
        candidates = [node for node in nodes if node['_origin'] != 'metadata' and
                      node['node_type'] in ('article', 'supplement_article', 'supplement', 'annex') and
                      any(line['line_id'] in {x['line_id'] for x in related_lines} for line in node['_lines'])]
        if not candidates:
            raise ValueError(f"{table['table_key']}의 상위 항목을 확인하지 못했습니다.")
        parent = candidates[-1]
        text = '\n'.join(' | '.join('[병합 또는 미추출]' if cell is None else
                                     cell.replace('\n', ' / ') for cell in row) for row in table['rows'])
        node = new_node(f"table:{table['table_key']}", 'table', table['table_key'],
                        '자동 추출 표(원문 대조 필요)', parent, related_lines, parent['_origin'])
        table['regulation_node_id'] = node['regulation_node_id']
        node['source_locator'] = {'table_bbox': table['bbox'], 'table_grid': table['rows']}
        node['body_text'] = text
        node['extraction_status'] = table['status']
        node['review_note'] = table['note']
    for node in nodes:
        if node['node_type'] != 'table':
            node['body_text'] = '\n'.join(line['text'] for line in node['_lines'])
        node['source_locator'].update(locate(node['_lines']))
        node['source_locator']['section_kind'] = node['_origin']
        if node['_origin'] == 'supplement':
            node['review_note'] = '이 현행 PDF에 수록된 부칙. 당시 전체 규정 개정본으로 간주하지 않음.'
        if node['_key'] in ('annex:2', 'annex:3'):
            node['extraction_status'] = 'needs_review'
            node['review_note'] = '표의 병합 셀·도식 순서 검토 필요. 검색 활성화 보류.'
        if node['_key'] == 'article:12':
            node['review_note'] = '본조 삭제 표기를 원문 그대로 보존. 현재 의무사항으로 안내하지 않음.'
    return nodes, by_key


def build_references(nodes: list, by_key: dict, ids: dict) -> list:
    references = []
    # 자동 연결은 직접 표기된 별표·서식·동일 규정명에 한정한다.
    # 다른 규정명과 함께 나온 조 번호를 현재 규정의 조 번호로 오연결하지 않는다.
    for node in nodes:
        if node['node_type'] not in ('article', 'annex', 'form'):
            continue
        patterns = [('annex', re.compile(r'별표\s*(\d+)')),
                    ('form', re.compile(r'별지\s*제?(\d+)호\s*서식')),
                    ('regulation', re.compile(r'「([^」]+)」'))]
        for kind, pattern in patterns:
            for text in dict.fromkeys(match.group(0) for match in pattern.finditer(node['body_text'])):
                match = pattern.search(text)
                name = match.group(1)
                target = by_key.get(f'{kind}:{name}') if kind != 'regulation' else None
                if target and target['regulation_node_id'] == node['regulation_node_id']:
                    continue
                same_regulation = kind == 'regulation' and name == '인재개발규정'
                references.append({
                    'regulation_reference_id': stable_id(f"reference:{node['regulation_node_id']}:{kind}:{text}"),
                    'regulation_node_id': node['regulation_node_id'], 'reference_type': kind,
                    'target_text': text,
                    'target_regulation_id': ids['regulation'] if target or same_regulation else None,
                    'target_version_id': ids['version'] if target or same_regulation else None,
                    'target_node_id': target['regulation_node_id'] if target else None,
                    'resolution_status': 'resolved' if target or same_regulation else 'unresolved',
                    'review_note': '직접 표기된 참조만 연결. 상대 조문·준용 범위·다른 문서의 적용 버전은 후속 검토.',
                })
    return references


def build_chunks(nodes: list, ids: dict, timestamp: str, profile: dict) -> tuple[list, list]:
    chunks, sources = [], []
    parents_with_paragraphs = {node['parent_node_id'] for node in nodes if node['node_type'] == 'paragraph'}
    max_chars = 1600  # 임베딩 모델 토큰 제한이 아닌, 검토용 문자 수 기준이다.
    for node in nodes:
        eligible = (node['_origin'] == 'body' and node['node_type'] in ('article', 'paragraph')) or \
                   (node['_origin'] == 'annex' and node['node_type'] == 'annex') or \
                   (node['_key'].startswith('supplement:2024-12-26') and
                    node['node_type'] == 'supplement_article')
        if not eligible or node['regulation_node_id'] in parents_with_paragraphs:
            continue
        parts, pending = [], []
        for line in node['_lines']:
            if pending and len('\n'.join(x['text'] for x in pending + [line])) > max_chars:
                parts.append(pending)
                pending = []
            pending.append(line)
        if pending:
            parts.append(pending)
        for number, lines in enumerate(parts, 1):
            text = f"{node['path']}\n" + '\n'.join(line['text'] for line in lines)
            chunk_id = stable_id(f"chunk:{node['regulation_node_id']}:{profile['profile_version']}:{number}")
            page_numbers = {line['page'] for line in lines}
            status = 'needs_review' if page_numbers & {6, 20, 21} else 'pending_review'
            if node['_key'] == 'article:12':
                status = 'not_for_answer'
            chunks.append({
                'rag_chunk_id': chunk_id, 'regulation_version_id': ids['version'],
                'chunk_text': text, 'chunk_type': node['node_type'], 'context_path': node['path'],
                'content_hash': sha256(text.encode('utf-8')), 'chunking_version': profile['profile_version'],
                'index_revision': profile['profile_version'], 'embedding_model': None,
                'search_status': status, 'created_at': timestamp, 'token_count': None,
            })
            sources.append({
                'rag_chunk_source_id': stable_id(f'source:{chunk_id}'), 'rag_chunk_id': chunk_id,
                'regulation_node_id': node['regulation_node_id'], 'source_order': 1,
                'source_role': 'primary', 'source_span': locate(lines),
            })
    return chunks, sources


def sql_value(value: Any, data_type: str, dialect: str) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return ('TRUE' if value else 'FALSE') if dialect == 'postgres' else str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    text = "'" + str(value).replace("'", "''") + "'"
    return text + ('::jsonb' if data_type == 'JSONB' and dialect == 'postgres' else '')


def schema_sql(schema: dict, dialect: str = 'postgres') -> str:
    prefix = schema['schema_name'] + '.' if dialect == 'postgres' else ''
    statements = [f"CREATE SCHEMA IF NOT EXISTS {schema['schema_name']};"] if prefix else []
    for name, table in schema['tables'].items():
        lines = []
        for col in table['cols']:
            data_type = col['t'] if dialect == 'postgres' else (
                'INTEGER' if col['t'] in ('INT', 'BOOLEAN') else 'TEXT')
            lines.append(f"  {col['n']} {data_type}" + (' PRIMARY KEY' if col.get('pk') else '') +
                         (' NOT NULL' if col['nn'] else ''))
        for src, field, dest, dest_field in schema['foreign_keys']:
            if src == name:
                lines.append(f'  FOREIGN KEY ({field}) REFERENCES {prefix}{dest} ({dest_field})')
        if name == 'rag_chunk_source':
            lines.append('  UNIQUE (rag_chunk_id, source_order)')
        statements.append(f'CREATE TABLE IF NOT EXISTS {prefix}{name} (\n' + ',\n'.join(lines) + '\n);')
    return '\n\n'.join(statements) + '\n'


def data_sql(schema: dict, records: dict, dialect: str = 'postgres') -> str:
    prefix = schema['schema_name'] + '.' if dialect == 'postgres' else ''
    statements = ['BEGIN;']
    for name, table in schema['tables'].items():
        cols = table['cols']
        pk = next(col['n'] for col in cols if col.get('pk'))
        for row in records[name]:
            values = ', '.join(sql_value(row[col['n']], col['t'], dialect) for col in cols)
            updates = ', '.join(f"{col['n']} = EXCLUDED.{col['n']}" for col in cols if not col.get('pk'))
            statements.append(f"INSERT INTO {prefix}{name} ({', '.join(col['n'] for col in cols)})\n"
                              f'VALUES ({values})\nON CONFLICT ({pk}) DO UPDATE SET {updates};')
    statements.append('COMMIT;')
    return '\n\n'.join(statements) + '\n'


def export_records(output: Path, schema: dict, records: dict) -> None:
    for name, table in schema['tables'].items():
        save_json(output / 'data' / f'{name}.json', records[name])
        with (output / 'data' / f'{name}.csv').open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=[col['n'] for col in table['cols']])
            writer.writeheader()
            for row in records[name]:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                                 else ('\\N' if value is None else str(value).lower() if isinstance(value, bool)
                                       else value) for key, value in row.items()})
    (output / 'sql').mkdir(parents=True, exist_ok=True)
    (output / 'sql' / '01_schema.sql').write_text(
        '-- 전달용 독립 스키마. 기존 public 테이블에는 변경 없음.\n' + schema_sql(schema), encoding='utf-8')
    (output / 'sql' / '02_data.sql').write_text(
        '-- 실제 실행 시 hr_regulation_pilot 테이블에 행을 추가하거나 같은 ID의 행을 갱신함.\n'
        '-- 통합 담당자가 별도로 검토한 후 실행. 자동 접속·실행은 하지 않음.\n' + data_sql(schema, records),
        encoding='utf-8')
    with (output / 'report' / 'field_guide.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['테이블', '한글명', '필드', '자료형', '필수', '키', '의미', '전달 시 주의'])
        for name, table in schema['tables'].items():
            for col in table['cols']:
                note = {'retrieved_at': '사용자 제공: 2026-10-01 09:39 한국시간. 분 단위; 초 00은 형식상 표기.',
                        'embedding_model': '벡터 미생성. 모델 미정이므로 NULL.',
                        'storage_key': '전달 폴더 기준 상대 경로. 실제 저장소 업로드 후 대응 필요.',
                        'external_code': '공식 ALIO 기관 코드로 추정하지 않음.',
                        'source_url': 'ALIO 내부규정 목록 URL. 원본 다운로드 직접 URL이 아님.',
                        'token_count': '모델 토크나이저 미정이므로 NULL.'}.get(col['n'], '')
                writer.writerow([name, table['label'], col['n'], col['t'], '예' if col['nn'] else '아니오',
                                 'PK' if col.get('pk') else 'FK' if col.get('fk') else '', col['c'], note])


def review_html(output: Path, schema: dict, records: dict, raw_tables: list,
                issues: list, report: dict, pdf_name: str) -> None:
    esc = lambda value: html.escape(str(value))
    nodes = records['regulation_node']
    parts = ['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" '
             'content="width=device-width,initial-scale=1"><title>인재개발규정 시범 전처리 검토</title><style>'
             'body{font:16px/1.75 "Malgun Gothic",sans-serif;color:#203246;background:#f2f5f9;margin:0}'
             'main{max-width:1150px;margin:auto;padding:36px 24px}section{background:white;border-radius:12px;'
             'padding:24px;margin:22px 0}h1,h2{line-height:1.3;color:#22558c}h3{margin:8px 0}'
             'table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d8e1ea;'
             'padding:8px;text-align:left;vertical-align:top}th{background:#eaf1fa}'
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 "Malgun Gothic",sans-serif}'
             'details{border:1px solid #dbe3ed;padding:12px;border-radius:8px;margin:10px 0}'
             'summary{cursor:pointer;font-weight:bold}a{color:#195d9a}img{width:100%;max-width:900px;display:block;'
             'margin:12px auto;border:1px solid #ddd}.bad{background:#fff2da;padding:15px;border-radius:8px}'
             '.scroll{overflow:auto}.small{font-size:14px;color:#526479}nav{display:flex;gap:16px;flex-wrap:wrap}'
             '@media print{body{background:white}main{padding:0}section{break-inside:avoid}details{break-inside:auto}}'
             '</style><main><h1>한국수자원공사 · 인재개발규정<br>시범 전처리 검토 V01</h1>'
             '<p>입력 PDF 1개 → 원문 추출 → 구조화 데이터 → DB 적재용 파일. 사용자 검토 대기 상태입니다.</p>'
             '<nav><a href="#summary">결과</a><a href="#review">검토 사항</a><a href="#body">조항</a>'
             '<a href="#appendix">별표</a><a href="#fields">필드 설명</a></nav>']
    parts.append('<section id="summary"><h2>1. 처리 결과</h2><table><tr><th>구분</th><th>결과</th></tr>')
    for label, value in [('규정 개정일', '2024-12-26 / 22차'), ('일반 시행일', '2025-01-01'),
                         ('예외 시행일', '제10조 제3항: 2028-01-01 / 부칙 18쪽'),
                         ('처리 페이지', report['page_count']), ('본문 조항', report['article_count']),
                         ('수록 부칙', report['supplement_count']), ('별표·서식', '별표 3개 · 서약서 1개'),
                         ('검출 표', report['table_count']), ('검색 후보', report['chunk_count']),
                         ('다운로드 시각', '2026-10-01 09:39 한국시간 / 사용자 제공 · 분 단위'),
                         ('검색·임베딩', '검색 미활성화 · 벡터 미생성'),
                         ('DB 확인', '로컬 임시 DB에서 관계·반복 적재 검증 / Supabase 실행 미검증')]:
        parts.append(f'<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>')
    parts.append(f'</table><p><a href="source/{esc(pdf_name)}">원본 PDF 사본</a> · '
                 '<a href="README.txt">진행 방법과 전달 안내</a></p>'
                 '<p class="small">원본 PDF·기존 ERD는 변경하지 않았습니다. 처리 결과는 공식 교육 인정 판정이나 '
                 '직원 역량 진단이 아닙니다. 수록된 과거 부칙은 과거 전체 개정본과 구분됩니다.</p></section>')
    parts.append('<section id="review"><h2>2. 우선 검토할 내용</h2>')
    for issue in issues:
        parts.append(f'<details open><summary>{esc(issue["title"])}</summary><p>{esc(issue["detail"])}</p>'
                     f'<p class="small">처리: {esc(issue["action"])}</p></details>')
    parts.append('</section><section id="body"><h2>3. 본문 조항 · 항</h2>'
                 '<p>조문에는 통합 원문을, 하위 항에는 해당 부분을 저장합니다. 검색 후보는 항이 있는 조문에서 '
                 '항 단위로 작성하여 중복을 줄였습니다. 행별 원문 위치는 JSON에 보관합니다.</p>')
    for node in nodes:
        if node['node_type'] != 'article':
            continue
        pages = node['source_locator']['pdf_pages']
        parts.append(f'<details><summary>{esc(node["label"])} · {esc(node["title"])} '
                     f'<span class="small">({esc(pages)}쪽)</span></summary>'
                     f'<p><a href="source/{esc(pdf_name)}#page={pages[0]}">원문 페이지 확인</a></p>'
                     f'<pre>{esc(node["body_text"])}</pre>')
        for child in nodes:
            if child['node_type'] == 'paragraph' and child['parent_node_id'] == node['regulation_node_id']:
                suffix = f' / 시행일 {child["effective_from_override"]}' if child['effective_from_override'] else ''
                parts.append(f'<p class="small">하위 항: {esc(child["label"] + suffix)}</p>')
        parts.append('</details>')
    parts.append('</section><section><h2>4. 부칙과 시행일</h2><p>부칙은 날짜별로 구분했습니다. '
                 '과거 부칙을 현재 교육시간 산정식으로 자동 적용하지 않습니다.</p>')
    for node in nodes:
        if node['node_type'] == 'supplement':
            children = [x for x in nodes if x['parent_node_id'] == node['regulation_node_id'] and
                        x['node_type'] == 'supplement_article']
            text = '\n'.join([node['body_text']] + [x['body_text'] for x in children])
            parts.append(f'<details><summary>{esc(node["label"])}</summary><pre>{esc(text)}</pre></details>')
    parts.append('</section><section id="appendix"><h2>5. 별표 · 서식 · 표</h2>'
                 '<p class="bad">별표 2·3: 표와 도식의 자동 추출 결과는 검토가 필요합니다. '
                 '아래 원문 이미지를 기준으로 확인하세요. 빈 셀을 임의로 보충하지 않았습니다.</p>')
    for node in nodes:
        if node['node_type'] in ('annex', 'form'):
            pages = node['source_locator']['pdf_pages']
            parts.append(f'<details open><summary>{esc(node["label"])} · {esc(node["title"])}</summary>'
                         f'<pre>{esc(node["body_text"])}</pre>')
            for number in pages:
                if (output / 'source' / f'page-{number:02d}.png').exists():
                    parts.append(f'<img src="source/page-{number:02d}.png" alt="원문 {number}쪽">')
            parts.append('</details>')
    for table in raw_tables:
        parts.append(f'<details><summary>자동 검출 표: {esc(table["table_key"])} · {esc(table["status"])}</summary>'
                     '<div class="scroll"><table>')
        for row in table['rows']:
            parts.append('<tr>' + ''.join(f'<td>{esc("[병합 또는 미추출]" if v is None else v)}</td>' for v in row) + '</tr>')
        parts.append('</table></div></details>')
    parts.append('</section><section id="fields"><h2>6. DB 테이블 · 필드 설명</h2>'
                 '<p>HR_Example_ERD의 규정 관련 9개 테이블을 기준으로 했습니다. PostgreSQL용 자료형으로 '
                 '바꾸되 기존 ERD 파일은 수정하지 않았습니다.</p>'
                 '<p class="bad">embedding_model(임베딩 모델)은 미생성 상태여서 전달용 스키마에서만 '
                 'NULL을 허용합니다. 다운로드 시각은 사용자 제공 2026-10-01 09:39 한국시간으로 반영했습니다. 실제 서비스 스키마와는 '
                 '통합 시 대응을 확인해야 합니다.</p>')
    for name, table in schema['tables'].items():
        parts.append(f'<details><summary>{esc(table["label"])} · {esc(name)} ({len(records[name])}행)</summary>'
                     '<div class="scroll"><table><tr><th>필드</th><th>의미</th><th>자료형</th><th>키·필수</th></tr>')
        for col in table['cols']:
            key = 'PK' if col.get('pk') else 'FK' if col.get('fk') else '—'
            parts.append(f'<tr><td>{esc(col["n"])}</td><td>{esc(col["c"])}</td><td>{esc(col["t"])}</td>'
                         f'<td>{key} / {"필수" if col["nn"] else "빈 값 허용"}</td></tr>')
        parts.append('</table></div></details>')
    parts.append('</section><p class="small">본문·표의 정확성은 사용자의 최종 검토 전입니다. '
                 'HTML 구조와 파일 경로는 검사했으며 브라우저 화면의 시각 검증은 수행하지 않았습니다.</p></main></html>')
    (output / '검토안내.html').write_text(''.join(parts), encoding='utf-8')


def run(args: argparse.Namespace) -> None:
    profile, schema = read_json(BASE / 'profile.json'), read_json(BASE / 'schema.json')
    source = args.input.resolve()
    output = args.output.resolve()
    digest = sha256(source.read_bytes())
    if digest != profile['expected_sha256']:
        raise ValueError('원문 해시가 시범 문서와 다릅니다. 개정본·다른 규정에는 이 프로필을 그대로 적용하지 마세요.')
    for directory in ('data', 'raw', 'report', 'source', 'sql'):
        (output / directory).mkdir(parents=True, exist_ok=True)
    if output != BASE:
        for name in ('preprocess.py', 'validate.py', 'requirements.txt', 'schema.json', 'profile.json', 'README.txt'):
            shutil.copyfile(BASE / name, output / name)
    copied = output / 'source' / source.name
    if source != copied.resolve():
        shutil.copyfile(source, copied)
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    ids = {
        'institution': args.institution_id or stable_id('institution:' + profile['institution_key']),
        'regulation': stable_id('regulation:' + (args.institution_id or profile['institution_key']) + ':인재개발규정'),
        'version': stable_id('version:' + (args.institution_id or profile['institution_key']) + ':인재개발규정:2024-12-26:22차'),
        'file': stable_id('file:' + (args.institution_id or profile['institution_key']) + ':' + digest),
    }
    pages, tables = extract_pdf(source, output, profile)
    nodes, by_key = create_nodes(pages, tables, profile, ids)
    delayed = by_key['article:10:paragraph:3']
    delayed['effective_from_override'] = '2028-01-01'
    delayed['review_note'] = '부칙(2024.12.26.) 제1조(18쪽)의 예외 시행일. 상위 제10조 전체의 시행일을 바꾸지 않음.'
    scopes = [{
        'regulation_scope_id': stable_id('scope:' + delayed['regulation_node_id'] + ':effective-date'),
        'regulation_node_id': by_key['supplement:2024-12-26:article:1']['regulation_node_id'],
        'scope_type': 'effective_date_exception',
        'condition_text': by_key['supplement:2024-12-26:article:1']['body_text'],
        'condition_json': {'effective_from': '2028-01-01', 'rule_source': 'explicit_pdf_text'},
        'applies_to_node_id': delayed['regulation_node_id'], 'review_status': 'pending_review',
    }]
    chunks, chunk_sources = build_chunks(nodes, ids, timestamp, profile)
    records = {
        'institution': [{'institution_id': ids['institution'], 'name': '한국수자원공사', 'external_code': None}],
        'regulation': [{'regulation_id': ids['regulation'], 'institution_id': ids['institution'],
                        'name': '인재개발규정', 'category': '교육·성장지원', 'document_kind': 'regulation'}],
        'regulation_version': [{
            'regulation_version_id': ids['version'], 'regulation_id': ids['regulation'],
            'version_label': '22차', 'title_at_version': '인재개발규정', 'content_kind': 'consolidated_text',
            'coverage_status': 'full_pdf_captured', 'amendment_date': '2024-12-26',
            'effective_from': '2025-01-01', 'effective_to': None, 'date_status': 'explicit_in_pdf',
            'review_status': 'pending_review', 'availability_status': 'review_only', 'previous_version_id': None,
            'review_note': '이후 개정 여부·종료일 미확인. 과거 부칙은 별도 과거 개정본이 아님. 내부 세부 인정기준 미확보.',
        }],
        'regulation_file': [{
            'regulation_file_id': ids['file'], 'regulation_version_id': ids['version'], 'source_file_id': None,
            'file_name': source.name, 'file_format': 'pdf', 'file_role': 'original',
            'storage_key': 'source/' + source.name, 'source_type': 'alio_public', 'source_url': profile['source_url'],
            'retrieved_at': profile['retrieved_at'], 'file_hash': digest, 'is_extraction_source': True,
            'parse_status': 'extracted_with_review_items', 'access_level': 'public',
        }],
        'regulation_node': [{key: value for key, value in node.items() if not key.startswith('_')} for node in nodes],
        'regulation_scope': scopes, 'regulation_reference': build_references(nodes, by_key, ids),
        'rag_chunk': chunks, 'rag_chunk_source': chunk_sources,
    }
    issues = [
        {'title': '① 별표 2·3의 병합 표·도식', 'detail': '20쪽 표 일부 글자가 옆 셀로 넘어가며, 21쪽 단계·직무·평가의 위치 정보가 평문에서 약화됩니다.',
         'action': '원문 이미지와 표 좌표를 보관. 해당 검색 후보는 needs_review로 보류. 공식 기준을 임의로 복원하지 않음.'},
        {'title': '② 제10조 제3항의 시행일', 'detail': '일반 시행일은 2025-01-01이나 이 항만 2028-01-01부터 시행합니다.',
         'action': '제3항 하위 노드에 예외 시행일을 저장. 검색 연동 시 기준일과 하위 항 시행일을 함께 확인할 것.'},
        {'title': '③ 제28조 제4항의 원문 확인', 'detail': '9쪽의 제4항은 “다음 각호에 따른다.”라고 끝나며 이어지는 호 내용은 이 PDF에서 확인되지 않습니다.',
         'action': '원문 그대로 보존하고 아래 내용을 만들어 넣지 않음. 해당 항의 답변 활용은 추가 원문 확인 전 보류.'},
        {'title': '④ 출처·종료일·내부 기준', 'detail': '다운로드 시각은 사용자 제공 2026-10-01 09:39 한국시간입니다. 개정본 종료일과 별도 학습 인정기준은 미확인입니다.',
         'action': '다운로드 시각은 분 단위 정보로 기록. 종료일·미생성 임베딩은 NULL. 처리 시각은 별도 manifest에 기록. 내부 기준을 공식 규정으로 가정하지 않음.'},
        {'title': '⑤ 다른 문서 참조와 과거 부칙', 'detail': '인사규정·취업규칙·여비규정·공무원 인재개발법 시행령 등은 이 파일만으로 대상 버전·준용 범위를 확정할 수 없습니다.',
         'action': '다른 문서 참조는 unresolved. 수록된 과거 부칙은 날짜별 노드로만 보관.'},
    ]
    for chunk in records['rag_chunk']:
        if chunk['context_path'].endswith('제28조 > 제4항'):
            chunk['search_status'] = 'needs_review'
    save_json(output / 'raw' / 'pages.json', pages)
    save_json(output / 'raw' / 'tables.json', tables)
    save_json(output / 'report' / 'review_issues.json', issues)
    save_json(output / 'report' / 'manifest.json', {
        'profile_version': profile['profile_version'], 'processed_at': timestamp,
        'source_sha256': digest, 'source_file': 'source/' + source.name,
        'source_listing_url': profile['source_url'], 'source_download_url': None,
        'retrieved_at': profile['retrieved_at'], 'retrieved_at_source': 'user_reported',
        'retrieved_at_precision': 'minute', 'retrieved_at_timezone': 'Asia/Seoul',
        'schema_source': schema['source'], 'package_schema': schema['schema_name'],
        'ids': ids, 'runtime': {'python': sys.version.split()[0], 'pdfplumber': pdfplumber.__version__},
        'encoding': 'JSON/SQL/HTML=UTF-8; CSV=UTF-8-BOM; CSV NULL=\\N',
        'integrations_executed': [],
    })
    export_records(output, schema, records)
    from validate import validate_package
    report = validate_package(output, schema, profile)
    save_json(output / 'report' / 'validation.json', report)
    review_html(output, schema, records, tables, issues, report, source.name)
    print(json.dumps({'output': str(output), 'counts': report['table_rows'],
                      'validation': report['result']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='해당 시범 원문 PDF의 경로')
    parser.add_argument('--output', type=Path, default=BASE, help='새 결과를 쓸 폴더')
    parser.add_argument('--institution-id', default=None, help='통합 담당자가 기존 기관 ID를 사용하려는 경우')
    arguments = parser.parse_args()
    try:
        run(arguments)
    except (OSError, ValueError, KeyError, AssertionError) as error:
        print(f'전처리 중단: {error}', file=sys.stderr)
        sys.exit(1)
