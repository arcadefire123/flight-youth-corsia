#!/usr/bin/env python
"""법령·고시 PDF 4종을 조각으로 나눠 7_rag/corpus_chunks_v1.jsonl 로 저장한다 (7단계 RAG).

  python rag_ingest.py <PDF가 있는 폴더>             # 법률·시행령·시행규칙·지침 PDF를 이름으로 찾음
  python rag_ingest.py --text-dir <폴더>              # 미리 글로 바꿔 둔 법.txt 시행령.txt 시행규칙.txt 지침.txt 를 읽음

이미 만들어 둔 corpus_chunks_v1.jsonl 이 있으면 이 도구는 필요 없다(법령이 개정되었을 때만 다시 만든다).
PDF → 글은 pdftotext가 있으면 그것을(-layout), 없으면 pypdf를 쓴다.
"""
import argparse
import glob
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corsia.rag import corpus as K  # noqa: E402

PATTERNS = {'법': '03_*법률*.pdf', '시행령': '04_*시행령*.pdf', '시행규칙': '05_*시행규칙*.pdf', '지침': '*관리에_관한_지침*.pdf'}


def pdf_to_text(path):
    if shutil.which('pdftotext'):
        out = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True, check=True)
        return out.stdout.decode('utf-8', errors='ignore')
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            raise SystemExit('[멈춤] PDF를 읽을 도구가 없습니다. 아나콘다 프롬프트에서  pip install pypdf  를 실행하세요.')
    return '\f'.join((p.extract_text() or '') for p in PdfReader(str(path)).pages)


def main(argv=None):
    ap = argparse.ArgumentParser(description='법령·고시 조각 만들기')
    ap.add_argument('folder', nargs='?')
    ap.add_argument('--text-dir')
    ap.add_argument('--out', default=str(K.CHUNKS_PATH))
    a = ap.parse_args(argv)
    texts = {}
    if a.text_dir:
        for doc in PATTERNS:
            texts[doc] = (Path(a.text_dir) / f'{doc}.txt').read_text(encoding='utf-8', errors='ignore')
    elif a.folder:
        for doc, pat in PATTERNS.items():
            hits = sorted(glob.glob(str(Path(a.folder) / pat)))
            if not hits:
                raise SystemExit(f'[멈춤] {doc} PDF를 {a.folder} 에서 찾지 못했습니다(찾는 이름: {pat}).')
            texts[doc] = pdf_to_text(hits[0])
    else:
        raise SystemExit('PDF가 있는 폴더를 주세요: python rag_ingest.py <폴더>')
    chunks = K.build(texts)
    K.save(chunks, a.out)
    kinds = Counter((c['doc'], c['kind']) for c in chunks)
    print(f'조각 {len(chunks)}개 저장: {a.out}')
    print('  ' + ' · '.join(f'{d} {k} {n}' for (d, k), n in kinds.items()))
    print(f'  본문 약 {sum(K.tokens_est(c["text"]) for c in chunks):,} 토큰, 통째 넣기(ID 포함) 약 {K.tokens_est(K.full_context(chunks)):,} 토큰')
    dup = sum(1 for c in chunks if '#' in c['id'])
    if dup:
        print(f'  [참고] 같은 ID가 반복되어 「#2」 같은 꼬리를 붙인 조각 {dup}개(별표 안에서 a)·b)가 되풀이되는 구간)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
