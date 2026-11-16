"""
형식 검증 (F) · 임계값 검증 (T)
근거: verification_rules_v1.md §1, §2
"""
import re
from decimal import Decimal, InvalidOperation

from ..constants import (FUEL_TYPES, MTOW_MIN_KG, THRESHOLD_CERT,
                         THRESHOLD_CERT_1920, THRESHOLD_EXEMPT, to_dec)
from ..co2 import cert_eligible, is_reportable
from .rules import (rule, CRITICAL, MAJOR, MINOR, INFO,
                    LAYER_FORMAT, LAYER_THRESHOLD)

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# ─────────────────────────────── 형식 검증 (F) ──────────────────────

@rule('F-08', LAYER_FORMAT, MAJOR, '필수 항목 빈칸 없음', 'OoM 5')
def f08(ctx):
    required = ['flight_id', 'date_utc', 'registration', 'icao_type',
                'dep_aerodrome', 'arr_aerodrome', 'fuel_type', 'block_hours']
    out = []
    for f in ctx['flights']:
        missing = [k for k in required if not str(f.get(k, '')).strip()]
        if missing:
            out.append({'flight_id': f.get('flight_id'), 'missing': missing})
    return out


@rule('F-16', LAYER_FORMAT, MINOR, '날짜 형식 YYYY-MM-DD', 'ETM 제3장 EUCR 필드 5.d (YYYY-MM-DD) · 지침 별표5 2a·c1·n1')
def f16(ctx):
    return [{'flight_id': f.get('flight_id'), 'date_utc': f.get('date_utc')}
            for f in ctx['flights'] if not DATE_RE.match(str(f.get('date_utc', '')))]


@rule('F-17', LAYER_FORMAT, MAJOR, '연료종류가 허용 6종 이내',
      '영 별표3 제1호 / 지침 별지2 3a')
def f17(ctx):
    return [{'flight_id': f.get('flight_id'), 'fuel_type': f.get('fuel_type')}
            for f in ctx['flights']
            if (f.get('fuel_type') or '').strip().upper() not in FUEL_TYPES]


@rule('F-18', LAYER_FORMAT, MAJOR, '항공기 등록부호 중복 기재 없음', 'OoM 17')
def f18(ctx):
    seen, dup = set(), []
    for a in ctx['aircraft'].values() if isinstance(ctx['aircraft'], dict) else ctx['aircraft']:
        reg = a['registration']
        if reg in seen:
            dup.append({'registration': reg})
        seen.add(reg)
    return dup


@rule('F-19', LAYER_FORMAT, MAJOR, 'MTOW 5,700kg 초과 항공기만 대상',
      '법 제6조①1 "초과" (지침 별표4 3a는 "이상" - 미확정 5)')
def f19(ctx):
    """상쇄대상으로 집계된 편 중 MTOW 기준 미달 항공기가 있는지."""
    out = []
    for f in ctx['flights']:
        if not is_reportable(f):
            continue
        a = ctx['aircraft'].get(f.get('registration'))
        if a and to_dec(a['mtow_kg'], Decimal('0')) <= Decimal(MTOW_MIN_KG):
            out.append({'flight_id': f.get('flight_id'),
                        'registration': f.get('registration'),
                        'mtow_kg': a['mtow_kg']})
    return out


@rule('F-20', LAYER_FORMAT, MAJOR, '상쇄의무량은 소수점 없는 정수 톤',
      'ETM 제3장 EUCR 필드 3·4 · 지침 제9조①')
def f20(ctx):
    v = ctx.get('final_offset', {}).get('FOR_t')
    return [] if isinstance(v, int) else [{'FOR_t': str(v), 'type': type(v).__name__}]


@rule('F-21', LAYER_FORMAT, MAJOR, '배출권 일련번호 배치 단위·오름차순',
      '지침 별표7 제3호', needs=('emission_units',))
def f21(ctx):
    units = ctx.get('emission_units') or []
    out, last = [], {}
    for u in units:
        prog = u.get('program')
        start, end = u.get('serial_start'), u.get('serial_end')
        if not start or not end:
            out.append({'program': prog, 'issue': '시작 또는 끝 일련번호 누락'})
            continue
        if start > end:
            out.append({'program': prog, 'issue': f'{start} > {end}'})
        if prog in last and start <= last[prog]:
            out.append({'program': prog, 'issue': f'오름차순 아님 ({start} ≤ {last[prog]})'})
        last[prog] = end
    return out


# ─────────────────────────────── 임계값 검증 (T) ────────────────────

@rule('T-01', LAYER_THRESHOLD, MAJOR, '이행의무자 지정 요건 (1만톤 이상)',
      '법 제6조①1 "이상" · ETM 2.2.4 "초과"')
def t01(ctx):
    oe = ctx['OE_y_t']
    if oe < Decimal(10_000):
        return [{'OE_y_t': str(oe),
                 'note': '연간 1만톤 미만 - 이행의무자 지정 요건 미달'}]
    return []


@rule('T-02', LAYER_THRESHOLD, CRITICAL, '5만톤 이상이면 연료사용량 방법 의무',
      'SARPs 2.2.1.3.1 · 지침 별표1 4a · 지침 제3조③ 단서')
def t02(ctx):
    # SARPs 2.2.1.3.1: 상쇄 대상 국제선의 연간 CO2(국내 EU-ETS 차감 전)로 판정
    oe, year = ctx['offsetting_co2_t'], ctx['year']
    method = ctx['method']
    if not cert_eligible(oe, year) and method == 'CERT':
        return [{'offsetting_co2_t': str(oe), 'method': method,
                 'note': '상쇄 대상 배출량 5만톤 이상인데 CERT를 사용함'}]
    return []


@rule('T-04', LAYER_THRESHOLD, CRITICAL, '데이터 갭 5% 초과 여부',
      '지침 별표4 6-b / SARPs 2.5.1')
def t04(ctx):
    pct = ctx['data_gap_pct']
    if pct > Decimal('5.0'):
        return [{'data_gap_pct': str(pct),
                 'note': '5% 초과 - 상세 내역 보고 의무 발생'}]
    return []


@rule('T-04b', LAYER_THRESHOLD, MAJOR, '갭 비율 소수점 1자리 반올림',
      '지침 별표4 6-b1 · SARPs 2.5.1')
def t04b(ctx):
    pct = ctx['data_gap_pct']
    return [] if pct == pct.quantize(Decimal('0.1')) else [{'data_gap_pct': str(pct)}]


@rule('T-05', LAYER_THRESHOLD, INFO, '상쇄의무 면제 판정 (3천톤 미만)',
      '영 제9조①1 · SARPs 3.4.2')
def t05(ctx):
    v = ctx.get('final_offset', {}).get('FOR_t')
    if v is not None and v < THRESHOLD_EXEMPT:
        return [{'FOR_t': v, 'note': '3천톤 미만 - 다음 이행기간 면제 대상'}]
    return []


@rule('T-07', LAYER_THRESHOLD, MINOR, '편당 평균 연료소비 250톤 초과 노선',
      'OoM 28')
def t07(ctx):
    return [r for r in ctx['route_stats'] if r['avg_fuel_per_flight'] > Decimal('250')]


@rule('T-08', LAYER_THRESHOLD, MINOR, '편당 평균 연료소비 1톤 미만 노선',
      'OoM 29')
def t08(ctx):
    return [r for r in ctx['route_stats'] if r['avg_fuel_per_flight'] < Decimal('1')]


@rule('T-10', LAYER_THRESHOLD, MAJOR, 'AFBR 소수점 3자리 반올림',
      '지침 별표2 제5호 주3 · SARPs 부록2 주2', needs=('afbr_values',))
def t10(ctx):
    out = []
    for k, v in (ctx.get('afbr_values') or {}).items():
        if v != v.quantize(Decimal('0.001')):
            out.append({'aircraft_type': k, 'afbr': str(v)})
    return out


@rule('T-11', LAYER_THRESHOLD, CRITICAL, 'FOR 음수면 0, 이월 없음',
      '지침 별표6 제1호 / SARPs 3.4.3')
def t11(ctx):
    fo = ctx.get('final_offset', {})
    if fo.get('was_negative') and (fo.get('FOR_t') != 0 or fo.get('carried_forward')):
        return [{'FOR_t': fo.get('FOR_t'), 'carried_forward': fo.get('carried_forward')}]
    return []
