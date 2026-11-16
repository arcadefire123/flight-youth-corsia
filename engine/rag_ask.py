#!/usr/bin/env python
"""절차 안내 한 질문 시험 (7단계 RAG)

  python rag_ask.py "배출량보고서는 매년 언제까지 내야 하나요?"                # 검색해서 붙이기(RAG)
  python rag_ask.py "…" --variant full                                        # 법령·고시 전체를 통째로 넣기
  python rag_ask.py "…" --variant closed                                      # 자료 없이(대조군)
  python rag_ask.py "…" --retrieval-only                                      # AI를 부르지 않고 검색 결과만 보기(비용 0)
답에는 근거 ID가 붙고, 그 ID가 AI에게 준 자료 안에 실제로 있는지(✓/✗)를 코드가 확인한다. AI 답은 검토자 참고 자료다.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corsia.rag import answer as A  # noqa: E402
from corsia.rag import corpus as K  # noqa: E402
from corsia.rag.search import Index  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description='절차 안내 한 질문')
    ap.add_argument('question')
    ap.add_argument('--variant', default='rag', choices=['rag', 'full', 'closed'])
    ap.add_argument('--k', type=int, default=5)
    ap.add_argument('--model', default='sonnet')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--retrieval-only', action='store_true')
    a = ap.parse_args(argv)
    chunks = K.load()
    idx = Index(chunks)
    if a.retrieval_only or a.variant == 'rag':
        hits = idx.search(a.question, k=a.k)
        print('검색된 조각:')
        for c, s, why in hits:
            print(f'  {s:.3f} [{why}] {c["id"]} — {c["text"][:70]}')
        if a.retrieval_only:
            return 0
    from corsia.ai.client import call
    res = A.ask(a.question, a.variant, idx, chunks, call, k=a.k, model=a.model, effort=a.effort)
    print(f'\n답({"근거 있음" if res["answerable"] else "근거 없음" if res["answerable"] is False else "형식 오류"}): {res["answer"]}')
    for c in res['valid']:
        print(f'  ✓ 인용 {c}' + ('' if a.variant != 'closed' else ' (자료 없는 변형이라 확인하지 않음)'))
    for c in res['invalid']:
        print(f'  ✗ 인용 {c} — AI에게 준 자료에 없음(가짜 인용 가능성)')
    print(f'\n예상 비용 ${res["usd"]:.4f} · 토큰 {res["tokens"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
