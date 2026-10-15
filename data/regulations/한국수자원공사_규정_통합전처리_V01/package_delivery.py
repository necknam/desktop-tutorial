"""Package K-water regulation data without external comparison documents."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
EXTERNAL = Path('provenance/external_sources')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(version: str) -> None:
    if re.fullmatch(r'V\d{2,}', version) is None:
        raise ValueError('전달본 버전은 V08 같은 형식이어야 합니다.')
    regulation_files = json.loads((ROOT / 'data/regulation_file.json').read_text(encoding='utf-8'))
    source_files = sorted(path for path in (ROOT / 'source').rglob('*') if path.is_file())
    if len(regulation_files) != 28 or len(source_files) != 28:
        raise ValueError('공사 규정 원문 28건을 확인할 수 없습니다.')
    expected = {row['storage_key'] for row in regulation_files}
    actual = {path.relative_to(ROOT).as_posix() for path in source_files}
    if expected != actual or any(row['source_type'] not in {'alio_public', 'alio_user_supplied'}
                                 for row in regulation_files):
        raise ValueError('공사 규정 원문 목록과 전처리 입력 목록이 다릅니다.')
    if any(sha(ROOT / row['storage_key']) != row['file_hash'] for row in regulation_files):
        raise ValueError('공사 규정 원문의 해시가 입력 목록과 다릅니다.')

    files = sorted(path for path in ROOT.rglob('*') if path.is_file()
                   and EXTERNAL not in path.relative_to(ROOT).parents
                   and '__pycache__' not in path.parts and path.suffix != '.pyc')
    if any(EXTERNAL in path.relative_to(ROOT).parents for path in files):
        raise ValueError('외부 비교 자료가 전달 파일에 포함되었습니다.')
    target = ROOT.parent / f'한국수자원공사_규정_통합전처리_전달본_{version}.zip'
    with ZipFile(target, 'x', compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, ROOT.name + '/' + path.relative_to(ROOT).as_posix())
    with ZipFile(target) as archive:
        names = archive.namelist()
        if archive.testzip() is not None or any('/provenance/external_sources/' in name for name in names):
            raise RuntimeError('전달 파일에 오류 또는 외부 비교 자료가 있습니다.')
        for path in files:
            name = ROOT.name + '/' + path.relative_to(ROOT).as_posix()
            if hashlib.sha256(archive.read(name)).hexdigest() != sha(path):
                raise RuntimeError('전달 파일의 내용이 원본과 다릅니다: ' + name)
    print(json.dumps({'archive': str(target), 'files': len(files),
                      'internal_regulation_pdfs': len(source_files),
                      'external_reference_files_in_archive': 0,
                      'bytes': target.stat().st_size, 'sha256': sha(target)}, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) == 2 else 'V08')
