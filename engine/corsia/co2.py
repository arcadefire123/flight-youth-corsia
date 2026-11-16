"""
C-06 · CO2 환산 / C-07 · CERT 적용 판정
근거: SARPs Part II Ch.2 / 영 별표3 / 지침 제3조③·제4조②③, 별표1 4-a
"""
from decimal import Decimal
from .constants import (FCF, FUEL_TYPES, THRESHOLD_CERT, THRESHOLD_CERT_1920,
                        MTOW_MIN_KG, r, to_dec)

__all__ = ['co2_from_fuel', 'annual_co2', 'is_obligor_aircraft', 'is_reportable', 'is_offsetting',
           'cert_eligible', 'monitoring_method_required', 'data_gap_ratio']


def co2_from_fuel(mass_t, fuel_type):
    """CO2 = M_f × FCF_f"""
    ft = (fuel_type or '').strip().upper()
    if ft not in FUEL_TYPES:
        raise ValueError(f'허용되지 않는 연료종류: {fuel_type} (F-17)')
    m = to_dec(mass_t)
    if m is None:
        return None
    return r(m * FCF[ft], 3)


def is_obligor_aircraft(mtow_kg):
    """법 제6조①1: MTOW 5,700kg을 '초과'. 정확히 5,700kg은 제외된다."""
    return to_dec(mtow_kg, Decimal('0')) > Decimal(MTOW_MIN_KG)


def is_reportable(f):
    """모니터링·보고 대상 국제운항편: 국제선이고 인도적·의료 목적이 아닌 편 (SARPs 1.1.2)"""
    return str(f.get('is_international')) == '1' and (f.get('flight_purpose') or 'NORMAL') == 'NORMAL'


def is_offsetting(f):
    """상쇄의무 대상편: 보고 대상 중 출발·도착국이 모두 해당 연도 CORSIA 참여국인 편
    (ICAO 「CORSIA States for Chapter 3 State Pairs」). 데이터의 is_offsetting_scope로 표시한다."""
    return is_reportable(f) and str(f.get('is_offsetting_scope')) == '1'


def annual_co2(flights, fuel_by_flight):
    """연간 CO2 합계. 보고 대상 전체와 그중 상쇄 대상을 따로 집계한다.

    - total_*       : 보고 대상 국제운항 전체 (배출량보고서 총 배출량, CERT·중요성 기준 판단)
    - offsetting_*  : 상쇄 대상 노선만 (상쇄의무량 계산의 OE_y)
    참여국이 아닌 나라를 오가는 노선(예: 대만)은 보고는 하지만 상쇄 대상은 아니다.
    """
    tot_f = tot_c = off_f = off_c = Decimal('0')
    counted = off_n = skipped = 0
    errors = []
    for f, fuel in zip(flights, fuel_by_flight):
        if not is_reportable(f) or fuel is None:
            skipped += 1
            continue
        ft = (f.get('fuel_type') or '').strip().upper()
        if ft not in FUEL_TYPES:
            # F-17 위반. 계산엔진은 멈추지 않고 기록만 남기고 건너뛴다.
            # 판정은 검증엔진(5단계)의 몫이다.
            errors.append({'flight_id': f.get('flight_id'), 'rule': 'F-17',
                           'detail': f'허용되지 않는 연료종류: {f.get("fuel_type")}'})
            skipped += 1
            continue
        c = fuel * FCF[ft]
        tot_f += fuel; tot_c += c; counted += 1
        if is_offsetting(f):
            off_f += fuel; off_c += c; off_n += 1
    return {
        'total_fuel_t': r(tot_f, 1),
        'total_co2_t': r(tot_c, 1),
        'counted_flights': counted,
        'offsetting_fuel_t': r(off_f, 1),
        'offsetting_co2_t': r(off_c, 1),
        'offsetting_flights': off_n,
        'skipped_flights': skipped,
        'errors': errors,
    }


def data_gap_ratio(flights, fuel_by_flight=None):
    """데이터 갭 비율. 소수점 1자리에서 반올림 (지침 별표4 6-b1 / T-04b)

    분모는 보고 대상 편수, 분자는 그중 갭 플래그가 선 편수.
    """
    scope = [f for f in flights if is_reportable(f)]
    if not scope:
        return Decimal('0')
    gaps = sum(1 for f in scope if str(f.get('data_gap_flag')) == '1')
    pct = Decimal(gaps) / Decimal(len(scope)) * 100
    return r(pct, 1)


def cert_eligible(annual_co2_t, year):
    """C-07: CERT 사용 가능 여부.
    2019~2020은 50만톤, 2021~은 5만톤 기준. 기준 '이상'이면 CERT 불가.
    """
    threshold = THRESHOLD_CERT_1920 if year in (2019, 2020) else THRESHOLD_CERT
    return to_dec(annual_co2_t, Decimal('0')) < Decimal(threshold)


def monitoring_method_required(annual_co2_t, year, is_first_designated_year=False):
    """상쇄 대상 국제선의 연간 CO2가 5만톤 이상이면 연료사용량 방법(별표2) 의무.
    근거: SARPs 2.2.1.3.1, 지침 별표1 4a. (지침 제3조③은 '국제항공 탄소 배출량'으로만 적었으나
    별표1 4a와 ICAO 원문은 상쇄 대상 국제선 기준.) 이행의무자로 지정된 최초 연도는 제외(제3조③ 단서).
    """
    if is_first_designated_year:
        return 'EITHER'
    return 'FUEL_USE_REQUIRED' if not cert_eligible(annual_co2_t, year) else 'EITHER'
