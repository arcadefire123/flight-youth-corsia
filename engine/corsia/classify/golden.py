"""골든셋(팀이 쓴 정답) 읽기와 속성별 채점 (8단계).

골든셋 파일은 6_goldenset/expert_request/골든셋_작성양식_v1.xlsx 와 같은 모양이다.
「1_작성할항목」 시트 3행이 머리글, 4행부터 항목. 팀원이 L~R열(주체·단계·의무성·시점·기한·입력유형·근거·연결필드)을 채운다.
파일이 여러 개(팀원 4명이 나눠 씀)여도 ID로 합친다.
"""
import glob
import re
import warnings
from collections import defaultdict
from pathlib import Path

import openpyxl

from . import catalog as C
from . import validate as V

NO_RULE = C.NO_RULE
ATTRS = C.ATTRS


def _find_sheet(wb):
    for name in wb.sheetnames:
        if '작성할항목' in name:
            return wb[name]
    raise ValueError('「1_작성할항목」 시트를 찾지 못했습니다.')


def read_form(path):
    """한 양식 파일을 읽어 [{id, order, template, gold: {속성: 값}, memo}] 로 돌려준다."""
    warnings.filterwarnings('ignore')
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = _find_sheet(wb)
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and 'ID' in r and '주체' in r)
    H = list(rows[hi])
    out = []
    for r in rows[hi + 1:]:
        d = dict(zip(H, r))
        if not d.get('ID'):
            continue
        gold = {a: V.norm_space(d.get(a)) for a in ATTRS}
        out.append({'id': d['ID'], 'order': d.get('작성 순서') or '', 'template': d.get('템플릿'), 'gold': gold,
                    'memo': V.norm_space(d.get('메모')), 'filled': any(gold[a] for a in ATTRS)})
    return out


def form_ids(path, order=None):
    """양식에 적힌 항목 ID(작성 순서 '1차'/'2차' 필터)"""
    return [x['id'] for x in read_form(path) if order in (None, x['order'])]


def read_golden(paths):
    """여러 파일을 합친다. {ID: {'gold': {...}, 'memo': ..., 'file': ...}}; 같은 ID를 서로 다르게 쓴 것은 conflicts에"""
    files = []
    for p in paths:
        p = str(p)
        files += [f for f in sorted(glob.glob(str(Path(p) / '*.xlsx'))) if not Path(f).name.startswith('~$')] if Path(p).is_dir() else [p]   # 엑셀 임시 파일(~$) 건너뜀
    merged, conflicts = {}, []
    for f in files:
        for x in read_form(f):
            if not x['filled']:
                continue
            if x['id'] in merged and merged[x['id']]['gold'] != x['gold']:
                conflicts.append((x['id'], merged[x['id']]['file'], f))
            merged[x['id']] = {'gold': x['gold'], 'memo': x['memo'], 'file': Path(f).name}
    return merged, conflicts


def is_no_rule(v):
    return str(v or '').strip().startswith(NO_RULE)


def same(attr, gold, pred):
    """정답과 예측이 같은가(정규화 후). 근거·연결필드는 순서 무관 집합 비교."""
    if attr in ('근거', '연결필드'):
        return set(V.split_parts(gold)) == set(V.split_parts(pred))
    return V.norm_value(attr, gold) == V.norm_value(attr, pred)


def jaccard(gold, pred):
    a, b = set(V.split_parts(gold)), set(V.split_parts(pred))
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _basis_base(iid):
    """규칙이 조립하는 기본 인용(근거를 기본/추가로 나눠 볼 때의 기준)"""
    from . import rules as R
    try:
        return set(V.split_parts(R.rule_predict(iid)['근거']['value']))
    except Exception:
        return set()


def score(preds, gold, exclude=(), exclude_cells=()):
    """preds: {ID: {속성: 값}} (한 변형), gold: read_golden()의 결과.
    정답이 「기준 없음」인 칸은 따로 센다(headline에 섞지 않음).
    반환: {속성: {'n','correct','accuracy','norule_n','norule_agree','jaccard'(근거만)}}, 틀린 목록"""
    res = {a: {'n': 0, 'correct': 0, 'norule_n': 0, 'norule_agree': 0, 'jac': 0.0, 'base_ok': 0, 'extra_items': 0, 'extra_hit_items': 0,
               'extra_gold': 0, 'extra_found': 0} for a in ATTRS}
    exclude_cells = set(exclude_cells)
    wrong = []
    for iid, g in gold.items():
        if iid in exclude or iid not in preds:
            continue
        for a in ATTRS:
            gv = g['gold'][a]
            if not gv or (iid, a) in exclude_cells:                 # 카드 예시로 쓴 칸은 채점에서 뺌
                continue
            pv = preds[iid].get(a, '')
            r = res[a]
            if is_no_rule(gv):
                r['norule_n'] += 1
                r['norule_agree'] += 1 if is_no_rule(pv) else 0
                continue
            r['n'] += 1
            ok = same(a, gv, pv)
            r['correct'] += 1 if ok else 0
            if a == '근거':
                r['jac'] += jaccard(gv, pv)
                gs, ps, base = set(V.split_parts(gv)), set(V.split_parts(pv)), _basis_base(iid)
                gb, ge = gs & base, gs - base                       # 정답 중 기본 인용 / 문항별 추가 인용
                r['base_ok'] += 1 if (gb <= ps and ps <= gs) else 0   # 기본 인용을 모두 포함하고 정답 밖의 인용은 없음
                if ge:
                    r['extra_items'] += 1
                    r['extra_hit_items'] += 1 if ge <= ps else 0
                    r['extra_gold'] += len(ge)
                    r['extra_found'] += len(ge & ps)
            if not ok:
                wrong.append({'id': iid, 'attr': a, 'gold': gv, 'pred': pv})
    for a, r in res.items():
        r['accuracy'] = (r['correct'] / r['n']) if r['n'] else None
        r['jaccard'] = (r['jac'] / r['n']) if (a == '근거' and r['n']) else None
    return res, wrong
