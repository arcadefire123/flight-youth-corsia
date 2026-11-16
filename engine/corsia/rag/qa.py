"""질문-정답 세트 읽기와 채점 (7단계 절차 안내).

질문-정답 양식(7_rag/qa_testset_template_v1.xlsx)의 「1_질문정답」 시트를 읽는다. 팀원이 법령 원문을 보고 직접 쓴 것(AI에게 묻지 않음).
채점은 두 가지를 따로 본다.
  답  : 정답의 「핵심 값」이 AI 답에 모두 들어 있나(문장 비교가 아니라 값 포함). 「문서에 없음」 문항은 AI가 「근거 없음」이라고 했나
  근거: 정답의 근거 조항(조·별표 단위)을 AI가 인용했나, AI가 인용한 조항이 자료에 실제로 있나(가짜 인용)
검색(rag만)은 AI 호출 없이 따로 잰다: 정답 근거가 검색 결과 상위 k개 안에 있었나.
"""
import re
import warnings
from pathlib import Path

import openpyxl

from . import corpus as K

TYPES = ['기한', '제출 방법·서식', '대상·자격', '절차·순서', '정의', '의무·벌칙', '별표 작성방법', '문서에 없음']
PURPOSES = ['개발용', '채점용']


def _norm(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()


def read_qa(paths):
    """양식 파일·폴더에서 질문-정답을 읽는다. 질문이 비어 있는 줄은 건너뛴다. [{...}]"""
    warnings.filterwarnings('ignore')
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(p.glob('*.xlsx')) if p.is_dir() else [p]
    out = []
    for f in files:
        if f.name.startswith('~$'):
            continue
        wb = openpyxl.load_workbook(f, data_only=True)
        name = next((n for n in wb.sheetnames if '질문정답' in n), None)
        if not name:
            continue
        rows = list(wb[name].iter_rows(values_only=True))
        hi = next((i for i, r in enumerate(rows) if r and '질문' in r and '정답' in r), None)
        if hi is None:
            continue
        H = list(rows[hi])
        for r in rows[hi + 1:]:
            d = dict(zip(H, r))
            q = _norm(d.get('질문'))
            if not q:
                continue
            ans = _norm(d.get('문서에 답이 있나')) or '있음'
            out.append({'no': d.get('번호'), 'question': q, 'type': _norm(d.get('질문 유형')), 'gold_answer': _norm(d.get('정답')),
                        'key_values': [x for x in re.split(r'\s*/\s*', _norm(d.get('핵심 값'))) if x],
                        'gold_cites': K.split_cites(d.get('근거 조항')), 'answerable': ans != '없음',
                        'purpose': _norm(d.get('용도')) or '채점용', 'author': _norm(d.get('작성자')),
                        'cross_check': _norm(d.get('교차 확인')), 'file': f.name})
    return out


DATE = re.compile(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일')


def norm_key(s):
    t = DATE.sub(r'\1/\2', str(s or ''))
    t = re.sub(r'[\s「」『』“”"\'‘’,.()·ㆍ-]', '', t).lower()
    return t


def key_ok(key_values, answer):
    a = norm_key(answer)
    return bool(key_values) and all(norm_key(k) in a for k in key_values)


def units(cites):
    return {u for u in (K.unit_of(c) for c in cites) if u}


def cite_hit(gold, pred):
    """정답 근거의 조(별표) 단위가 모두 AI 인용 안에 있나"""
    g = units(gold)
    return bool(g) and g <= units(pred)


def cite_exact(gold, pred):
    return {K.flat(x) for x in gold} == {K.flat(x) for x in pred}


def judge(q, res):
    """질문 하나의 자동 채점. q: read_qa 항목, res: answer.ask 결과"""
    r = {'answer_ok': None, 'cite_hit': None, 'cite_exact': None, 'fake_cite': bool(res.get('invalid')), 'correct': None}
    if res.get('answerable') is None:                      # JSON을 못 읽음
        r.update(answer_ok=False, cite_hit=False, cite_exact=False, correct=False)
        return r
    if not q['answerable']:                                # 문서에 없는 질문: 「근거 없음」이라고 해야 정답
        ok = (res['answerable'] is False)
        r.update(answer_ok=ok, correct=ok)
        return r
    r['answer_ok'] = bool(res['answerable']) and key_ok(q['key_values'], res['answer'])
    r['cite_hit'] = cite_hit(q['gold_cites'], res['citations'])
    r['cite_exact'] = cite_exact(q['gold_cites'], res['citations'])
    r['correct'] = bool(r['answer_ok'] and r['cite_hit'] and not r['fake_cite'])
    return r


def retrieval_recall(qa_items, index, ks=(1, 3, 5, 8)):
    """AI 호출 없이: 정답 근거(조·별표 단위)가 검색 상위 k개 안에 있었나. 반환: ({k: 맞춘 수}, 답이 있는 질문 수, 놓친 목록)"""
    n, hit, miss = 0, {k: 0 for k in ks}, []
    kmax = max(ks)
    for q in qa_items:
        if not q['answerable'] or not units(q['gold_cites']):
            continue
        n += 1
        top = [c['id'] for c, _s, _w in index.search(q['question'], k=kmax)]
        g = units(q['gold_cites'])
        for k in ks:
            if g <= units(top[:k]):
                hit[k] += 1
        if not g <= units(top[:5]):
            miss.append({'no': q['no'], 'question': q['question'], 'gold': q['gold_cites'], 'top5': top[:5]})
    return hit, n, miss


def summarize(items):
    """items: [{'q':…, 'res':…, 'j':…}] → 집계 dict"""
    def rate(sel, f):
        sel = list(sel)
        return (sum(1 for x in sel if f(x)), len(sel)) if sel else (0, 0)
    ans = [x for x in items if x['q']['answerable']]
    no = [x for x in items if not x['q']['answerable']]
    return {
        'n': len(items),
        'correct': rate(items, lambda x: x['j']['correct']),
        'answer_ok': rate(ans, lambda x: x['j']['answer_ok']),
        'cite_hit': rate(ans, lambda x: x['j']['cite_hit']),
        'cite_exact': rate(ans, lambda x: x['j']['cite_exact']),
        'fake_cite': rate(items, lambda x: x['j']['fake_cite']),
        'no_answer_ok': rate(no, lambda x: x['j']['answer_ok']),
        'usd': round(sum(x['res'].get('usd', 0.0) for x in items), 4),
    }
