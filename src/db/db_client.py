"""Server-side PostgreSQL persistence using SUPABASE_DB_URL; no memory fallback."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from src.db.deployment import CATALOG, connect, ident
from src.common.assessment_schema import validate_questions, validate_responses


class DatabaseOperationError(RuntimeError):
    pass


def user_id_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('users.user_id must be an integer, not a profile ID or UUID.')
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError('users.user_id must be an integer, not a profile ID or UUID.')
    result = int(value)
    if result <= 0 or result > 2147483647:
        raise ValueError('users.user_id is outside the positive PostgreSQL INTEGER range.')
    return result


def serializable(value):
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if isinstance(value, (UUID, date, datetime, Decimal)):
        return str(value)
    return value


class Query:
    """Small compatibility layer for existing .client.table(...) call sites."""
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.spec = CATALOG.get('public.' + table)
        if self.spec is None:
            raise ValueError('Unsupported public table')
        self.columns = {c['column']: c for c in self.spec['columns']}
        self.operation, self.fields = 'select', '*'
        self.filters, self.sorts, self.cap = [], [], None
        self.payload, self.conflict = None, None

    def column(self, field):
        if field not in self.columns:
            raise ValueError(f'Unknown column: {self.table_name}.{field}')
        return ident(field)

    def select(self, fields='*'):
        self.fields = '*' if fields == '*' else ', '.join(self.column(f.strip()) for f in fields.split(','))
        return self

    def _filter(self, field, operator, value):
        self.filters.append((self.column(field), operator, value))
        return self

    def eq(self, field, value): return self._filter(field, '=', value)
    def neq(self, field, value): return self._filter(field, '<>', value)
    def ilike(self, field, value): return self._filter(field, 'ILIKE', value)
    def gte(self, field, value): return self._filter(field, '>=', value)
    def lte(self, field, value): return self._filter(field, '<=', value)
    def in_(self, field, values): return self._filter(field, '= ANY', list(values))

    def order(self, field, desc=False):
        self.sorts.append(self.column(field) + (' DESC' if desc else ' ASC'))
        return self

    def limit(self, value):
        if type(value) is not int or not 1 <= value <= 10000:
            raise ValueError('limit must be 1..10000')
        self.cap = value
        return self

    def insert(self, data):
        self.operation, self.payload = 'insert', data
        return self

    def upsert(self, data, on_conflict=None):
        self.operation, self.payload = 'upsert', data
        self.conflict = on_conflict.split(',') if on_conflict else self.spec['pk']
        return self

    def update(self, data):
        self.operation, self.payload = 'update', data
        return self

    def delete(self):
        self.operation = 'delete'
        return self

    def value(self, field, value):
        self.column(field)
        if value is not None and self.columns[field]['type'] == 'jsonb':
            from psycopg.types.json import Jsonb
            return Jsonb(serializable(value))
        if value is not None and self.columns[field]['type'].startswith('vector(') and isinstance(value, list):
            import json
            return json.dumps(value, allow_nan=False)
        return value

    def _run(self, conn):
        table = 'public.' + ident(self.table_name)
        predicates, params = [], []
        for col, operator, value in self.filters:
            if value is None and operator in ('=', '<>'):
                predicates.append(col + (' IS NULL' if operator == '=' else ' IS NOT NULL'))
            else:
                predicates.append(col + (' = ANY(%s)' if operator == '= ANY' else f' {operator} %s'))
                params.append(value)
        where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
        if self.operation == 'select':
            statement = f'SELECT {self.fields} FROM {table}' + where
            if self.sorts:
                statement += ' ORDER BY ' + ', '.join(self.sorts)
            if self.cap is not None:
                statement += ' LIMIT %s'
                params.append(self.cap)
            return conn.execute(statement, params).fetchall()
        if self.operation in ('delete', 'update'):
            if not where:
                raise ValueError('UPDATE and DELETE require explicit filters')
            if self.operation == 'delete':
                return conn.execute(f'DELETE FROM {table}' + where + ' RETURNING *', params).fetchall()
            fields = list(self.payload)
            if not fields:
                raise ValueError('Empty update')
            assignments = ', '.join(self.column(f) + '=%s' for f in fields)
            values = [self.value(f, self.payload[f]) for f in fields]
            return conn.execute(f'UPDATE {table} SET {assignments}' + where + ' RETURNING *', values + params).fetchall()
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        returned = []
        for row in rows:
            fields = list(row)
            if not fields:
                raise ValueError('Empty insert')
            statement = f'INSERT INTO {table} ({", ".join(self.column(f) for f in fields)}) VALUES ({", ".join(["%s"] * len(fields))})'
            if self.operation == 'upsert':
                keys = [key.strip() for key in self.conflict]
                statement += ' ON CONFLICT (' + ', '.join(self.column(k) for k in keys) + ') '
                updates = [f for f in fields if f not in keys]
                statement += ('DO UPDATE SET ' + ', '.join(self.column(f) + '=EXCLUDED.' + self.column(f) for f in updates)) if updates else 'DO NOTHING'
            returned.extend(conn.execute(statement + ' RETURNING *', [self.value(f, row[f]) for f in fields]).fetchall())
        return returned

    def execute(self):
        with self.db.transaction() as conn:
            rows = serializable(self._run(conn))
        return SimpleNamespace(data=rows, count=len(rows))


class DatabaseClient:
    def __init__(self, connection=None):
        self._connection = connection  # Optional caller-owned connection for transaction tests.
        self.client = self

    @contextmanager
    def transaction(self):
        try:
            if self._connection is not None:
                with self._connection.transaction():
                    yield self._connection
            else:
                with connect('SUPABASE_DB_URL') as conn:
                    yield conn
        except (ValueError, DatabaseOperationError):
            raise
        except Exception as exc:
            raise DatabaseOperationError(f'DB operation failed ({type(exc).__name__}); transaction rolled back. Check DB connection and schema.') from None

    def table(self, name): return Query(self, name)

    def _rows(self, table, filters=None, order=None):
        query = self.table(table).select()
        for field, value in (filters or {}).items():
            query.eq(field, value)
        if order:
            query.order(order)
        return query.execute().data

    def _one(self, table, filters):
        rows = self._rows(table, filters)
        return rows[0] if rows else None

    def _session_lock(self, conn, session_id):
        row = conn.execute('SELECT session_id, current_stage FROM public.roadmap_sessions WHERE session_id=%s FOR UPDATE', (session_id,)).fetchone()
        if row is None:
            raise ValueError('Project session does not exist.')
        return row

    def check_health(self):
        try:
            with self.transaction() as conn:
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute('SELECT user_id,name FROM public.users LIMIT 1').fetchall()
            return {'connected': True, 'message': 'PostgreSQL connected', 'storage': 'postgresql'}
        except (RuntimeError, ValueError):
            return {'connected': False, 'message': 'SUPABASE_DB_URL connection/schema check failed', 'storage': 'postgresql'}

    def list_test_users(self):
        return self.table('users').select('user_id,name,email,department,job_title,grade,source_type').in_(
            'source_type', ['TEST_MANUAL', 'TEST_AUTO']).order('user_id').execute().data

    def list_skill_tags(self):
        return self.table('skill_tags').select('skill_id,name,category').order('skill_id').execute().data

    def fetch_mock_profiles(self):
        return self._rows('test_mock_profiles')

    def get_or_create_user(self, username, email, department='', job_title='', grade='', source_type='TEST_MANUAL'):
        if not username.strip() or not email.strip() or source_type not in ('TEST_MANUAL', 'TEST_AUTO', 'REAL_USER'):
            raise ValueError('User name/email/source_type are required.')
        with self.transaction() as conn:
            conn.execute('''INSERT INTO public.users (name,email,department,job_title,grade,source_type)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (email) DO NOTHING''',
                (username.strip(), email.strip(), department, job_title, grade, source_type))
            row = conn.execute('SELECT user_id FROM public.users WHERE email=%s', (email.strip(),)).fetchone()
        return user_id_value(row['user_id'])

    def create_roadmap_session(self, user_id=None, project_name=None, status='DRAFT', current_stage=1, **kwargs):
        uid = user_id_value(user_id)
        if not project_name or not str(project_name).strip():
            raise ValueError('Project name is required.')
        row = self.table('roadmap_sessions').insert({'user_id': uid, 'project_name': str(project_name).strip()[:255],
            'status': status, 'current_stage': current_stage}).execute().data[0]
        return row['session_id']

    def get_roadmap_session(self, session_id): return self._one('roadmap_sessions', {'session_id': session_id})

    def list_roadmap_sessions(self, user_id=None, limit=10):
        query = self.table('roadmap_sessions').select().order('created_at', desc=True).limit(limit)
        if user_id is not None:
            query.eq('user_id', user_id_value(user_id))
        return query.execute().data

    def update_session_stage(self, session_id, stage_num, status='IN_PROGRESS'):
        if type(stage_num) is not int or not 1 <= stage_num <= 7:
            raise ValueError('stage_num must be 1..7')
        rows = self.table('roadmap_sessions').update({'current_stage': stage_num, 'status': status,
            'updated_at': datetime.now(timezone.utc)}).eq('session_id', session_id).execute().data
        if not rows:
            raise ValueError('Project session does not exist.')
        return True

    def save_project_plan(self, session_id, plan_data, **kwargs):
        if not isinstance(plan_data, dict):
            raise ValueError('Project plan must be a JSON object.')
        fields = ('draft_plan','refined_plan','project_goal','project_scope','tech_stack','milestones','key_issues','deliverables','validation_criteria')
        # Only supplied fields are updated; finalizing must not erase the original draft.
        payload = {key: plan_data[key] for key in fields if key in plan_data}
        if not payload:
            raise ValueError('Project plan contains no supported fields.')
        payload.update(session_id=session_id, updated_at=datetime.now(timezone.utc))
        self.table('project_plans').upsert(payload, on_conflict='session_id').execute()
        return True

    def get_project_plan(self, session_id): return self._one('project_plans', {'session_id': session_id})

    def save_plan_conversation(self, session_id, role, content, sequence_no):
        self.table('plan_conversations').insert({'session_id': session_id, 'role': role,
            'content': content, 'sequence_no': sequence_no}).execute()
        return True

    def get_plan_conversations(self, session_id): return self._rows('plan_conversations', {'session_id': session_id}, 'sequence_no')

    @staticmethod
    def _assessment_type(value):
        if value not in ('PRE', 'POST'):
            raise ValueError('assessment_type must be PRE or POST')
        return value

    def save_assessment_questions(self, session_id, assessment_type, questions):
        self._assessment_type(assessment_type)
        validate_questions(questions, require_ids=False)
        rows = [{'session_id': session_id, 'assessment_type': assessment_type, 'skill_id': q.get('skill_id'),
                 'skill': q.get('skill', q.get('skill_name', '')), 'question': q['question'],
                 'options': q.get('options', []), 'sequence_no': i} for i, q in enumerate(questions, 1)]
        with self.transaction() as conn:
            session = self._session_lock(conn, session_id)
            allowed_stages = (2, 3) if assessment_type == 'PRE' else (5, 6)
            if session['current_stage'] not in allowed_stages:
                raise ValueError('이 단계에서는 설문 문항을 변경할 수 없습니다.')
            catalog = {r['skill_id']: r['name'] for r in conn.execute('SELECT skill_id, name FROM public.skill_tags').fetchall()}
            if any(catalog.get(q['skill_id']) != q['skill'] for q in questions):
                raise ValueError('설문의 스킬 ID와 DB 태그 이름이 일치하지 않습니다.')
            if conn.execute('SELECT 1 FROM public.assessment_responses WHERE session_id=%s AND assessment_type=%s LIMIT 1',
                            (session_id, assessment_type)).fetchone():
                raise ValueError('Answered questions cannot be replaced. Start a new project assessment.')
            self.table('assessment_questions').delete().eq('session_id', session_id).eq('assessment_type', assessment_type)._run(conn)
            self.table('assessment_questions').insert(rows)._run(conn)
        return True

    def get_assessment_questions(self, session_id, assessment_type):
        return self._rows('assessment_questions', {'session_id': session_id, 'assessment_type': self._assessment_type(assessment_type)}, 'sequence_no')

    def save_assessment_responses(self, session_id, assessment_type, responses):
        self._assessment_type(assessment_type)
        if not responses:
            raise ValueError('Empty assessment responses cannot replace saved responses.')
        ids = [str(r['question_id']) for r in responses]
        if len(set(ids)) != len(ids) or any(type(r.get('score')) is not int or not 1 <= r['score'] <= 5 for r in responses):
            raise ValueError('Duplicate question IDs or invalid 1..5 scores.')
        rows = [{'session_id': session_id, 'assessment_type': assessment_type, 'question_id': r['question_id'],
                 'selected_option': str(r.get('selected_option', '')), 'score': r['score']} for r in responses]
        with self.transaction() as conn:
            session = self._session_lock(conn, session_id)
            expected_stage = 3 if assessment_type == 'PRE' else 6
            if session['current_stage'] != expected_stage:
                raise ValueError('다음 단계에서 사용한 설문 응답은 변경할 수 없습니다.')
            questions = conn.execute('SELECT question_id, skill_id, skill, question, options, sequence_no FROM public.assessment_questions WHERE session_id=%s AND assessment_type=%s ORDER BY sequence_no',
                                     (session_id, assessment_type)).fetchall()
            validate_responses(questions, responses)
            self.table('assessment_responses').delete().eq('session_id', session_id).eq('assessment_type', assessment_type)._run(conn)
            self.table('assessment_responses').insert(rows)._run(conn)
        return True

    def get_assessment_responses(self, session_id, assessment_type):
        return self._rows('assessment_responses', {'session_id': session_id, 'assessment_type': self._assessment_type(assessment_type)})

    def save_roadmap_generations(self, session_id, generations):
        if not generations or any(g.get('status') == 'FAILED' or not isinstance(g.get('roadmap_content'), dict)
                                  or g['roadmap_content'].get('error') for g in generations):
            raise ValueError('Only successful roadmap generations may be saved.')
        rows = [{'session_id': session_id, 'strategy_type': g['strategy_type'], 'strategy_title': g.get('strategy_title'),
                 'summary': g.get('summary'), 'roadmap_content': g['roadmap_content'], 'generation_order': i}
                for i, g in enumerate(generations, 1)]
        with self.transaction() as conn:
            self._session_lock(conn, session_id)
            if conn.execute('SELECT 1 FROM public.roadmap_selections WHERE session_id=%s', (session_id,)).fetchone():
                raise ValueError('Selected roadmaps cannot be replaced. Start a new project session.')
            self.table('roadmap_generations').delete().eq('session_id', session_id)._run(conn)
            self.table('roadmap_generations').insert(rows)._run(conn)
        return True

    def get_roadmap_generations(self, session_id): return self._rows('roadmap_generations', {'session_id': session_id}, 'generation_order')

    def save_roadmap_selection(self, session_id, generation_id):
        self.table('roadmap_selections').upsert({'session_id': session_id, 'generation_id': generation_id,
            'selected_at': datetime.now(timezone.utc)}, on_conflict='session_id').execute()
        return True

    def get_roadmap_selection(self, session_id): return self._one('roadmap_selections', {'session_id': session_id})

    def save_roadmap_report(self, session_id, generation_id, report_content):
        self.table('roadmap_reports').upsert({'session_id': session_id, 'generation_id': generation_id,
            'report_content': report_content, 'updated_at': datetime.now(timezone.utc)}, on_conflict='session_id').execute()
        return True

    def select_roadmap_with_report(self, session_id, generation_id, report_content):
        with self.transaction() as conn:
            self._session_lock(conn, session_id)
            self.table('roadmap_selections').upsert({'session_id': session_id, 'generation_id': generation_id,
                'selected_at': datetime.now(timezone.utc)}, on_conflict='session_id')._run(conn)
            self.table('roadmap_reports').upsert({'session_id': session_id, 'generation_id': generation_id,
                'report_content': report_content, 'updated_at': datetime.now(timezone.utc)}, on_conflict='session_id')._run(conn)
        return True

    def get_roadmap_report(self, session_id): return self._one('roadmap_reports', {'session_id': session_id})

    def save_assessment_report(self, session_id, report_content, report_type='PRE_POST'):
        self.table('assessment_reports').upsert({'session_id': session_id, 'report_type': report_type,
            'report_content': report_content}, on_conflict='session_id').execute()
        return True

    def get_assessment_report(self, session_id): return self._one('assessment_reports', {'session_id': session_id})

    def get_active_prompts(self):
        rows = self._rows('prompt_versions', {'is_active': True})
        if len({r['agent_id'] for r in rows}) != len(rows):
            raise ValueError('Multiple active prompt versions for an agent.')
        return {r['agent_id']: r for r in rows}

    def get_active_prompt_for_agent(self, agent_id): return self.get_active_prompts().get(agent_id)

    def get_prompt_history(self, agent_id):
        return self.table('prompt_versions').select().eq('agent_id', agent_id).order('version_num', desc=True).execute().data

    def create_prompt_version(self, **kwargs): return self.table('prompt_versions').insert(kwargs).execute().data[0]

    def activate_prompt_version(self, prompt_version_id, agent_id):
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('prompt:' + agent_id,))
            target = conn.execute('SELECT 1 FROM public.prompt_versions WHERE prompt_version_id=%s AND agent_id=%s',
                                   (prompt_version_id, agent_id)).fetchone()
            if not target:
                raise ValueError('Prompt version does not belong to this agent.')
            self.table('prompt_versions').update({'is_active': False, 'deactivated_at': datetime.now(timezone.utc)}).eq('agent_id', agent_id)._run(conn)
            self.table('prompt_versions').update({'is_active': True, 'deactivated_at': None}).eq('prompt_version_id', prompt_version_id)._run(conn)
        return True

    def rollback_prompt_version(self, agent_id):
        current = self.get_active_prompt_for_agent(agent_id)
        if not current or not current.get('parent_version_id'):
            return {'success': False, 'message': 'No parent version'}
        parent = current['parent_version_id']
        return {'success': self.activate_prompt_version(parent, agent_id), 'restored_parent_id': parent}

    def log_prompt_evaluation(self, **kwargs): return self.table('prompt_evaluations').insert(kwargs).execute().data[0]['evaluation_id']

    def log_prompt_optimization(self, **kwargs):
        self.table('prompt_optimizations').insert(kwargs).execute()
        return True

    def get_selection_statistics(self, user_type='REAL_USER'):
        with self.transaction() as conn:
            rows = conn.execute('''SELECT g.strategy_type, COUNT(*) AS n FROM public.roadmap_selections s
                JOIN public.roadmap_generations g ON g.generation_id=s.generation_id AND g.session_id=s.session_id
                JOIN public.roadmap_sessions r ON r.session_id=s.session_id JOIN public.users u USING(user_id)
                WHERE (%s::text IS NULL OR u.source_type=%s) GROUP BY g.strategy_type''', (user_type, user_type)).fetchall()
        counts = {r['strategy_type']: r['n'] for r in rows}
        total = sum(counts.values())
        return {'total_selections': total, 'counts': counts, 'rates': {key: count / total for key, count in counts.items()}}

    def check_prompt_update_trigger(self, threshold=10, user_type='REAL_USER'):
        stats = self.get_selection_statistics(user_type)
        rates = stats['rates']
        return {'should_trigger': stats['total_selections'] >= threshold, 'current_real_selections': stats['total_selections'],
                'lowest_agent_id': min(rates, key=rates.get) if rates else None, 'selection_rates': rates}


db_client = DatabaseClient()
__all__ = ['DatabaseClient', 'DatabaseOperationError', 'db_client', 'user_id_value']
