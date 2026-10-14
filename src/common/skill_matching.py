"""Explicit existing-name aliases and evidence-based skill matching. YAML remains authoritative."""
import re
from src.common.config_loader import get_rules_config

ALIASES = {'SQL 기본 문법 & 조인': 'SQL 기본 문법 & 조인', 'SQL 쿼리 튜닝 & 인덱스 최적화': 'SQL 쿼리 튜닝 & 인덱스 최적화', 'RDBMS 데이터 모델링 & 정규화': 'RDBMS 데이터 모델링', 'NoSQL & 분산 데이터베이스': 'NoSQL & 분산 DB', 'ETL & 데이터 파이프라인 구축': 'ETL & 데이터 파이프라인', '데이터 웨어하우스 & OLAP': '데이터 웨어하우스 & OLAP', '탐색적 데이터 분석(EDA) & 특성공학': '탐색적 데이터 분석(EDA) & 전처리', '머신러닝 지도/비지도 모델링': '머신러닝 지도/비지도 학습', 'MLOps & 모델 서빙 인프라': 'MLOps & 모델 서빙', '딥러닝 심층신경망(CNN/RNN) 아키텍처': '딥러닝 신경망 아키텍처', 'PyTorch/TensorFlow 딥러닝 구현': 'PyTorch/TensorFlow 딥러닝 구현', '자연어처리(NLP) & 거대언어모델(LLM)': '자연어 처리(NLP) & LLM 기초', 'RAG & 프롬프트 엔지니어링': 'RAG & 생성형 AI 에이전트', '컴퓨터 비전 & 이미지 처리': '컴퓨터 비전(CV) 모델링', '리눅스 시스템 & 쉘 스크립트': '리눅스 환경 및 Bash 스크립팅', 'TCP/IP & 컴퓨터 네트워크 기초': 'TCP/IP 네트워크 프로토콜', '클라우드 인프라 아키텍처 (AWS/GCP)': '클라우드 컴퓨팅 아키텍처 (AWS/Azure)', '클라우드 인프라 보안 & IAM': '클라우드 IAM & 네트워크 보안', 'Docker 컨테이너 가상화': 'Docker 컨테이너 가상화', 'Kubernetes 오케스트레이션': 'Kubernetes 클러스터 오케스트레이션', 'CI/CD 자동화 배포 파이프라인': 'CI/CD 배포 자동화 파이프라인', 'Python 심화 & 비동기 프로그래밍': 'Python 프로그래밍 & 비동기 I/O', '자료구조 & 코딩테스트 알고리즘': '자료구조 & 알고리즘 최적화', '운영체제 커널 & 프로세스 동기화': '운영체제(OS) & 동시성 제어', '컴퓨터 구조 & 메모리 계층 최적화': '컴퓨터 구조 & 메모리 관리', '객체지향 설계(OOP) & 디자인 패턴': '객체지향 설계(OOP) & 디자인 패턴', 'RESTful API 아키텍처 설계': 'RESTful API 서버 아키텍처', '대용량 트래픽 처리 & 캐싱(Redis)': '대용량 트래픽 처리 & 캐싱', '마이크로서비스 아키텍처(MSA)': '마이크로서비스(MSA) 설계', '웹 프레임워크 실무 (FastAPI/Django)': '웹 프레임워크 (FastAPI/Django)', '시스템 프로그래밍 & 저수준 I/O': '시스템 프로그래밍 & POSIX API', '동시성 제어 & 멀티스레딩': '멀티스레딩 & 비동기 동시성', '시스템 & 리눅스 커널 보안': '시스템 & OS 보안 강화', '웹 애플리케이션 취약점 진단': '웹 애플리케이션 취약점 진단', '네트워크 보안 & 침입탐지(IDS/IPS)': '네트워크 보안 & 침입 탐지', '암호학 기초 & 데이터 거버넌스': '데이터 거버넌스 & 보안 규정 준수'}

# Literal domain terms supplement existing YAML patterns; never attach a default skill.
TERMS = {
 'SQL 기본 문법 & 조인': [r'\bsql\b', r'postgresql', r'mysql'],
 '머신러닝 지도/비지도 학습': [r'machine learning', r'머신\s*러닝', r'기계\s*학습'],
 '딥러닝 신경망 아키텍처': [r'deep learning', r'딥\s*러닝'],
 '자연어 처리(NLP) & LLM 기초': [r'자연어\s*처리', r'natural language', r'large language model'],
 'RAG & 생성형 AI 에이전트': [r'retrieval.augmented', r'검색\s*증강', r'generative ai'],
 '컴퓨터 비전(CV) 모델링': [r'컴퓨터\s*비전', r'computer vision', r'이미지\s*(분류|인식|처리)', r'object detection', r'opencv'],
 '리눅스 환경 및 Bash 스크립팅': [r'\blinux\b', r'\bbash\b', r'리눅스'],
 '클라우드 컴퓨팅 아키텍처 (AWS/Azure)': [r'클라우드\s*(컴퓨팅|아키텍처)', r'cloud architecture'],
 'Python 프로그래밍 & 비동기 I/O': [r'\bpython\b', r'파이썬'],
 '운영체제(OS) & 동시성 제어': [r'operating system'],
 '객체지향 설계(OOP) & 디자인 패턴': [r'object.oriented', r'design patterns'],
 '마이크로서비스(MSA) 설계': [r'microservices?'],
 '컴퓨터 구조 & 메모리 관리': [r'컴퓨터\s*구조', r'computer architecture', r'마이크로프로세서'],
}

class SkillMatcher:
    def __init__(self, skills, rules=None):
        self.skills = {s['skill_id']:s for s in skills}
        names = {s['name']:s['skill_id'] for s in skills}
        self.patterns = {}
        self.unresolved_rules = []
        for name, patterns in (rules or get_rules_config().get('skill_rules', {})).items():
            canonical = ALIASES.get(name, name)
            sid = names.get(canonical)
            if sid is None:
                self.unresolved_rules.append(name)
                continue
            for pattern in patterns:
                # Avoid RAG matching stoRAGe and DAP matching aDAPter.
                if re.fullmatch(r'[A-Za-z]+', pattern):
                    pattern = r'\b' + pattern + r'\b'
                self.patterns.setdefault(sid, []).append(re.compile(pattern, re.IGNORECASE))
        for name, patterns in TERMS.items():
            if name in names:
                self.patterns.setdefault(names[name], []).extend(re.compile(p,re.IGNORECASE) for p in patterns)

    def match(self, text):
        return {sid: sorted({m.group(0) for p in patterns for m in p.finditer(text)})
                for sid, patterns in self.patterns.items() if any(p.search(text) for p in patterns)}

    def select(self, rows, limit, *, include_unmatched=False):
        # Deterministic coverage across DB skills before filling by source order.
        rows = [r for r in rows if include_unmatched or r.get('matches')]
        chosen, seen = [], set()
        for sid in sorted(self.skills):
            for row in rows:
                key = (row['source'], row['external_key'])
                if sid in row['matches'] and key not in seen:
                    chosen.append(row); seen.add(key); break
            if len(chosen) >= limit: return chosen
        for row in rows:
            key = (row['source'], row['external_key'])
            if key not in seen:
                chosen.append(row); seen.add(key)
                if len(chosen) >= limit: break
        return chosen
