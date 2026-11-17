"""
화면·보고서 표시용 대응표

엔진 내부에서는 코드 이름(documents, reported_t …)을 쓰고, 사람에게 보여줄 때만
이 대응표로 한글 이름을 붙인다. 계산·판정 로직은 이 파일을 쓰지 않는다.

1. INPUT_LABELS  — 판정 보류(SKIPPED) 사유에 나오는 「필요한 자료」 이름
   대응표에 없으면 코드 이름이 그대로 나온다. 누락은 tests/test_verify.py가 잡는다.
2. SAMPLE_LABELS — 위반 상세(펼친 줄)에 나오는 수치·항목 이름, 단위, 소수 자릿수
   대응표에 없으면 영어 이름을 그대로 두되, 소수는 둘째 자리까지만 보인다.

근거: web_design_v1.md §3.3
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ── 1. 판정에 필요한 자료 ───────────────────────────────────────────
# (한글 이름, 서류 JSON에 들어 있는 자료인가)
INPUT_LABELS = {
    'documents':          ('서류 JSON', True),
    'documents.EMP':      ('서류 JSON의 모니터링 계획(EMP)', True),
    'documents.ER':       ('서류 JSON의 배출량보고서(ER)', True),
    'documents.EUCR':     ('서류 JSON의 상쇄의무이행보고서(EUCR)', True),
    'documents.CEF_DECL': ('서류 JSON의 적격연료 사용신고서(CEF)', True),
    'documents.VR':       ('서류 JSON의 배출량검증보고서(VR)', True),
    'documents.VR_EUCR':  ('서류 JSON의 상쇄의무이행검증보고서', True),
    'emission_units':     ('서류 JSON의 말소 배출권 목록', True),
    'external_fuel_data': ('서류 JSON의 외부 연료 대조 자료', True),
    'afbr_values':        ('기종별 평균 연료소모율(AFBR) 값', False),
    'corsia_states':      ('CORSIA 참여국 목록(corsia_states_연도.csv)', False),
}


_LATIN_FINAL = set('LMNRlmnr')   # 엘·엠·엔·알 — 받침으로 끝나는 영문자


def josa_i_ga(word):
    """「이/가」 고르기. 끝의 괄호 설명은 떼고 본다 (예: 계획(EMP) → 계획)."""
    w = word.rstrip()
    if w.endswith(')') and '(' in w:
        w = w[:w.rfind('(')].rstrip() or w
    ch = w[-1:] or ' '
    if '가' <= ch <= '힣':
        return '이' if (ord(ch) - 0xAC00) % 28 else '가'
    return '이' if ch in _LATIN_FINAL else '가'


def input_label(key):
    return INPUT_LABELS.get(key, (key, False))[0]


def from_documents_json(key):
    return INPUT_LABELS.get(key, (key, False))[1]


def skip_summary(skipped):
    """판정 보류 규칙이 있으면 요약 상자·엑셀에 붙일 안내 한 문장. 없으면 ''.
    판정(ICAO 3종)은 바꾸지 않고 안내만 한다.  skipped : status가 SKIPPED인 규칙 결과 목록"""
    if not skipped:
        return ''
    missing = []
    for f in skipped:
        for m in f.get('missing_inputs') or []:
            if m not in missing:
                missing.append(m)
    hints = []
    if any(from_documents_json(m) for m in missing):
        hints.append('서류 JSON을 함께 올리면 검증됩니다')
    others = [input_label(m) for m in missing if not from_documents_json(m)]
    if others:
        hints.append('필요한 자료: ' + ', '.join(others))
    return (f'일부 규칙 {len(skipped)}개는 자료가 없어 검증하지 못했습니다'
            + (f' ({" · ".join(hints)})' if hints else '') + '.')


# ── 2. 위반 상세의 수치·항목 ────────────────────────────────────────
# (한글 이름, 단위, 소수 자릿수)
#   소수 자릿수 0/1 → 그 자리로 반올림하고 천 단위 쉼표
#   'auto'         → 정수면 정수, 아니면 소수 첫째 자리
#   None           → 숫자로 보지 않고 글자 그대로 (ID·연도·버전 등)
SAMPLE_LABELS = {
    # 배출량·연료 (톤)
    'reported_t':          ('보고값', 't', 0),
    'computed_t':          ('재계산', 't', 0),
    'diff_t':              ('차이', 't', 0),
    'materiality_limit_t': ('중요성 임계', 't', 0),
    'reported_total_t':    ('보고 총 배출량', 't', 0),
    'sum_of_routes_t':     ('노선별 합계', 't', 0),
    'purchased_fuel_t':    ('구매 연료량', 't', 0),
    'reported_fuel_t':     ('보고 연료량', 't', 0),
    'total_fuel_t':        ('총 연료', 't', 1),
    'avg_fuel_per_flight': ('편당 평균 연료', 't', 1),
    'fuel_t':              ('편당 연료소비', 't', 1),
    'route_median_t':      ('노선 중앙값', 't', 1),
    'block_off_t':         ('블록오프 연료량', 't', 1),
    'block_on_t':          ('블록온 연료량', 't', 1),
    'tank_capacity_t':     ('탱크 용량', 't', 1),
    'uplift_t':            ('급유량', 't', 1),
    'OE_y_t':              ('상쇄 대상 배출량', 't', 0),
    'offsetting_co2_t':    ('상쇄 대상 배출량', 't', 0),
    'FOR_t':               ('최종 상쇄의무량', 't', 0),
    'reported':            ('보고값', 't', 0),
    'computed':            ('계산값', 't', 0),
    'required':            ('필요 수량', 't', 0),
    'cancelled':           ('말소 수량', 't', 0),
    '별지3':               ('적격연료 사용신고서 합계', 't', 1),
    # 편수·비율·기타 수치
    'reported_total':      ('보고 편수', '편', 0),
    'sum_of_routes':       ('노선별 편수 합계', '편', 0),
    'flights':             ('편수', '편', 0),
    'data_gap_pct':        ('데이터 갭', '%', 1),
    'mtow_kg':             ('최대이륙중량', 'kg', 0),
    'afbr':                ('AFBR', '', 3),
    'ER':                  ('배출량보고서', '', 'auto'),
    'VR':                  ('검증보고서', '', 'auto'),
    'marked':              ('표시값', '', None),
    'expected':            ('기대값', '', None),
    # 글자 항목
    'EMP':                 ('모니터링 계획', '', None),
    'EMP_fuel_types':      ('EMP 등재 연료', '', None),
    'not_in_EMP':          ('EMP에 없는 연료', '', None),
    'flight_id':           ('항공편', '', None),
    'missing_flight_id':   ('누락 항공편', '', None),
    'flagged_flights':     ('해당 항공편', '', None),
    'registration':        ('등록부호', '', None),
    'icao_type':           ('기종', '', None),
    'aircraft_type':       ('기종', '', None),
    'dep':                 ('출발', '', None),
    'arr':                 ('도착', '', None),
    'dep_state':           ('출발국', '', None),
    'arr_state':           ('도착국', '', None),
    'route':               ('노선', '', None),
    'fuel_type':           ('연료종류', '', None),
    'fuel_classes':        ('연료 계열', '', None),
    'saf_batch_id':        ('적격연료 배치', '', None),
    'certificate_no':      ('인증서 번호', '', None),
    'blend_date':          ('혼합일', '', None),
    'date_utc':            ('날짜', '', None),
    'issue_date':          ('작성일', '', None),
    'reporting_year':      ('보고연도', '', None),
    'period_end':          ('이행기간 종료', '', None),
    'document':            ('서류', '', None),
    'item':                ('항목', '', None),
    'key':                 ('항목 키', '', None),
    'keys':                ('누락 키', '', None),
    'missing':             ('누락', '', None),
    'missing_item':        ('누락 항목', '', None),
    'missing_record':      ('누락 자료', '', None),
    'missing_points':      ('누락 지점', '', None),
    'issue':               ('문제', '', None),
    'note':                ('비고', '', None),
    'category':            ('변경 사유', '', None),
    'version':             ('버전', '', None),
    'template_version':    ('서식 버전', '', None),
    'selected':            ('선택값', '', None),
    'filled_levels':       ('작성된 보고 수준', '', None),
    'names':               ('법인명', '', None),
    'addresses':           ('주소', '', None),
    'program':             ('배출권 프로그램', '', None),
    'carried_forward':     ('이월', '', None),
    'cited_clause':        ('인용 조항', '', None),
}


def _to_dec(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None
    if isinstance(v, str):
        t = v.strip()
        if not t or not any(ch.isdigit() for ch in t):
            return None
        try:
            return Decimal(t)
        except InvalidOperation:
            return None
    return None


def _fmt_num(d, places):
    if places == 'auto':
        places = 0 if d == d.to_integral_value() else 1
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f'{q:,.{places}f}'


def _fmt_plain(v):
    if isinstance(v, dict):
        return ' / '.join(f'{k} {_fmt_plain(x)}' for k, x in v.items())
    if isinstance(v, (list, tuple, set)):
        return ', '.join(_fmt_plain(x) for x in v)
    return str(v)


def format_value(key, value):
    """항목 하나를 「이름 값 단위」 문자열로."""
    if key in SAMPLE_LABELS:
        name, unit, places = SAMPLE_LABELS[key]
        if places is not None:
            d = _to_dec(value)
            if d is not None:
                return f'{name} {_fmt_num(d, places)}' + ('' if not unit else unit if unit == '%' else f' {unit}')
        return f'{name} {_fmt_plain(value)}'
    # 대응표에 없는 항목: 이름은 영어 그대로, 소수만 둘째 자리까지
    if isinstance(value, (float, Decimal)) or (isinstance(value, str) and '.' in value):
        d = _to_dec(value)
        if d is not None and d != d.to_integral_value():
            q = d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize()
            return f'{key} {q:f}'
    return f'{key} {_fmt_plain(value)}'


def format_sample(sample):
    """위반 사례 하나(dict)를 한 줄로. 항목 사이는 「 · 」."""
    return ' · '.join(format_value(k, v) for k, v in sample.items())
