#!/usr/bin/env python
"""웹 연결용 정성 검증 단독 실행·확인 도구 (AI 참고 의견)

웹에 붙이기 전에 팀장 PC에서 실제 AI 호출을 한 번 확인할 때 쓴다. engine 폴더에서:

  python run_web_qual.py --dry-run                       # 예상 비용만(호출 없음)
  python run_web_qual.py --mock ok                       # 모의 응답으로 흐름만(비용 0)
  python run_web_qual.py                                 # 실제 호출(샘플 서류 dataset/documents.json, 약 $0.1, 약 10초)
  python run_web_qual.py --weak                          # 부실한 서술 1건(시험 세트 Q03-D2)을 넣어 실제 호출
  python run_web_qual.py --documents dataset/documents_dirty.json --out result.json

실제 호출은 환경 변수 ANTHROPIC_API_KEY가 있어야 한다(없으면 안내 한 줄만 나온다). 결과를 --out 으로 저장하면
웹이 JSON 내려받기에 넣을 구조(qualitative_result_v1)와 같다. 같은 서류를 다시 실행하면 engine/ai_cache/ 의 기억을 써서 비용 0이다
(--no-cache로 끔).
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corsia.ai import web_qual as W  # noqa: E402

HERE = Path(__file__).resolve().parent
TESTSET = HERE.parent / '7_qualitative' / 'qual_testset_v1.json'


def apply_testset_case(documents, case_id):
    """시험 세트 사례의 서술(입력)을 서류 JSON에 덮어쓴 사본을 돌려준다(시연용 부실 서술 만들기)."""
    t = json.loads(TESTSET.read_text(encoding='utf-8'))
    case = next(c for c in t['cases'] if c['case_id'] == case_id)
    d = copy.deepcopy(documents)
    inner = d['documents'] if 'documents' in d else d
    for field, text in {**case['inputs'], **(case.get('companions') or {})}.items():
        doc, key = field.split('.', 1)
        inner.setdefault(doc, {})[key] = text
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description='정성 검증(AI 참고 의견) 단독 실행')
    ap.add_argument('--documents', default=str(HERE / 'dataset' / 'documents.json'))
    ap.add_argument('--weak', action='store_true', help='시험 세트 Q03-D2(노선 결정 절차가 부실한 사례)를 서류에 넣고 실행')
    ap.add_argument('--mock', choices=['ok', 'weak', 'fake_quote', 'bad_json', 'network', 'slow'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--workers', type=int, default=W.MAX_WORKERS_DEFAULT)
    ap.add_argument('--max-usd', type=float, default=W.MAX_USD_DEFAULT)
    ap.add_argument('--gap', type=float, default=None, help='코드가 계산한 데이터갭 비율(%%). 안 주면 서류의 ER.data_gap_pct')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--out')
    a = ap.parse_args(argv)

    docs = json.loads(Path(a.documents).read_text(encoding='utf-8'))
    if a.weak:
        docs = apply_testset_case(docs, 'Q03-D2')
    use_cache = not (a.no_cache or a.mock)                    # 모의 응답은 기억을 읽지도 쓰지도 않는다
    cache = W.CACHE_DIR if use_cache else None
    if a.mock:
        print('(모의 응답: 디스크 기억 engine/ai_cache를 읽지도 쓰지도 않습니다)')
    est = W.estimate_qualitative(docs, a.gap, a.max_usd, cache, use_cache)
    print(f'예상: 호출 {est["n_calls"]}회(기억에서 {est["n_cached"]}회, 건너뜀 {est["n_skipped"]}회) · 약 ${est["estimated_usd"]:.3f} (상한 ${a.max_usd:.2f}) · 키 {"있음" if est["key_set"] else "없음"}')
    for s in est['skipped']:
        print(f'  - {s["rule_id"]}: {s["state"]} — {s["message"]}')
    if a.dry_run:
        return 0
    call_fn = W.mock_call(a.mock) if a.mock else None
    result = None
    for ev in W.iter_qualitative(docs, a.gap, call_fn, a.max_usd, a.workers, cache, use_cache):
        if ev['type'] == 'item':
            it = ev['item']
            cost = f' · ${it["cost_usd"]:.4f}' if it['cost_usd'] else (' · 기억' if it['cached'] else '')
            cost += f' · 출처 {it["source"]}' if it.get('source') else ''
            print(f'  [{ev["done"]}/{ev["total"]}] {it["rule_id"]} {it["status"]:18} {it["verdict"] or "-":8}{cost} · {it["seconds"]:.1f}초'
                  + (f' · 인용 문제 {it["fake_citations"]}건' if it['fake_citations'] else ''))
        elif ev['type'] == 'done':
            result = ev['result']
    print(f'\n상태 {result["status"]} · 판정 {result["summary"]["verdicts"]} · 실제 비용 ${result["cost"]["actual_usd"]:.4f} · {result["timing"]["seconds"]:.1f}초')
    if result['status_message']:
        print('안내:', result['status_message'])
    for it in result['items']:
        if it['status'] == 'ok':
            print(f'\n{it["rule_id"]} {it["verdict"]} — {it["reason"]}')
            for c in it['checks']:
                mark = ' [인용 문제]' if c['citation_problem'] else ''
                print(f'   {c["id"]}({c["kind"]}) {c["status"]}{mark}' + (f' — {c["note"]}' if c.get('note') else ''))
            for mp in it['missing_points']:
                print(f'   보완: {mp}')
    if a.out:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'\n저장: {a.out}')
    return 0 if result['status'] in ('ok', 'no_key', 'no_text') else 1


if __name__ == '__main__':
    sys.exit(main())
