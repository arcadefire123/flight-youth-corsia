#!/usr/bin/env python
"""절차 안내 질문-정답 평가 실행기 (7단계 RAG)

같은 질문으로 세 변형을 비교한다: rag(검색해서 붙이기) / full(법령·고시 전체를 통째로 넣기) / closed(자료 없이, 대조군).
정답은 팀원이 법령 원문을 보고 직접 쓴 질문-정답 양식(7_rag/qa_testset_template_v1.xlsx 모양)이다.

쓰는 법 (engine 폴더에서)
  python rag_eval.py --qa <양식 파일·폴더> --retrieval-only                 # AI 호출 없이 검색 재현율만(비용 0)
  python rag_eval.py --qa <양식> --variant rag,full,closed --dry-run         # 비용 추정만
  python rag_eval.py --qa <양식> --variant rag,full,closed --label qa30      # 실제 실행
  python rag_eval.py --qa <양식> --purpose 개발용 --variant rag               # 개발용 질문으로 검색·지시문 조정할 때
  --purpose 채점용|개발용|all(기본 all)  · --k 검색 조각 수(기본 5) · --max-usd 상한(기본 5) · --mock perfect|noisy
결과: 7_rag/runs/<시각>_<label>/ 에 config.json, results.jsonl, summary.md, qa_results.xlsx(사람 판정 칸 포함)
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corsia.ai.models import MODELS, estimate_cost  # noqa: E402
from corsia.rag import answer as A  # noqa: E402
from corsia.rag import corpus as K  # noqa: E402
from corsia.rag import qa as Q  # noqa: E402
from corsia.rag import report as RP  # noqa: E402
from corsia.rag.search import Index  # noqa: E402

OUT_ROOT = K.RAG_DIR / 'runs'
EST_OUT = 250      # 첫 실제 호출 실측 출력 114토큰 + 여유
MAX_CONSECUTIVE_ERRORS = 3


class _Fake:
    def __init__(self, text, usage, model_key):
        self.text, self.stop_reason, self.truncated = text, 'end_turn', False
        self.cost = estimate_cost(model_key, usage)


def make_mock(kind, items):
    """질문 글로 정답을 찾아 답하는 가짜 AI. perfect: 정답대로 / noisy: 근거를 일부러 엉뚱하게(가짜 인용 포함)"""
    by_q = {x['question']: x for x in items}
    state = {'n': 0}

    def fn(user, model_key='sonnet', system='', max_tokens=0, thinking=None, effort=None, cache_prefix=None):
        state['n'] += 1
        q = by_q[re.search(r'## 질문\n(.*?)\n', user).group(1)]
        if not q['answerable']:
            obj = {'answerable': False, 'answer': '제공된 자료에서 근거를 찾지 못했습니다', 'citations': []}
        else:
            cites = list(q['gold_cites'])
            if kind == 'noisy':
                cites = ['법 제99조'] if state['n'] % 2 else cites[:1]
            obj = {'answerable': True, 'answer': f"{q['gold_answer']} ({' '.join(q['key_values'])})" if kind == 'perfect' or state['n'] % 2 == 0 else '잘 모르겠습니다',
                   'citations': cites}
        text = json.dumps(obj, ensure_ascii=False)
        n_in = len(system) + len(user) + len(cache_prefix or '')
        usage = {'input_tokens': int(n_in * 0.4), 'output_tokens': 150}
        if cache_prefix:
            usage = {'input_tokens': 200, 'output_tokens': 150, 'cache_read_input_tokens': int(len(cache_prefix) * 0.9)}
        return _Fake(text, usage, model_key)
    return fn


def estimate(items, variants, idx, chunks, args):
    """변형별 (입력 토큰, 출력 토큰, 비용 USD). full은 캐시가 안 먹는 경우의 비용(보수적)과 캐시가 먹는 경우를 따로 돌려준다."""
    out = {}
    n = len(items)
    prefix_tok = K.tokens_est(A.full_prefix(chunks))
    for v in variants:
        if v == 'rag':
            tin = sum(K.tokens_est(A.build_rag(x['question'], idx, args.k)[0] + A.build_rag(x['question'], idx, args.k)[1]) for x in items)
        elif v == 'full':
            tin = n * (prefix_tok + K.tokens_est(A.SYSTEM) + 150)
        else:
            tin = sum(K.tokens_est(''.join(A.build_closed(x['question']))) for x in items)
        tout = n * EST_OUT
        usd = estimate_cost(args.model, {'input_tokens': tin, 'output_tokens': tout})['usd']
        out[v] = (tin, tout, usd)
    cached = None
    if 'full' in variants:
        cached = estimate_cost(args.model, {'input_tokens': n * 200, 'output_tokens': n * EST_OUT, 'cache_write_input_tokens': 0,
                                            'cache_creation_input_tokens': prefix_tok, 'cache_read_input_tokens': prefix_tok * max(n - 1, 0)})['usd']
    return out, cached


def main(argv=None):
    ap = argparse.ArgumentParser(description='절차 안내 질문-정답 평가')
    ap.add_argument('--qa', nargs='+', required=True)
    ap.add_argument('--variant', default='rag,full,closed')
    ap.add_argument('--purpose', default='all', choices=['all', '채점용', '개발용'])
    ap.add_argument('--k', type=int, default=5)
    ap.add_argument('--model', default='sonnet', choices=list(MODELS))
    ap.add_argument('--thinking', default='adaptive')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--max-tokens', type=int, default=3000)
    ap.add_argument('--max-usd', type=float, default=5.0)
    ap.add_argument('--label', default='')
    ap.add_argument('--out', default=str(OUT_ROOT))
    ap.add_argument('--retrieval-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mock', choices=['perfect', 'noisy'])
    a = ap.parse_args(argv)

    items = Q.read_qa(a.qa)
    if not items:
        print('채워진 질문이 없습니다(질문 열이 빈 양식).')
        return 1
    if a.purpose != 'all':
        items = [x for x in items if x['purpose'] == a.purpose]
    chunks = K.load()
    idx = Index(chunks)
    n_ans = sum(1 for x in items if x['answerable'])
    print(f'질문 {len(items)}개(답이 있는 것 {n_ans}개, 문서에 없음 {len(items) - n_ans}개) · 용도 {a.purpose} · 조각 {len(chunks)}개')
    retrieval = Q.retrieval_recall(items, idx)
    hit, n, miss = retrieval
    print('검색만(AI 없음): 정답 근거가 상위 k개 안에 있는 비율 — ' + ' · '.join(f'k={k}: {hit[k]}/{n}' for k in hit))
    if a.retrieval_only:
        for m in miss:
            print(f'  놓침 {m["no"]}번 「{m["question"][:40]}」 정답 {m["gold"]} / 상위5 {m["top5"]}')
        return 0
    variants = [v.strip() for v in a.variant.split(',') if v.strip()]
    bad = [v for v in variants if v not in ('rag', 'full', 'closed')]
    if bad:
        raise SystemExit(f'모르는 변형: {bad}')
    est, cached = estimate(items, variants, idx, chunks, a)
    total = sum(v[2] for v in est.values())
    for v in variants:
        print(f'  {v}: 입력 약 {est[v][0]:,} · 출력 약 {est[v][1]:,} 토큰 · 비용 약 ${est[v][2]:.3f}')
    if cached is not None:
        print(f'  (full은 캐시가 먹으면 약 ${cached:.2f}로 줄어듭니다. 상한 판단에는 캐시가 안 먹는 경우를 씁니다)')
    print(f'  합계 추정 ${total:.3f} (상한 ${a.max_usd:.2f})')
    if a.dry_run:
        print('  (dry-run: AI를 부르지 않음)')
        return 0
    if not a.mock and total > a.max_usd:
        print('  [멈춤] 추정 비용이 상한을 넘습니다. 범위를 줄이거나 --max-usd를 올리세요(크레딧 안에서, 팀장 판단).')
        return 2
    call_fn = make_mock(a.mock, items) if a.mock else __import__('corsia.ai.client', fromlist=['call']).call

    started = datetime.now()
    label = a.label or (f'mock-{a.mock}' if a.mock else 'qa')
    run_dir = Path(a.out) / f'{started:%Y%m%d-%H%M%S}_{label}'
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = {'label': label, 'started': started.isoformat(timespec='seconds'), 'variants': variants, 'k': a.k, 'model': a.model,
           'model_id': MODELS[a.model].model_id, 'thinking': a.thinking, 'effort': a.effort, 'purpose': a.purpose,
           'n_questions': len(items), 'corpus_chunks': len(chunks), 'mock': a.mock, 'max_usd': a.max_usd}
    (run_dir / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    errors, spent = 0, 0.0
    with open(run_dir / 'results.jsonl', 'w', encoding='utf-8') as fout:
        for v in variants:
            print(f'\n[{v}] 시작')
            for i, q in enumerate(items, 1):
                t0 = time.time()
                try:
                    res = A.ask(q['question'], v, idx, chunks, call_fn, k=a.k, model=a.model, effort=a.effort, thinking=a.thinking, max_tokens=a.max_tokens)
                except Exception as exc:
                    errors += 1
                    print(f'  [{v} {i}/{len(items)}] 오류: {exc}')
                    if errors >= MAX_CONSECUTIVE_ERRORS:
                        print('  [멈춤] 같은 오류가 연달아 났습니다. 설정을 확인하세요.')
                        RP.write_summary(run_dir, cfg, retrieval)
                        return 3
                    continue
                errors = 0
                j = Q.judge(q, res)
                spent += res['usd']
                fout.write(json.dumps({'no': q['no'], 'variant': v, 'question': q['question'], 'type': q['type'], 'purpose': q['purpose'],
                                       'answerable': q['answerable'], 'gold_answer': q['gold_answer'], 'key_values': q['key_values'],
                                       'gold_cites': q['gold_cites'], 'res': res, 'j': j, 'seconds': round(time.time() - t0, 1)}, ensure_ascii=False) + '\n')
                fout.flush()
                mark = '✓' if j['correct'] else '✗'
                print(f'  [{v} {i}/{len(items)}] {mark} 답 {"○" if j["answer_ok"] else "×"} 근거 {"○" if j["cite_hit"] else ("×" if j["cite_hit"] is not None else "-")}'
                      f'{" 가짜인용" if j["fake_cite"] else ""} · ${res["usd"]:.4f} (누계 ${spent:.4f})')
    cfg['cost_usd'] = round(spent, 4)
    (run_dir / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    RP.write_summary(run_dir, cfg, retrieval)
    RP.write_xlsx(run_dir)
    print(f'\n완료 · 예상 비용 ${spent:.4f} · 결과: {run_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
