"""
C-11 · 이행기간 총 최종 상쇄의무량 / C-12 · 면제 판정 및 중요성 기준
근거: SARPs 3.4 / 지침 제9조①, 별표6 제1호 / 영 제9조①
"""
from decimal import Decimal, ROUND_CEILING
from .constants import (THRESHOLD_EXEMPT, MATERIALITY_BOUNDARY_T, MATERIALITY_HIGH, MATERIALITY_LOW,
                        r, r_int, to_dec)

__all__ = ['final_offsetting_requirement', 'exemption_check',
           'materiality_threshold', 'materiality']


def final_offsetting_requirement(or_list, er_list):
    """FOR_c = Σ OR_(y,c) - Σ ER_(y,c)

    - 톤 단위로 올림한다 (SARPs 3.4.4: "rounded up to the nearest tonne").
      국문 번역본과 지침 제9조①·별표6 제1호는 "반올림"이지만, ICAO 기준 단일 적용 방침에 따라 영문 원문을 따른다.
    - 음수이면 0으로 간주하며 다음 이행기간으로 이월하지 않는다 (SARPs 3.4.3)
    """
    sum_or = sum((to_dec(v, Decimal('0')) for v in or_list), Decimal('0'))
    sum_er = sum((to_dec(v, Decimal('0')) for v in er_list), Decimal('0'))
    raw = sum_or - sum_er
    value = int(raw.quantize(Decimal('1'), rounding=ROUND_CEILING))
    return {
        'sum_OR_t': r(sum_or, 2),
        'sum_ER_t': r(sum_er, 2),
        'raw_t': r(raw, 2),
        'FOR_t': max(value, 0),
        'was_negative': value < 0,
        'carried_forward': False,  # SARPs 3.4.3: 이월 없음
    }


def exemption_check(prev_period_total_or=None, is_new_entrant_within_3y=False,
                    emissions_since_start=None, world_2019_emissions=None):
    """C-12: 상쇄의무 이행 면제 (영 제9조①)

    1호: 직전 이행기간 통보받은 총 상쇄의무량 < 3,000 t
    2호: 국제항공운송사업 면허 첫해 포함 3년 미경과 + 국제운항 개시 후 총 배출량이
         2019년 세계 국제항공 배출량의 1/1000 이하
    """
    reasons = []
    if prev_period_total_or is not None:
        if to_dec(prev_period_total_or) < Decimal(THRESHOLD_EXEMPT):
            reasons.append('영 제9조①1: 직전 이행기간 총 상쇄의무량 3천톤 미만')
    if is_new_entrant_within_3y and emissions_since_start is not None \
            and world_2019_emissions is not None:
        limit = to_dec(world_2019_emissions) / Decimal('1000')
        if to_dec(emissions_since_start) <= limit:
            reasons.append('영 제9조①2: 신규 진입 3년 이내 + 세계 배출량 1/1000 이하')
    return {'exempt': bool(reasons), 'reasons': reasons}


def materiality_threshold(annual_co2_t):
    """중요성 임계치 (SARPs App.6 3.5.1)
    연간 국제선 배출량 50만 t 초과 → 2%, 50만 t 이하 → 5%
    """
    v = to_dec(annual_co2_t, Decimal('0'))
    return MATERIALITY_HIGH if v > MATERIALITY_BOUNDARY_T else MATERIALITY_LOW


def materiality(annual_co2_t):
    """검증 허용오차 계산. X-12가 재계산값과 보고값의 차이를 이 값과 비교한다."""
    pct = materiality_threshold(annual_co2_t)
    return {
        'annual_co2_t': to_dec(annual_co2_t),
        'standard': 'ICAO Annex 16 Vol.IV App.6 3.5.1',
        'applied_pct': pct,
        'limit_t': r(to_dec(annual_co2_t) * pct, 1),
    }
