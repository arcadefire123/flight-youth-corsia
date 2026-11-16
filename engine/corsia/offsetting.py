"""
C-08 · 연도별 상쇄의무량 / C-09 · 운영사 개별 성장계수
근거: SARPs 3.2.1~3.2.5 / 지침 제9조①, 별표6 제2~4호
"""
from decimal import Decimal
from .constants import PCT_TABLE, OGF_DEFAULT_BASE, r, to_dec

__all__ = ['pct_for_year', 'ogf', 'offsetting_requirement', 'eu_ets_adjustment']


def pct_for_year(year):
    """해당 연도의 (%S, %O). 지침 별표6 제3호는 2024년부터만 규정한다.
    2021~2023은 SARPs 3.2.1에만 있으므로 미확정 사항 1이 해소될 때까지
    SARPs 값을 쓰되 경고를 함께 반환한다.
    """
    for start, end, s, o in PCT_TABLE:
        if start <= year <= end:
            warn = ('국내 지침 별표6 제3호에 2021~2023 구간이 없음 (미확정 1)'
                    if year <= 2023 else None)
            return s, o, warn
    raise ValueError(f'CORSIA 적용 범위(2021~2035) 밖의 연도: {year}')


def ogf(oe_y, oe_base=None, is_new_entrant=False, is_exempt_special=False):
    """C-09: OGF_y = (OE_y - OE_(B,y)) / OE_y

    결과는 **퍼센트(성장률)** 이다. 배수가 아니다. X-27 검증 대상.
    oe_base : 2019년 총 배출량의 85%. 신규 진입자 또는 상쇄의무 특례 대상이면
              10,000톤을 적용한다 (지침 별표6 제4호 단서).
    """
    oe = to_dec(oe_y)
    if oe is None:
        return None
    if oe == 0:
        raise ValueError('OE_y가 0입니다. OGF 계산 불가 (KR-06c 가드)')
    base = (OGF_DEFAULT_BASE if (is_new_entrant or is_exempt_special or oe_base is None)
            else to_dec(oe_base))
    return r((oe - base) / oe, 4)


def offsetting_requirement(oe_y, year, sgf, ogf_value=None, **ogf_kwargs):
    """C-08: OR_y = %S_y × (OE_y × SGF_y) + %O_y × (OE_y × OGF_y)

    sgf : ICAO 공표 부문 성장계수 (비율, 예: 2024년 공식값 0.15405257)
    """
    oe = to_dec(oe_y)
    s, o, warn = pct_for_year(year)
    sgf = to_dec(sgf)
    if o > 0 and ogf_value is None:
        ogf_value = ogf(oe_y, **ogf_kwargs)
    ogf_value = to_dec(ogf_value, Decimal('0'))

    sector_part = s * (oe * sgf)
    individual_part = o * (oe * ogf_value)
    return {
        'year': year, 'OE_y': oe, 'pct_S': s, 'pct_O': o,
        'SGF': sgf, 'OGF': ogf_value,
        'sector_part_t': r(sector_part, 2),
        'individual_part_t': r(individual_part, 2),
        'OR_y_t': r(sector_part + individual_part, 2),
        'warning': warn,
    }


def eu_ets_adjustment(flights, co2_by_flight):
    """C-12 / X-23 · 지침 제9조③
    EU 역내 구간을 EU-ETS 배출권으로 상쇄한 경우 그 구간 배출량을 제외한다.
    반환: (차감액, 대상 편수)
    """
    total = Decimal('0')
    n = 0
    for f, c in zip(flights, co2_by_flight):
        if str(f.get('eu_ets_offset_flag')) == '1' and c is not None:
            total += c
            n += 1
    return r(total, 1), n
