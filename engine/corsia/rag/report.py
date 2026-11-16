"""질문-정답 평가 결과 요약(md)과 문항별 결과(xlsx) (7단계 RAG)."""
import json
import math
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from . import qa as Q

VNAME = {'rag': '검색해서 붙이기(RAG)', 'full': '통째 넣기', 'closed': '자료 없이(대조군)'}
HEAD = PatternFill('solid', fgColor='D9E1F2')
YELLOW = PatternFill('solid', fgColor='FFF2CC')


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def load_results(run_dir):
    p = Path(run_dir) / 'results.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()] if p.exists() else []


def _pct(k, n):
    return '-' if not n else f'{k}/{n} ({k / n * 100:.0f}%)'


def _ci(k, n):
    w = wilson(k, n)
    return '-' if not w else f'{w[0] * 100:.0f}~{w[1] * 100:.0f}%'


def write_summary(run_dir, cfg, retrieval=None):
    run_dir = Path(run_dir)
    recs = load_results(run_dir)
    by_v = defaultdict(list)
    for r in recs:
        by_v[r['variant']].append({'q': {'answerable': r['answerable']}, 'res': r['res'], 'j': r['j'], 'r': r})
    L = [f"# 절차 안내 질문-정답 평가 — {cfg['label']}", '',
         f"- 실행: {cfg['started']} · 모델 {cfg['model_id']} · 노력 {cfg['effort']} · 검색 k={cfg['k']} · 질문 {cfg['n_questions']}개(용도 {cfg['purpose']}) · 변형 {','.join(cfg['variants'])}",
         f"- 예상 비용 합계 ${sum(r['res'].get('usd', 0) for r in recs):.4f} (실제 차감액은 Console 비용 페이지가 기준)",
         '- **질문이 30개 안팎이라 한 문항이 3~5%p입니다.** 95% 신뢰구간(Wilson)을 함께 적습니다. 변형 사이 차이가 구간 안이면 우열을 말하지 않습니다.',
         '- 정답은 팀원이 법령 원문을 보고 직접 썼습니다(AI가 만들지 않음). 자동 채점은 「핵심 값 포함」과 「근거 조 단위 일치」이며, 애매한 것은 qa_results.xlsx에서 사람이 최종 판정합니다.', '']
    variants = [v for v in ('rag', 'full', 'closed') if v in by_v]
    for scope, pred in (('채점용', lambda r: r['purpose'] == '채점용'), ('개발용', lambda r: r['purpose'] == '개발용'), ('전체', lambda r: True)):
        sub = {v: [x for x in by_v[v] if pred(x['r'])] for v in variants}
        if not any(sub.values()):
            continue
        L += [f'## {scope} 질문', '', '| 변형 | 질문 수 | 종합 정답(답+근거+가짜 인용 없음) | 종합 95% 구간 | 답 정답(핵심 값 포함) | 근거 조 단위 일치 | 근거 정확 일치 | 가짜 인용이 있는 질문 | 「문서에 없음」 처리 |', '|---|---|---|---|---|---|---|---|---|']
        for v in variants:
            s = Q.summarize([{'q': x['q'], 'res': x['res'], 'j': x['j']} for x in sub[v]])
            L.append(f"| {VNAME[v]} | {s['n']} | {_pct(*s['correct'])} | {_ci(*s['correct'])} | {_pct(*s['answer_ok'])} | {_pct(*s['cite_hit'])} | {_pct(*s['cite_exact'])} | {_pct(*s['fake_cite'])} | {_pct(*s['no_answer_ok'])} |")
        L.append('')
    L += ['## 질문 유형별 종합 정답 (채점용)', '', '| 유형 | ' + ' | '.join(VNAME[v] for v in variants) + ' |', '|---|' + '---|' * len(variants)]
    types = sorted({r['type'] for r in recs if r['purpose'] == '채점용'})
    for t in types:
        cells = []
        for v in variants:
            xs = [x for x in by_v[v] if x['r']['purpose'] == '채점용' and x['r']['type'] == t]
            k = sum(1 for x in xs if x['j']['correct'])
            cells.append(_pct(k, len(xs)))
        L.append(f'| {t or "(비어 있음)"} | ' + ' | '.join(cells) + ' |')
    L += ['', '## 비용과 시간', '', '| 변형 | 질문당 평균 비용 | 질문당 평균 시간 | 입력 토큰 평균 | 캐시 읽기 토큰 평균 |', '|---|---|---|---|---|']
    for v in variants:
        xs = by_v[v]
        n = len(xs)
        tok = [x['res'].get('tokens') or {} for x in xs]
        L.append(f"| {VNAME[v]} | ${sum(x['res'].get('usd', 0) for x in xs) / n:.4f} | {sum(x['r'].get('seconds', 0) for x in xs) / n:.1f}초 | "
                 f"{sum(t.get('input', 0) for t in tok) // n:,} | {sum(t.get('cache_read', 0) for t in tok) // n:,} |")
    if 'full' in by_v:
        tok = [x['res'].get('tokens') or {} for x in by_v['full']]
        if sum(t.get('cache_read', 0) for t in tok) == 0:
            L += ['', '[주의] 통째 넣기에서 캐시 읽기 토큰이 0입니다. 캐시가 먹지 않아 비용이 약 10배 높습니다(질문당 약 $0.1).']
    if retrieval:
        hit, n, miss = retrieval
        L += ['', '## 검색만의 정확도 (AI 호출 없음, 모든 질문 중 정답 근거가 있는 것 기준)', '',
              '| 상위 k개 안에 정답 근거(조·별표 단위) 포함 | ' + ' | '.join(f'k={k}' for k in hit) + ' |', '|---|' + '---|' * len(hit),
              f'| 질문 {n}개 | ' + ' | '.join(_pct(hit[k], n) for k in hit) + ' |']
        if miss:
            L += ['', f'상위 5개에 없던 질문 {len(miss)}개:']
            for m in miss[:15]:
                L.append(f"- {m['no']}번 「{m['question'][:50]}」 정답 근거 {', '.join(m['gold'])} / 검색된 것: {', '.join(m['top5'])}")
    (run_dir / 'summary.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return run_dir / 'summary.md'


def write_xlsx(run_dir):
    run_dir = Path(run_dir)
    recs = load_results(run_dir)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '문항별'
    heads = ['번호', '변형', '유형', '용도', '질문', '정답', '핵심 값', '정답 근거', 'AI 답', 'AI 인용', '자료에 없는 인용', '검색된 조각(상위)', '답 맞음(자동)', '근거 맞음(자동)', '종합(자동)', '사람 판정', '메모']
    ws.append(heads)
    for c in range(1, len(heads) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical='top')
    yn = lambda b: '' if b is None else ('○' if b else '×')
    for r in recs:
        res, j = r['res'], r['j']
        ws.append([r['no'], VNAME[r['variant']], r['type'], r['purpose'], r['question'], r['gold_answer'], ' / '.join(r['key_values']), ' / '.join(r['gold_cites']),
                   res.get('answer', ''), ' / '.join(res.get('citations', [])), ' / '.join(res.get('invalid', [])),
                   ', '.join(x['id'] for x in res.get('retrieved', [])[:5]), yn(j['answer_ok']), yn(j['cite_hit']), yn(j['correct']), '', ''])
    for col, w in zip('ABCDEFGHIJKLMNOPQ', (6, 18, 12, 8, 36, 30, 16, 22, 44, 26, 18, 30, 8, 8, 8, 12, 24)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        row[15].fill = YELLOW
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    dv = DataValidation(type='list', formula1='"정답,부분 정답,오답"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'P2:P{max(ws.max_row, 2)}')
    ws.freeze_panes = 'F2'
    wb.save(run_dir / 'qa_results.xlsx')
