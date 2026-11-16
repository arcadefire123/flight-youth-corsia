"""
C-10 · CORSIA 적격연료 사용에 따른 탄소배출 감축량
근거: SARPs 3.3 / 지침 제9조②, 별표6 제5호·제6호

원문 산식:  ER_y = FCF × [ Σ_f MS_(f,y) × (1 - L_CEF / LC) ]
FCF가 합계(Σ) **밖**에서 한 번만 곱해진다는 점이 핵심이다.
"""
from decimal import Decimal
from .constants import FCF, LC, r, to_dec

__all__ = ['emission_reduction', 'validate_batches']


class MixedFuelClassError(ValueError):
    """Jet 계열(FCF 3.16)과 AvGas 계열(FCF 3.10)이 한 계산에 섞인 경우.

    원문 산식은 FCF를 합계 밖에서 단일 적용하므로 두 계열이 섞이면
    어느 계수를 쓸지 정의되지 않는다 (calc_spec C-10 구현 주의).
    """


def _fcf_class(fuel_type):
    ft = (fuel_type or '').strip().upper()
    if ft not in FCF:
        raise ValueError(f'허용되지 않는 연료종류: {fuel_type}')
    return 'JET' if FCF[ft] == Decimal('3.16') else 'AVGAS'


def emission_reduction(batches, split_by_class=False):
    """ER_y 계산.

    batches : dict 리스트. 키 = equivalent_fuel_type, mass_tonnes,
              l_cef_gco2e_mj, (선택) lc_gco2e_mj
    split_by_class : True면 연료 계열별로 나눠 계산 후 합산한다.
                     False(기본)면 계열이 섞였을 때 MixedFuelClassError를 던진다.
    """
    if not batches:
        return {'ER_y_t': Decimal('0'), 'detail': []}

    classes = {_fcf_class(b['equivalent_fuel_type']) for b in batches}
    if len(classes) > 1 and not split_by_class:
        raise MixedFuelClassError(
            'Jet 계열과 AvGas 계열 적격연료가 섞였습니다. '
            '지침 별표6 제5호는 FCF를 합계 밖에서 단일 적용하므로 계산이 정의되지 '
            '않습니다. split_by_class=True로 분리 계산하거나 입력을 나누십시오. (KR-11)')

    groups = {}
    for b in batches:
        groups.setdefault(_fcf_class(b['equivalent_fuel_type']), []).append(b)

    total = Decimal('0')
    detail = []
    for cls, items in groups.items():
        ft = (items[0]['equivalent_fuel_type'] or '').strip().upper()
        fcf = FCF[ft]
        inner = Decimal('0')
        for b in items:
            ms = to_dec(b['mass_tonnes'])
            l_cef = to_dec(b['l_cef_gco2e_mj'])
            lc = to_dec(b.get('lc_gco2e_mj'), Decimal(LC[ft]))
            factor = Decimal('1') - (l_cef / lc)
            contrib = ms * factor
            inner += contrib
            detail.append({
                'saf_batch_id': b.get('saf_batch_id'), 'class': cls,
                'mass_t': ms, 'L_CEF': l_cef, 'LC': lc,
                'factor': r(factor, 6), 'inner_t': r(contrib, 4),
            })
        total += fcf * inner
    return {'ER_y_t': r(total, 2), 'detail': detail}


def validate_batches(batches):
    """지침 제6조 적격 요건 사전 점검. 계산 전에 거르는 용도."""
    issues = []
    for b in batches:
        bid = b.get('saf_batch_id')
        if not b.get('certification_scheme'):
            issues.append((bid, '지침 제6조 제2호', '인증제도 미기재'))
        if not b.get('certificate_no'):
            issues.append((bid, '지침 제6조 제2호', '인증서 번호 미기재'))
        if to_dec(b.get('mass_tonnes'), Decimal('0')) <= 0:
            issues.append((bid, 'F-08', '질량이 0 이하'))
        src = (b.get('lca_value_source') or '').upper()
        if src not in ('DEFAULT', 'ACTUAL'):
            issues.append((bid, '지침 별표6 제5호', 'L_CEF 출처가 DEFAULT/ACTUAL이 아님'))
    return issues
