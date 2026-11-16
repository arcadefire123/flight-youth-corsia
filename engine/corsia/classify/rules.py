"""코드 규칙으로 항목 속성을 채운다 (8단계 rule 변형과 hybrid의 코드 몫).

규칙의 근거는 판정 기준 v1(2026-10-29)이고, 템플릿 단위 상수는 8_classifier/template_rules_v1.json에 있다.
각 속성은 {'value': 값, 'fixed': 확정 여부, 'why': 이유} 로 돌려준다.
  fixed=True  : 코드가 정할 수 있는 값(hybrid에서 AI가 바꾸지 않음)
  fixed=False : 코드의 잠정값. 글을 읽어야 해서 hybrid에서는 AI가 정함(rule 변형에서는 이 잠정값이 답)
"""
import re

from . import catalog as C

NO_RULE = C.NO_RULE

# 판정 기준 5: 칸 자체나 상위 항목에 「~할 경우, 필요시」 같은 조건이 있으면 조건부
COND = re.compile(r'\bif\b|where applicable|when applicable|in case|only if|only when|unless|경우|필요시|필요한 경우|해당하는|해당 시|있을 때|없을 때', re.I)
# 작성 방식에 붙은 조건(예: 필요시 첨부)은 반영하지 않는다
COND_IGNORE = re.compile(r'필요시\s*첨부|\(필요시 첨부\)')
ATTACH = re.compile(r'\battach|\bupload|\bevidence file|\bsupporting document|첨부', re.I)
# 단위가 분명한 표현은 항목명·세부라벨·원문 설명에서, 모호한 수량 표현은 항목명·세부라벨에서만 찾는다
NUMERIC_STRONG = re.compile(r'\(in tonnes\)|\(in kg\)|\btonnes?\b|\bkg\b|\blitres?\b|\bpercent|per cent|%|\bdensity\b|\bdistance\b|\bmass of\b', re.I)
NUMERIC_WEAK = re.compile(r'\bnumber of (?!the\b)|\bquantity\b|\bamount\b|\bcount\b|\bthreshold\b|\btotal (?:of )?(?:emissions|fuel|mass|number|quantity|offsetting)', re.I)
NARRATIVE = re.compile(r'\bdescribe\b|\bexplain\b|\bexplanation\b|description of|details of|\bprocedures?\b|\bmethodology\b|\bapproach\b', re.I)


def _norm(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()


def _texts(item, ctx):
    """항목 글과 상위 항목 글(의무성 단서 검색용). [(출처, 글)]"""
    K = ctx['K']
    out = [('항목명', _norm(item.get('항목명'))), ('세부라벨', _norm(item.get('세부라벨'))), ('원문 설명', _norm(item.get('원문 설명'))),
           ('국내 항목명', _norm(item.get('국내 항목명')))]
    k = C.domestic_row(item, K)
    if k:
        out.append(('국내 작성방법', _norm(k.get('작성방법(별표) 설명'))))
    p = C.parent_of(item, ctx['by_key'])
    if p:
        out.append(('상위 항목명', _norm(p.get('항목명'))))
        out.append(('상위 국내 항목명', _norm(p.get('국내 항목명'))))
    return out


def condition_cues(item_id):
    """의무성 단서: [(출처, 걸린 표현)]"""
    ctx = C.context()
    item = ctx['by_id'][item_id]
    cues = []
    for src, text in _texts(item, ctx):
        t = COND_IGNORE.sub(' ', text)
        for m in COND.finditer(t):
            cues.append((src, m.group(0)))
            break
    return cues


QUESTION = re.compile(r'^\s*(is|are|does|do|has|have|was|were|can|should)\b', re.I)
DATE_FMT = re.compile(r'yy|mm|dd', re.I)


def _cfg():
    """템플릿 규칙표의 결정 대기 설정(날짜 칸 입력유형, 소제목 줄)"""
    r = C.template_rules()
    return r.get('입력유형_설정') or {}, r.get('소제목_줄') or {}


def _input_type(item):
    """입력유형(판정 기준 3). 칸의 실제 모양을 먼저 보고(코드가 확정), 그다음 크기·글 단서로 잠정값을 낸다.
    v2(2026-11-15): 큰 칸(너비 3 이상·면적 12 이상)이 수치 단서 낱말보다 먼저, 서술 단서는 세부라벨(없으면 항목명)에서만,
    질문 문장과 텍스트 서식(@)은 수치 단서에서 제외, 날짜 서식은 설정값(합의 안건 11)을 따른다."""
    cell = C.template_cells().get(item['ID'], {})
    n = lambda k: _norm(item.get(k))
    short = n('항목명') + ' ' + n('세부라벨')
    label = short + ' ' + n('원문 설명')
    if cell.get('dropdown'):
        return '선택', True, f"원본 칸에 선택 목록({cell.get('options') or '있음'})"
    if cell.get('formula'):
        return '자동계산', True, '원본 칸이 수식(=…)'
    if ATTACH.search(label):
        return '첨부', False, '글에 attach/첨부 표현'
    nf = str(cell.get('numfmt') or 'General')
    if DATE_FMT.search(nf):
        v = (_cfg()[0].get('날짜 서식 칸') or '텍스트')
        return v, True, f'날짜 서식 칸 → {v} (합의 안건 11 결정 대기, 규칙표 「입력유형_설정」으로 바꿈)'
    numfmt = nf not in ('General', '@', '') and bool(re.search(r'[0#]', nf))
    if cell.get('dv_numeric') or numfmt:
        return '수치', False, '숫자 데이터 검증' if cell.get('dv_numeric') else '숫자 표시 형식'
    w, h = cell.get('merged_w', 1), cell.get('merged_h', 1)
    if w >= 3 and w * h >= 12:
        return '서술', False, f'큰 칸({w}×{h})'
    nar = n('세부라벨') or n('항목명')
    if NARRATIVE.search(nar) and w * h >= 3:
        return '서술', False, '설명·절차를 쓰라는 라벨'
    # 입력칸 첫 줄의 행 머리글(같은 줄 왼쪽 칸 글). 입력칸에는 단위 표시가 없어도 행 머리글(예: 「… (in tonnes):」)이 값의 성격을 말해 준다.
    # 첫 줄만 쓴다(칸 범위가 서로 다른 항목의 줄들을 함께 담는 경우가 있어 전체 줄을 쓰면 다른 항목의 글이 섞임). 질문 문장은 제외.
    rl0 = str(cell.get('first_row_label') or '')
    rl = '' if QUESTION.match(rl0) else rl0
    if nf != '@' and not QUESTION.match(n('항목명')) and (NUMERIC_STRONG.search(label) or NUMERIC_WEAK.search(short)):
        return '수치', False, '단위·수량 표현'
    if nf != '@' and not QUESTION.match(n('항목명')) and rl and (NUMERIC_STRONG.search(rl) or NUMERIC_WEAK.search(rl)):
        return '수치', False, '같은 줄 행 머리글의 단위·수량 표현'
    return '텍스트', False, '그 밖에는 짧은 글(텍스트)'


def _basis(item, ctx, rules):
    t = item['템플릿']
    tr = rules.get(t, {})
    if t == '1.4 OoM':
        base = tr.get('근거_기본')
        if not base:
            return NO_RULE, True, 'OoM 근거 기준 없음'
        no = _norm(item.get('항목번호'))
        num = no if re.fullmatch(r'\d+', no) else ''
        parts = [b.replace('{문항번호}', num) for b in base if num or '{문항번호}' not in b]
        return ' / '.join(parts), bool(num), ('OoM 기본 인용(합의 안건 3, 문항 번호만)' if num else '문항 번호가 없는 칸(ETM 부록1 번호 생략)')
    dom = tr.get('국내')
    form = item.get('국내 서식')
    if dom and form:
        k = C.domestic_row(item, ctx['K'])
        sec = _norm(k['섹션']) if k else ''
        no = C.split_domestic_no(item.get('국내 항목번호'))[1] or (_norm(k['항목번호']) if k else '')
        parts = []
        sec_only = (not k) and re.fullmatch(r'\d+(?:\.\d+)?', no or '') and item.get('시트') != 'Template Information' \
            and not str(item.get('매핑') or '').startswith('회의 결정 9')
        if sec_only:                                           # 국내 항목번호가 섹션 번호(표 열 등)인 칸
            parts += [f"지침 {dom['서식']} {no}", f"지침 {dom['별표']} {no}"]
            fixed, why = True, '섹션 단위 매핑에서 조립(국내 항목번호가 섹션 번호, 형식은 팀 확인)'
        elif sec and no:
            parts += [f"지침 {dom['서식']} {sec}.{no}", f"지침 {dom['별표']} {sec}.{no}"]
            fixed, why = True, '국내 서식 매핑에서 조립(서식·별표 번호는 서식별 상수)'
        else:
            parts += [f"지침 {dom['서식']} {sec}".strip()]
            fixed, why = False, '국내 항목번호가 없는 줄(섹션 줄 등)'
        parts += [dom['지침조문']] + list(dom['법령'])
        return ' / '.join(parts), fixed, why
    if t == '1.6 VR':
        no = _norm(item.get('항목번호'))
        base = tr['ICAO'] + (f' / ETM 부록1 1.6 VR {no}' if no else '')
        return base, False, 'ICAO 근거(기본값). 해당 소절은 글을 읽어야 정해짐'
    return tr.get('ICAO') or NO_RULE, False, '국내 서식 대응이 없는 칸이라 ICAO 문서(기본값)'


def rule_predict(item_id):
    ctx = C.context()
    item = ctx['by_id'][item_id]
    rules = C.template_rules()
    t = item['템플릿']
    tr = rules.get(t, {})
    head = rules.get('소제목_줄') or {}
    # 팀이 정답을 확정한 견본 5건은 제목 줄 목록에 있어도 제외하지 않는다(F0571은 A형 기준에 해당하지만 확정 정답이 있어 팀 확인 필요)
    if item_id in (head.get('ids') or []) and item_id not in C.EXAMPLE_IDS and head.get('정책') in ('exclude', 'blank_input'):   # 합의 안건 12 결정 반영
        pol = head['정책']
        base = _predict(item_id, item, rules, tr, ctx)
        if pol == 'exclude':
            return {a: {'value': '', 'fixed': True, 'why': '소제목 줄(제외 권장, 합의 안건 12)'} for a in C.ATTRS}
        base['입력유형'] = {'value': '', 'fixed': True, 'why': '소제목 줄: 입력유형 비움(합의 안건 12)'}
        return base
    return _predict(item_id, item, rules, tr, ctx)


def _predict(item_id, item, rules, tr, ctx):
    t = item['템플릿']
    out = {}
    for a in ('주체', '단계'):
        v = tr.get(a)
        out[a] = {'value': v if v else NO_RULE, 'fixed': True, 'why': '템플릿 규칙' if v else '기준 없음'}
    # 시점·기한
    if t == '1.6 VR':
        text = ' '.join(_norm(item.get(k)) for k in ('항목명', '세부라벨', '시트', '섹션', '원문 설명')).lower()
        if any(w in text for w in tr.get('이행_단서', [])):
            out['시점·기한'] = {'value': tr['시점·기한_이행'], 'fixed': False, 'why': '배출권 취소 보고서 검증 칸으로 보임'}
        else:
            out['시점·기한'] = {'value': tr['시점·기한'], 'fixed': False, 'why': '배출량 검증보고서 기한(기본값)'}
    else:
        v = tr.get('시점·기한')
        out['시점·기한'] = {'value': v if v else NO_RULE, 'fixed': True, 'why': '템플릿별 제출 기한' if v else '기준 없음'}
    v, fixed, why = _input_type(item)
    out['입력유형'] = {'value': v, 'fixed': fixed, 'why': why}
    cues = condition_cues(item_id)
    out['의무성'] = {'value': '조건부' if cues else '강행', 'fixed': False,
                   'why': ('조건 표현: ' + '; '.join(f'{s}「{w}」' for s, w in cues)) if cues else '조건 표현 없음 → 강행'}
    v, fixed, why = _basis(item, ctx, rules)
    out['근거'] = {'value': v, 'fixed': fixed, 'why': why}
    out['연결필드'] = {'value': '-', 'fixed': False, 'why': '코드는 연결을 찾지 않음(AI가 후보에서 고름)'}
    return out


def is_excluded(item_id):
    """소제목 줄로 확정되어 속성을 채우지 않기로 한 항목인가(합의 안건 12, 정책 exclude)"""
    head = C.template_rules().get('소제목_줄') or {}
    return head.get('정책') == 'exclude' and item_id in (head.get('ids') or []) and item_id not in C.EXAMPLE_IDS


def rule_values(item_id):
    return {a: d['value'] for a, d in rule_predict(item_id).items()}
