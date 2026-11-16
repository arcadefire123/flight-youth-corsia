"""
5단계 잔여 규칙 - 서류 형식(F) · 서류 간 일치(X) · 상쇄 대상 국가쌍(X-16)
근거: verification_rules_v1.md §1, §3, §6.1

판정할 서류가 없으면 통과가 아니라 SKIPPED다. 이를 위해 서류가 없을 때는
MissingInput을 던지고, run_rules가 이를 SKIPPED로 기록한다.
"""
import re
from decimal import Decimal

from ..constants import (TEMPLATE_VERSION, AIRCRAFT_ID_OPTIONS, ER_REQUIRED,
                         VR_REQUIRED, EUCR_REQUIRED, EMP_CHANGE_CATEGORIES,
                         RECORDS_REQUIRED, to_dec)
from .rules import (rule, CRITICAL, MAJOR, MINOR, LAYER_FORMAT, LAYER_CONSISTENCY,
                    MissingInput)

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _doc(ctx, name):
    d = (ctx.get('documents') or {}).get(name)
    if not d:
        raise MissingInput(f'documents.{name}')
    return d


def _filled(v):
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True     # 숫자 0, False도 '값이 있음'으로 본다


# ─────────────────────────────── 형식 (F) ───────────────────────────

@rule('F-01', LAYER_FORMAT, MAJOR, '항공기운영자 법인명이 존재할 것',
      'OoM 1 · 지침 별표1 2a', needs=('documents',))
def f01(ctx):
    out = []
    for name in ('EMP', 'ER', 'EUCR'):
        d = _doc(ctx, name)
        if not _filled(d.get('operator_name')):
            out.append({'document': name, 'issue': '법인명 비어 있음'})
    return out


@rule('F-02', LAYER_FORMAT, MAJOR,
      '항공기 식별정보 선택(ICAO 지정기호 / 등록부호 / 둘 다)과 해당 값이 채워져 있을 것',
      '지침 별표1 2d · 별지2 1-b · OoM 1', needs=('documents',))
def f02(ctx):
    """ER 템플릿 선택지가 3가지(지정기호 / 등록부호 / 둘 다)라 '둘 다'도 정상이다."""
    out = []
    for name in ('EMP', 'ER'):
        d = _doc(ctx, name)
        opt = d.get('aircraft_identification')
        if opt not in AIRCRAFT_ID_OPTIONS:
            out.append({'document': name, 'selected': opt,
                        'issue': f'선택지는 {sorted(AIRCRAFT_ID_OPTIONS)} 중 하나'})
            continue
        for key in AIRCRAFT_ID_OPTIONS[opt]:
            if not _filled(d.get(key)):
                out.append({'document': name, 'selected': opt, 'missing': key})
    return out


@rule('F-03', LAYER_FORMAT, MINOR, '주소·AOC 등 기본정보 누락 없음', 'OoM 2', needs=('documents',))
def f03(ctx):
    out = []
    for name in ('EMP', 'ER'):
        d = _doc(ctx, name)
        addr = d.get('operator_address') or {}
        for k in ('street', 'city', 'postal_code', 'country'):
            if not _filled(addr.get(k)):
                out.append({'document': name, 'missing': f'operator_address.{k}'})
        aoc = d.get('aoc') or {}
        for k in ('number', 'issuing_authority'):
            if not _filled(aoc.get(k)):
                out.append({'document': name, 'missing': f'aoc.{k}'})
    return out


@rule('F-04', LAYER_FORMAT, CRITICAL, '배출량보고서 필수 15개 항목 전부 포함',
      '지침 제5조①', needs=('documents',))
def f04(ctx):
    er = _doc(ctx, 'ER')
    return [{'missing_item': label, 'keys': list(keys)}
            for label, keys in ER_REQUIRED if not any(_filled(er.get(k)) for k in keys)]


def _check_vr(ctx, name):
    vr = _doc(ctx, name)
    return [{'document': name, 'missing_item': label, 'key': key}
            for label, key in VR_REQUIRED if not _filled(vr.get(key))]


@rule('F-05', LAYER_FORMAT, CRITICAL, '배출량검증보고서 필수 5개 항목 포함',
      '영 제8조③', needs=('documents',))
def f05(ctx):
    return _check_vr(ctx, 'VR')


@rule('F-06', LAYER_FORMAT, CRITICAL, '상쇄의무이행보고서 필수 5개 항목 포함',
      '지침 제11조①', needs=('documents',))
def f06(ctx):
    eucr = _doc(ctx, 'EUCR')
    out = [{'missing_item': label, 'key': key}
           for label, key in EUCR_REQUIRED if not _filled(eucr.get(key))]
    if not ctx.get('emission_units'):
        out.append({'missing_item': '말소 배출권 식별정보', 'key': 'emission_units'})
    return out


@rule('F-07', LAYER_FORMAT, CRITICAL, '상쇄의무이행검증보고서 필수 5개 항목 포함',
      '영 제10조③', needs=('documents',))
def f07(ctx):
    return _check_vr(ctx, 'VR_EUCR')


@rule('F-09', LAYER_FORMAT, MAJOR, '올바른 서식 버전 사용', 'OoM 7', needs=('documents',))
def f09(ctx):
    out = []
    for name in ('ER', 'VR'):
        v = _doc(ctx, name).get('template_version')
        if v != TEMPLATE_VERSION:
            out.append({'document': name, 'template_version': v, 'expected': TEMPLATE_VERSION})
    return out


@rule('F-10', LAYER_FORMAT, MINOR,
      '배출량보고서 작성일이 보고연도가 끝난 뒤, 다음 해 안일 것', 'OoM 8', needs=('documents',))
def f10(ctx):
    """보고연도 배출량은 연도가 끝나야 확정되므로, 작성일이 보고연도 안이면 이상하다.
    다음 해를 넘겨 작성됐어도 이상하다(제출 기한은 다음 해 4월 30일)."""
    er = _doc(ctx, 'ER')
    d = str(er.get('issue_date') or '')
    yr = int(er.get('reporting_year') or ctx['year'])
    if not DATE_RE.match(d):
        return [{'issue_date': d, 'issue': '날짜 형식 오류 또는 누락'}]
    if not (f'{yr + 1}-01-01' <= d <= f'{yr + 1}-12-31'):
        return [{'issue_date': d, 'reporting_year': yr,
                 'issue': f'작성일은 {yr + 1}년 안이어야 함'}]
    return []


@rule('F-26', LAYER_FORMAT, MAJOR,
      '변경승인 대상 사유(5종)에 해당하는 모니터링 계획 변경은 승인을 받았을 것',
      '지침 제3조⑥', needs=('documents',))
def f26(ctx):
    emp = _doc(ctx, 'EMP')
    out = []
    for v in emp.get('version_history') or []:
        for ch in v.get('changes') or []:
            if ch.get('category') in EMP_CHANGE_CATEGORIES and not ch.get('approved'):
                out.append({'version': v.get('version'), 'category': ch.get('category'),
                            'issue': '변경승인 대상인데 승인 기록 없음'})
    return out


@rule('F-28', LAYER_FORMAT, MAJOR, '이행의무자 보관 자료 4종이 모두 보관되고 있을 것',
      '규칙 제10조①', needs=('documents',))
def f28(ctx):
    kept = set(_doc(ctx, 'EMP').get('records_retained') or [])
    return [{'missing_record': r} for r in RECORDS_REQUIRED if r not in kept]


# ─────────────────────────────── 서류 간 일치 (X) ───────────────────

def _norm_addr(a):
    return tuple(' '.join(str((a or {}).get(k, '')).split())
                 for k in ('street', 'city', 'postal_code', 'country'))


@rule('X-02', LAYER_CONSISTENCY, MAJOR, 'EMP 주소 = ER 주소 = EUCR 주소',
      'ETM 부록1 EUCR 1-b', needs=('documents',))
def x02(ctx):
    addrs = {n: _norm_addr(_doc(ctx, n).get('operator_address')) for n in ('EMP', 'ER', 'EUCR')}
    return [] if len(set(addrs.values())) == 1 else [{'addresses': {k: ', '.join(v) for k, v in addrs.items()}}]


@rule('X-03', LAYER_CONSISTENCY, CRITICAL,
      '항공사 보고서와 검증보고서의 연료소비량·비행횟수 일치', 'OoM 6', needs=('documents',))
def x03(ctx):
    er, vr = _doc(ctx, 'ER'), _doc(ctx, 'VR')
    out = []
    f_er, f_vr = to_dec(er.get('total_fuel_t')), to_dec(vr.get('verified_total_fuel_t'))
    if f_er is None or f_vr is None or (f_er - f_vr).copy_abs() > Decimal('0.5'):
        out.append({'item': '연료소비량(t)', 'ER': str(f_er), 'VR': str(f_vr)})
    if er.get('total_flights') != vr.get('verified_total_flights'):
        out.append({'item': '비행횟수', 'ER': er.get('total_flights'),
                    'VR': vr.get('verified_total_flights')})
    return out


@rule('X-04', LAYER_CONSISTENCY, CRITICAL, 'ER의 모니터링 방법 = 승인된 EMP의 방법',
      'OoM 12·13', needs=('documents',))
def x04(ctx):
    a, b = _doc(ctx, 'EMP').get('monitoring_method'), _doc(ctx, 'ER').get('monitoring_method')
    return [] if a and a == b else [{'EMP': a, 'ER': b}]


@rule('X-05', LAYER_CONSISTENCY, MAJOR, 'ER의 연료종류가 EMP에 등재된 연료종류 안에 있을 것',
      'OoM 21', needs=('documents',))
def x05(ctx):
    allowed = {str(x).upper() for x in (_doc(ctx, 'EMP').get('fuel_types') or [])}
    used = {str(x).upper() for x in (_doc(ctx, 'ER').get('fuel_mass_by_type') or {})}
    extra = sorted(used - allowed)
    return [] if not extra else [{'not_in_EMP': extra, 'EMP_fuel_types': sorted(allowed)}]


@rule('X-06', LAYER_CONSISTENCY, CRITICAL,
      'ER의 적격연료 감축량 = 적격연료 사용신고서(별지3) 합계', 'OoM 38 · 지침 제7조①',
      needs=('documents',))
def x06(ctx):
    er_claim = _doc(ctx, 'ER').get('cef_claim') or {}
    decl = _doc(ctx, 'CEF_DECL')
    a, b = to_dec(er_claim.get('ER_t')), to_dec(decl.get('total_ER_t'))
    out = []
    if a is None or b is None or (a - b).copy_abs() > Decimal('0.01'):
        out.append({'item': '감축량(t)', 'ER': str(a), '별지3': str(b)})
    ids_er = set(er_claim.get('batches') or [])
    ids_decl = {x.get('saf_batch_id') for x in decl.get('batches') or []}
    if ids_er != ids_decl:
        out.append({'item': 'SAF 배치 목록', 'ER': sorted(ids_er), '별지3': sorted(ids_decl)})
    return out


# ─────────────────────────────── 상쇄 대상 국가쌍 (X-16) ────────────

@rule('X-16', LAYER_CONSISTENCY, CRITICAL,
      '상쇄의무 대상 여부 분류가 참여국 목록과 일치',
      'OoM 25 · ICAO 「CORSIA States for Chapter 3 State Pairs」', needs=('corsia_states',))
def x16(ctx):
    """출발·도착국이 모두 해당 연도 참여국이면 상쇄 대상, 하나라도 아니면 비대상.
    국내선과 인도적·의료 목적 편은 판정에서 제외한다."""
    states = ctx['corsia_states']
    out = []
    for f in ctx['flights']:
        dep, arr = f.get('dep_state'), f.get('arr_state')
        if dep == arr or (f.get('flight_purpose') or 'NORMAL') != 'NORMAL':
            continue
        unknown = [s for s in (dep, arr) if s not in states]
        if unknown:
            out.append({'flight_id': f.get('flight_id'), 'issue': f'참여국 목록에 없는 국가 {unknown}'})
            continue
        expected = '1' if (states[dep] and states[arr]) else '0'
        if str(f.get('is_offsetting_scope')) != expected:
            out.append({'flight_id': f.get('flight_id'), 'route': f'{dep}-{arr}',
                        'marked': f.get('is_offsetting_scope'), 'expected': expected})
    return out
