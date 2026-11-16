"""
일관성 검증 (X)
근거: verification_rules_v1.md §3
ETM 3.3.8.1 허위진술·부적합 예시와 1.4 OoM 체크리스트에서 옮겨온 규칙이 대부분이다.
"""
import re
from collections import defaultdict
from decimal import Decimal

from ..constants import FCF, to_dec
from ..co2 import is_reportable
from .rules import (rule, CRITICAL, MAJOR, MINOR, INFO, LAYER_CONSISTENCY)

FID_RE = re.compile(r'^([A-Za-z]*)(\d+)$')


def _fuel_class(ft):
    ft = (ft or '').strip().upper()
    if ft not in FCF:
        return None
    return 'JET' if FCF[ft] == Decimal('3.16') else 'AVGAS'


# ── 문서 간 일치 ────────────────────────────────────────────────────

@rule('X-01', LAYER_CONSISTENCY, CRITICAL,
      'EMP 2a = ER 1a = EUCR 1a 법인명 일치',
      '지침 별표7 1a · ETM 부록1 EUCR 1-a', needs=('documents',))
def x01(ctx):
    docs = ctx.get('documents') or {}
    names = {k: (docs.get(k) or {}).get('operator_name')
             for k in ('EMP', 'ER', 'EUCR') if docs.get(k)}
    vals = {v for v in names.values() if v}
    return [] if len(vals) <= 1 else [{'names': names}]


@rule('X-07', LAYER_CONSISTENCY, CRITICAL,
      'EUCR 총 최종 상쇄의무량 = 국토교통부 통보값',
      '지침 별표7 2-a', needs=('documents',))
def x07(ctx):
    docs = ctx.get('documents') or {}
    eucr = docs.get('EUCR') or {}
    reported = eucr.get('total_final_offsetting_requirement')
    computed = ctx.get('final_offset', {}).get('FOR_t')
    if reported is None or computed is None:
        return []
    if int(reported) != int(computed):
        return [{'reported': reported, 'computed': computed}]
    return []


@rule('X-08', LAYER_CONSISTENCY, CRITICAL,
      '말소 배출권 총 수량 ≥ 총 최종 상쇄의무량',
      'ETM 허위진술 d / 지침 제10조①', needs=('emission_units',))
def x08(ctx):
    units = ctx.get('emission_units') or []
    total = sum(int(u.get('quantity', 0)) for u in units)
    need = ctx.get('final_offset', {}).get('FOR_t')
    if need is None:
        return []
    return [] if total >= need else [{'cancelled': total, 'required': need}]


# ── 보고서 내부 정합 ────────────────────────────────────────────────

@rule('X-09', LAYER_CONSISTENCY, CRITICAL,
      '보고 수준은 국가쌍·공항쌍 중 하나만 선택',
      '지침 별표4 5-a', needs=('documents',))
def x09(ctx):
    er = (ctx.get('documents') or {}).get('ER') or {}
    chosen = [k for k in ('state_pairs', 'aerodrome_pairs') if er.get(k)]
    if len(chosen) != 1:
        return [{'filled_levels': chosen or ['(없음)'],
                 'note': '정확히 하나만 작성되어야 함'}]
    return []


@rule('X-10', LAYER_CONSISTENCY, CRITICAL,
      '선택된 수준의 노선별 배출량 합계 = 총 배출량',
      '지침 별표4 5.1·5.2', needs=('documents',))
def x10(ctx):
    er = (ctx.get('documents') or {}).get('ER') or {}
    level = er.get('state_pairs') or er.get('aerodrome_pairs')
    if not level:
        return []
    s = sum(to_dec(r.get('co2_t'), Decimal('0')) for r in level)
    total = to_dec(er.get('total_co2_t'), Decimal('0'))
    diff = (s - total).copy_abs()
    return [] if diff <= Decimal('0.5') else [
        {'sum_of_routes_t': str(s), 'reported_total_t': str(total), 'diff_t': str(diff)}]


@rule('X-11', LAYER_CONSISTENCY, CRITICAL,
      '선택된 수준의 노선별 편수 합계 = 총 국제항공편 수',
      '지침 별지2 5.1a/5.2a', needs=('documents',))
def x11(ctx):
    er = (ctx.get('documents') or {}).get('ER') or {}
    level = er.get('state_pairs') or er.get('aerodrome_pairs')
    if not level:
        return []
    s = sum(int(r.get('flights', 0)) for r in level)
    total = int(er.get('total_flights', 0))
    return [] if s == total else [{'sum_of_routes': s, 'reported_total': total}]


@rule('X-12', LAYER_CONSISTENCY, CRITICAL,
      '배출량 = 연료질량 × 연료전환계수 재계산 일치',
      '영 제8조②3 (보완 요청 사유)', needs=('documents',))
def x12(ctx):
    """검증기관이 독립적으로 재계산한 값과 이행의무자 보고값을 대조한다.
    영 제8조제2항제3호가 이 대조를 직접 명령하고 있다."""
    er = (ctx.get('documents') or {}).get('ER') or {}
    reported = to_dec(er.get('total_co2_t'))
    if reported is None:
        return []
    computed = ctx['total_co2_t']      # 보고 총 배출량끼리 비교 (상쇄 대상만이 아님)
    diff = (reported - computed).copy_abs()
    tol = computed * ctx['materiality']['applied_pct']   # SARPs App.6 3.5.1 중요성 임계치
    if diff > tol:
        return [{'reported_t': str(reported), 'computed_t': str(computed),
                 'diff_t': str(diff), 'materiality_limit_t': str(tol)}]
    return []


@rule('X-13', LAYER_CONSISTENCY, MAJOR, '왕복 노선 양방향 보고 (A-B, B-A)',
      '지침 별표4 5.1 비고1 / OoM 23')
def x13(ctx):
    pairs = {(r['dep'], r['arr']) for r in ctx['route_stats']}
    out = []
    for dep, arr in sorted(pairs):
        if (arr, dep) not in pairs:
            out.append({'route': f'{dep}-{arr}', 'note': '역방향 노선 미보고'})
    return out


@rule('X-14', LAYER_CONSISTENCY, MAJOR, '동일 노선·동일 연료종류 중복 등재 없음',
      'OoM 26')
def x14(ctx):
    seen, dup = set(), []
    for r in ctx['route_stats']:
        key = (r['dep'], r['arr'], r['fuel_type'])
        if key in seen:
            dup.append({'route': f"{r['dep']}-{r['arr']}", 'fuel_type': r['fuel_type']})
        seen.add(key)
    return dup


@rule('X-14b', LAYER_CONSISTENCY, MAJOR,
      '동일 노선에 연료형식이 다르면 분리 보고',
      '지침 별표4 5.1 비고2 · 5.2 비고2')
def x14b(ctx):
    by_route = defaultdict(set)
    for f in ctx['flights']:
        if not is_reportable(f):
            continue
        by_route[(f['dep_aerodrome'], f['arr_aerodrome'])].add(
            _fuel_class(f.get('fuel_type')))
    out = []
    for (dep, arr), classes in sorted(by_route.items()):
        classes.discard(None)
        if len(classes) > 1:
            out.append({'route': f'{dep}-{arr}', 'fuel_classes': sorted(classes),
                        'note': '연료형식별로 노선을 구분해 분리 보고해야 함'})
    return out


@rule('X-15', LAYER_CONSISTENCY, CRITICAL,
      '출발·도착이 같은 국가면 국제선이 아님', 'OoM 27')
def x15(ctx):
    return [{'flight_id': f.get('flight_id'),
             'dep_state': f.get('dep_state'), 'arr_state': f.get('arr_state')}
            for f in ctx['flights']
            if str(f.get('is_international')) == '1'
            and f.get('dep_state') == f.get('arr_state')]


@rule('X-17', LAYER_CONSISTENCY, MAJOR,
      '밀도 적용이 운항·안전 절차와 동일함을 확인',
      '지침 별표4 4-a1', needs=('documents',))
def x17(ctx):
    er = (ctx.get('documents') or {}).get('ER') or {}
    if 'density_consistency_confirmed' not in er:
        return []
    return [] if er['density_consistency_confirmed'] else [
        {'note': '밀도 일관성 확인란이 부(否)로 표기됨'}]


# ── 원시 데이터 타당성 (ETM 허위진술 예시) ───────────────────────────

@rule('X-18', LAYER_CONSISTENCY, CRITICAL, '항공편 순서에 누락된 항공편 없음',
      'ETM 3.3.8.1 허위진술 a')
def x18(ctx):
    nums, prefix = [], ''
    for f in ctx['flights']:
        m = FID_RE.match(str(f.get('flight_id', '')))
        if m:
            prefix = m.group(1)
            nums.append(int(m.group(2)))
    if not nums:
        return []
    nums = sorted(set(nums))
    missing = []
    for a, b in zip(nums, nums[1:]):
        if b - a > 1:
            missing.extend(range(a + 1, b))
    return [{'missing_flight_id': f'{prefix}{n:06d}'} for n in missing]


@rule('X-19', LAYER_CONSISTENCY, CRITICAL,
      '미해결 데이터 공백 없음 (블록온 연료·급유량 누락)',
      'ETM 3.3.8.1 허위진술 b')
def x19(ctx):
    out = []
    for f in ctx['flights']:
        if not is_reportable(f):
            continue
        if str(f.get('tank_block_on_t', '')).strip() == '':
            out.append({'flight_id': f.get('flight_id'), 'missing': 'tank_block_on_t'})
    return out


@rule('X-20', LAYER_CONSISTENCY, CRITICAL, '급유량이 탱크 용량을 초과하지 않음',
      'ETM 3.3.8.1 허위진술 c')
def x20(ctx):
    out = []
    for f in ctx['flights']:
        u = to_dec(f.get('uplift_tonnes'))
        a = ctx['aircraft'].get(f.get('registration'))
        if u is None or not a:
            continue
        cap = to_dec(a.get('tank_capacity_t'))
        if cap and u > cap:
            out.append({'flight_id': f.get('flight_id'), 'uplift_t': str(u),
                        'tank_capacity_t': str(cap)})
    return out


@rule('X-21', LAYER_CONSISTENCY, CRITICAL,
      '블록온 연료량이 블록오프 연료량보다 크지 않음',
      'ETM 3.3.8.1 허위진술 c')
def x21(ctx):
    out = []
    for f in ctx['flights']:
        off, on = to_dec(f.get('tank_block_off_t')), to_dec(f.get('tank_block_on_t'))
        if off is None or on is None:
            continue
        if on > off:
            out.append({'flight_id': f.get('flight_id'),
                        'block_off_t': str(off), 'block_on_t': str(on)})
    return out


@rule('X-22', LAYER_CONSISTENCY, MINOR,
      '편당 연료소비가 동일 노선 중앙값 대비 현저히 높거나 낮지 않음',
      'ETM 3.3.8.1 허위진술 c')
def x22(ctx):
    """기종 평균이 아니라 **노선별** 중앙값과 비교한다.
    같은 기종이라도 장거리편과 단거리편의 연료소비는 자릿수가 다르므로
    기종 기준으로 비교하면 정상 데이터가 대량으로 걸린다(오탐)."""
    MIN_N = 5
    by_route = defaultdict(list)
    for f, fuel in zip(ctx['flights'], ctx['fuel_by_flight']):
        if fuel is None or not is_reportable(f):
            continue
        key = (f['dep_aerodrome'], f['arr_aerodrome'], f['icao_type'])
        by_route[key].append((f.get('flight_id'), fuel))
    out = []
    for key, items in sorted(by_route.items()):
        if len(items) < MIN_N:
            continue          # 표본이 적으면 판정하지 않는다
        vals = sorted(v for _, v in items)
        med = vals[len(vals) // 2]
        hi, lo = med * Decimal('2'), med / Decimal('2')
        for fid, v in items:
            if v > hi or v < lo:
                out.append({'flight_id': fid,
                            'route': f'{key[0]}-{key[1]}', 'icao_type': key[2],
                            'fuel_t': str(v), 'route_median_t': str(med)})
    return out


# ── 이중 계산 방지 ──────────────────────────────────────────────────

@rule('X-23', LAYER_CONSISTENCY, CRITICAL,
      'EU 역내 구간을 EU-ETS로 상쇄한 경우 산정에서 제외',
      '지침 제9조③ (국내 고유)')
def x23(ctx):
    """조정이 실제로 반영되었는지 확인한다."""
    flagged = [f for f in ctx['flights'] if str(f.get('eu_ets_offset_flag')) == '1']
    if not flagged:
        return []
    if ctx['eu_ets_adjusted_t'] <= 0:
        return [{'flagged_flights': len(flagged), 'adjusted_t': str(ctx['eu_ets_adjusted_t']),
                 'note': 'EU-ETS 상쇄 구간이 있으나 차감되지 않음'}]
    return []


@rule('X-24', LAYER_CONSISTENCY, CRITICAL,
      '적격연료 실적을 타 온실가스 감축 실적에 사용하지 않음',
      '지침 제6조 제3호 / OoM 40')
def x24(ctx):
    out = []
    used = ctx['saf_used_ids']
    for b in ctx['saf_batches']:
        if b['saf_batch_id'] not in used:
            continue
        if str(b.get('double_claimed_elsewhere', '')).strip().upper() == 'Y':
            out.append({'saf_batch_id': b['saf_batch_id'],
                        'note': '타 제도에 이미 감축실적으로 사용됨'})
    # 동일 인증서 번호를 둘 이상의 배치가 사용하는 경우도 이중 청구 신호
    by_cert = defaultdict(list)
    for b in ctx['saf_batches']:
        if b['saf_batch_id'] in used and b.get('certificate_no'):
            by_cert[b['certificate_no']].append(b['saf_batch_id'])
    for cert, ids in by_cert.items():
        if len(ids) > 1:
            out.append({'certificate_no': cert, 'batches': ids,
                        'note': '동일 인증서를 복수 배치가 청구'})
    return out


@rule('X-25', LAYER_CONSISTENCY, MAJOR,
      '적격연료 혼합 시점이 이행기간 종료 전', 'OoM 39')
def x25(ctx):
    out = []
    end = f"{ctx['period_end_year']}-12-31"
    for b in ctx['saf_batches']:
        if b['saf_batch_id'] not in ctx['saf_used_ids']:
            continue
        d = str(b.get('blend_date', ''))
        if d and d > end:
            out.append({'saf_batch_id': b['saf_batch_id'], 'blend_date': d,
                        'period_end': end})
    return out


@rule('X-26', LAYER_CONSISTENCY, MAJOR,
      '보고 배출량이 다른 연료 관련 자료와 비교해 타당',
      '지침 별표2 제5호 말미', needs=('external_fuel_data',))
def x26(ctx):
    ext = ctx.get('external_fuel_data')
    if not ext:
        return []
    reported = ctx['total_fuel_t']
    purchased = to_dec(ext.get('purchased_fuel_t'))
    if purchased is None:
        return []
    diff = (reported - purchased).copy_abs()
    tol = purchased * Decimal('0.05')
    return [] if diff <= tol else [
        {'reported_fuel_t': str(reported), 'purchased_fuel_t': str(purchased),
         'diff_t': str(diff)}]


@rule('X-27', LAYER_CONSISTENCY, CRITICAL,
      'OGF가 퍼센트 범위(0~1) 내 - 배수로 계산되지 않았는지',
      '지침 별표6 제4호')
def x27(ctx):
    v = ctx.get('OGF')
    if v is None:
        return []
    if not (Decimal('0') <= v <= Decimal('1')):
        return [{'OGF': str(v),
                 'note': 'OGF는 성장률(%)이다. 배수로 계산했을 가능성'}]
    return []
