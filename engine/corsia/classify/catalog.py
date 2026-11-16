"""카탈로그(field_master.xlsx) 읽기, 상위 항목·국내 서식 매핑, 연결필드 후보 검색 (8단계)."""
import json
import math
import re
import warnings
from collections import Counter
from functools import lru_cache
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[3]
FIELD_MASTER = ROOT / '1_document_map' / 'field_master.xlsx'
CLASSIFIER_DIR = ROOT / '8_classifier'
TEMPLATE_RULES = CLASSIFIER_DIR / 'template_rules_v1.json'
TEMPLATE_CELLS = CLASSIFIER_DIR / 'template_cells_v1.json'

ATTRS = ['주체', '단계', '의무성', '시점·기한', '입력유형', '근거', '연결필드']
NO_RULE = '기준 없음'
# 정답이 확정된 견본 5건: AI에게 예시로 보여 주고 채점에는 쓰지 않는다
EXAMPLE_IDS = ['F0161', 'F0206', 'F0469', 'F0571', 'F0664']


def _norm(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()


@lru_cache(maxsize=1)
def load_codes():
    """속성 코드값(2_속성_코드값 시트)"""
    warnings.filterwarnings('ignore')
    wb = openpyxl.load_workbook(FIELD_MASTER, data_only=True)
    rows = list(wb['2_속성_코드값'].iter_rows(values_only=True))
    heads = rows[0]
    return {h: [r[i] for r in rows[1:] if r[i]] for i, h in enumerate(heads)}


def _first_row(coord):
    m = re.search(r'(\d+)', str(coord or ''))
    return int(m.group(1)) if m else None


@lru_cache(maxsize=1)
def raw_text_by_item():
    """ICAO 원본 칸의 글(4_raw_cells)을 그 칸이 속한 항목에 붙인다(연결필드 후보 검색 전용).
    같은 시트에서 칸의 줄 번호보다 작거나 같은 줄에서 시작하는 가장 가까운 항목에 붙인다."""
    warnings.filterwarnings('ignore')
    wb = openpyxl.load_workbook(FIELD_MASTER, data_only=True)
    items, _ = load_catalog()
    by_sheet = {}
    for it in items:
        r = _first_row(it.get('셀좌표'))
        if r:
            by_sheet.setdefault((it['템플릿'], it['시트']), []).append((r, it['ID']))
    for v in by_sheet.values():
        v.sort()
    out = {}
    for t, sh, cell, text in list(wb['4_raw_cells'].iter_rows(values_only=True))[1:]:
        text = _norm(text)
        r = _first_row(cell)
        if not r or len(text) < 12 or (t, sh) not in by_sheet:
            continue
        owner = None
        for row, iid in by_sheet[(t, sh)]:
            if row <= r:
                owner = iid
            else:
                break
        if owner:
            out.setdefault(owner, []).append(text)
    return {k: ' '.join(v)[:500] for k, v in out.items()}


@lru_cache(maxsize=1)
def load_catalog():
    """살아 있는 항목 667개(삭제 확정·예시 줄 제외)를 ID 순서대로 돌려준다."""
    warnings.filterwarnings('ignore')
    wb = openpyxl.load_workbook(FIELD_MASTER, data_only=True)
    rows = list(wb['1_field_master'].iter_rows(values_only=True))
    H = list(rows[0])
    items = []
    for r in rows[1:]:
        if not r[0]:
            continue
        d = dict(zip(H, r))
        if d['ID'] == '예시-삭제가능' or '삭제' in str(d.get('매핑') or ''):
            continue
        items.append(d)
    # 국내 별지 항목(작성방법·섹션 번호)
    K = []
    ws = wb['6_국내별지_항목']
    kh = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            K.append(dict(zip(kh, row)))
    return items, K


def template_rules():
    return json.loads(TEMPLATE_RULES.read_text(encoding='utf-8'))


CARD_EXAMPLES = CLASSIFIER_DIR / 'card_examples_v1.json'


_CARD_EXAMPLES_ON = False     # 2026-11-16 팀 결정: 골든셋 항목을 카드 예시로 쓰지 않는다(기본 꺼짐). 켜려면 실행 옵션 --card-examples


def set_card_examples(on):
    global _CARD_EXAMPLES_ON
    _CARD_EXAMPLES_ON = bool(on)


def _card_examples_raw():
    if not CARD_EXAMPLES.exists():
        return []
    return json.loads(CARD_EXAMPLES.read_text(encoding='utf-8')).get('examples', [])


def card_examples():
    """참고 카드에 넣는 OoM 작성 예(골든셋 항목). 꺼져 있으면(기본) 빈 목록 — 카드에는 문구만 들어간다."""
    return _card_examples_raw() if _CARD_EXAMPLES_ON else []


def card_example_cells(ids=None):
    """카드 예시로 쓴 (항목 ID, 속성) 칸. 모든 변형의 채점에서 제외한다.
    ids가 주어지면(실행 설정 config.json의 card_example_ids) 그 예시만, 없으면 지금 켜져 있는 예시만 센다."""
    src = [e for e in _card_examples_raw() if e['id'] in set(ids)] if ids is not None else card_examples()
    return {(e['id'], a) for e in src for a in ATTRS if a in e}


def template_cells():
    if not TEMPLATE_CELLS.exists():
        return {}
    return json.loads(TEMPLATE_CELLS.read_text(encoding='utf-8')).get('items', {})


def gold_of_examples():
    """견본 5건의 확정 정답(카탈로그에 이미 적혀 있음)"""
    items, _ = load_catalog()
    out = {}
    for it in items:
        if it['ID'] in EXAMPLE_IDS:
            out[it['ID']] = {a: _norm(it.get(a)) for a in ATTRS}
    return out


def sheet_prefix(sheet):
    m = re.match(r'^(\d+(?:\.\d+)?)\b', str(sheet or '').strip())
    return m.group(1) if m else ''


def link_label(item):
    """다른 템플릿에서 이 항목을 가리킬 때 쓰는 이름: '1.2 ER!5.1a' 형식(판정 기준 7).
    항목번호가 없으면 None."""
    no = _norm(item.get('항목번호')).rstrip(')')
    if not no:
        return None
    no = re.sub(r'\.0$', '', no)
    return f"{item['템플릿']}!{sheet_prefix(item.get('시트'))}{no}"


def split_domestic_no(raw):
    """국내 항목번호 문자열을 (섹션 번호 또는 None, 항목 번호)로 나눈다. 대부분 「a)」이지만 CEF 청구서 일부 칸은
    「3 a)」처럼 섹션 번호와 항목 번호가 한 칸에 붙어 있다(F0402 근거 누락 버그의 원인, 14개)."""
    t = _norm(raw)
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s+([a-z]\d*\))', t)
    return (m.group(1), m.group(2)) if m else (None, t)


def domestic_row(item, K):
    """이 항목에 짝지어진 국내 별지 항목(섹션 번호를 얻기 위함)"""
    form = item.get('국내 서식')
    sec_hint, no = split_domestic_no(item.get('국내 항목번호'))
    if not form:
        return None
    name = re.sub(r'^[a-z]\d*\)\s*', '', _norm(item.get('국내 항목명')))
    cand = [k for k in K if k['별지 서식'] == form and _norm(k['항목번호']) == no]
    if sec_hint:
        cand = [k for k in cand if _norm(k['섹션']) == sec_hint]
    if len(cand) > 1:
        exact = [k for k in cand if _norm(k['항목명']) == name]
        loose = [k for k in cand if len(_norm(k['항목명'])) >= 8 and name.startswith(_norm(k['항목명'])[:12])]
        cand = (exact or loose or [k for k in cand if _norm(k['섹션']) == sheet_prefix(item.get('시트'))] or cand)
    return cand[0] if cand else None


def compute_heading_ids():
    """제목 줄(소제목) 항목 ID를 카탈로그에서 계산한다(팀 기준, 2026-11-16 정정 전달문).
    대상: 셀좌표가 「row N」인 항목. (A) 같은 템플릿·시트·항목번호에 row가 아닌(입력 칸) 항목이 따로 있음, 또는
    (B) 같은 시트에 하위 번호 항목(b) → b1), b2))이 따로 있음, 또는 (C) OoM a1)~a8). 반환: (전체 집합, {'A': 수, 'B': 수, 'C': 수})"""
    items, _ = load_catalog()

    def isrow(it):
        return str(it.get('셀좌표') or '').startswith('row')

    def no(it):
        return _norm(it.get('항목번호'))
    rows = [it for it in items if isrow(it)]
    nonrow = set()
    allno = {}
    for it in items:
        if not isrow(it) and no(it):
            nonrow.add((it['템플릿'], it['시트'], no(it)))
        allno.setdefault((it['템플릿'], it['시트']), set()).add(no(it))

    def has_sub(it):
        m = re.fullmatch(r'([a-z])\)', no(it))
        return bool(m) and any(re.fullmatch(m.group(1) + r'\d+\)', n) for n in allno[(it['템플릿'], it['시트'])])
    A = {it['ID'] for it in rows if no(it) and (it['템플릿'], it['시트'], no(it)) in nonrow}
    B = {it['ID'] for it in rows if has_sub(it)} - A
    Cc = {it['ID'] for it in rows if it['템플릿'] == '1.4 OoM' and re.fullmatch(r'a\d\)', no(it))} - A - B
    return A | B | Cc, {'A': len(A), 'B': len(B), 'C': len(Cc)}


def parent_of(item, items_by_key):
    """같은 템플릿·시트에서 한 단계 위 항목('b4)' → 'b)'). 없으면 None"""
    no = _norm(item.get('항목번호'))
    m = re.fullmatch(r'([a-z])\d+\)', no)
    if not m:
        return None
    return items_by_key.get((item['템플릿'], item['시트'], m.group(1) + ')'))


def index_by_key(items):
    out = {}
    for it in items:
        key = (it['템플릿'], it['시트'], _norm(it.get('항목번호')))
        out.setdefault(key, it)
    return out


# ---- 연결필드 후보: 글자 조각(3글자) TF-IDF 코사인. 외부 패키지 없이 순수 파이썬 ----
def _grams(text, n=3):
    t = re.sub(r'[^0-9a-z가-힣]+', ' ', str(text or '').lower())
    t = ' '.join(t.split())
    return [t[i:i + n] for i in range(max(len(t) - n + 1, 0))]


class CandidateIndex:
    def __init__(self, items):
        self.items = items
        raw = raw_text_by_item()
        docs = [Counter(_grams(' '.join([_norm(it.get(k)) for k in ('항목명', '세부라벨', '원문 설명')] + [raw.get(it['ID'], '')])))
                for it in items]
        df = Counter()
        for d in docs:
            df.update(d.keys())
        n = len(docs)
        self.idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self.vecs, self.norms = [], []
        for d in docs:
            v = {g: c * self.idf[g] for g, c in d.items()}
            self.vecs.append(v)
            self.norms.append(math.sqrt(sum(x * x for x in v.values())) or 1.0)

    def top(self, i, k=8, min_score=0.30):
        """i번째 항목과 비슷한 다른 템플릿 항목(점수 높은 순). (항목, 점수) 목록"""
        a, na, ti = self.vecs[i], self.norms[i], self.items[i]['템플릿']
        out = []
        for j, it in enumerate(self.items):
            if it['템플릿'] == ti or not link_label(it) or it.get('시트') == 'Template Information':
                continue
            b = self.vecs[j]
            small, big = (a, b) if len(a) < len(b) else (b, a)
            dot = sum(x * big.get(g, 0.0) for g, x in small.items())
            s = dot / (na * self.norms[j])
            if s >= min_score:
                out.append((it, round(s, 3)))
        out.sort(key=lambda x: -x[1])
        seen, uniq = set(), []
        for it, s in out:                       # 같은 이름(라벨)은 점수가 가장 높은 하나만
            lab = link_label(it)
            if lab not in seen:
                seen.add(lab)
                uniq.append((it, s))
        return uniq[:k]


@lru_cache(maxsize=1)
def context():
    """분류에 필요한 조회표를 한 번만 만든다."""
    items, K = load_catalog()
    return {'items': items, 'K': K, 'by_key': index_by_key(items), 'by_id': {it['ID']: it for it in items},
            'pos': {it['ID']: i for i, it in enumerate(items)}, 'cand': None}


def candidates(item_id, k=8):
    ctx = context()
    if ctx['cand'] is None:
        ctx['cand'] = CandidateIndex(ctx['items'])
    return ctx['cand'].top(ctx['pos'][item_id], k=k)
