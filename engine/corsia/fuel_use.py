"""
C-01 ~ C-05 · 연료 사용량 모니터링 5방법
근거: SARPs App.2 / ETM 3.1.4 / 지침 별표2
출력 단위: 톤(t). 모든 항공편은 시간순 정렬을 전제로 한다.
"""
from decimal import Decimal
from .constants import DENSITY_STD, r, to_dec

__all__ = ['volume_to_mass', 'method_a', 'method_b', 'block_off_on',
           'fuel_uplift_group', 'afbr', 'block_hour_alloc',
           'compute_sequence', 'METHODS']

METHODS = ('METHOD_A', 'METHOD_B', 'BLOCK_OFF_ON', 'FUEL_UPLIFT', 'BLOCK_HOUR')


def volume_to_mass(volume_l, density_kg_l=None):
    """부피(L) → 질량(t). 밀도 미제공 시 표준밀도 0.8 kg/L (지침 제3조④)"""
    if volume_l in (None, ''):
        return None
    d = to_dec(density_kg_l, DENSITY_STD)
    return r(Decimal(str(volume_l)) * d / Decimal('1000'), 1)


# ── C-01 방법 A ─────────────────────────────────────────────────────
def method_a(T_N, T_next, U_next):
    """F_N = T_N - T_(N+1) + U_(N+1)

    T_N      : 항공편 N 급유 후 탱크 연료량. 급유가 없으면 N의 블록오프 연료량
    T_next   : 후속편 N+1 급유 후 탱크 연료량. 급유가 없으면 N+1의 블록오프 연료량
    U_next   : 후속편 N+1 급유량 (없으면 0)
    """
    T_N, T_next = to_dec(T_N), to_dec(T_next)
    U_next = to_dec(U_next, Decimal('0'))
    if T_N is None or T_next is None:
        return None
    return r(T_N - T_next + U_next, 1)


# ── C-02 방법 B ─────────────────────────────────────────────────────
def method_b(R_prev, R_N, U_N):
    """F_N = R_(N-1) - R_N + U_N"""
    R_prev, R_N = to_dec(R_prev), to_dec(R_N)
    U_N = to_dec(U_N, Decimal('0'))
    if R_prev is None or R_N is None:
        return None
    return r(R_prev - R_N + U_N, 1)


# ── C-03 Block-off / Block-on ───────────────────────────────────────
def block_off_on(T_N, R_N):
    """F_N = T_N - R_N"""
    T_N, R_N = to_dec(T_N), to_dec(R_N)
    if T_N is None or R_N is None:
        return None
    return r(T_N - R_N, 1)


# ── C-04 연료 급유량 ────────────────────────────────────────────────
def fuel_uplift_group(uplift_t, block_hours):
    """하나의 배분 구간에 대한 연료 배분.

    block_hours : [BH_N, BH_(N+1), ..., BH_(N+n)]  - 급유편 N 자신을 포함한다
    반환        : [F_N, F_(N+1), ..., F_(N+n)]

    급유편 N 자신도 배분 대상이다. 구간이 1편뿐이면 비율 1이 되어 F_N = U_N.
    """
    U = to_dec(uplift_t, Decimal('0'))
    bhs = [to_dec(b, Decimal('0')) for b in block_hours]
    total = sum(bhs)
    if total == 0:
        raise ValueError('블록타임 합계가 0입니다. 배분 불가 (C-04 가드)')
    return [r(U * bh / total, 1) for bh in bhs]


# ── C-05 Block Hour 연료할당 ────────────────────────────────────────
def afbr(sum_uplift_t, sum_block_hours):
    """AFBR = ΣU / ΣBH, 소수점 셋째 자리 반올림 (지침 별표2 제5호 2) 주3)"""
    su, sb = to_dec(sum_uplift_t), to_dec(sum_block_hours)
    if not sb:
        raise ValueError('블록타임 합계가 0입니다 (C-05 가드)')
    return r(su / sb, 3)


def block_hour_alloc(afbr_value, block_hours_n):
    """F_N = AFBR × BH_N"""
    return r(to_dec(afbr_value) * to_dec(block_hours_n), 1)


# ── 시퀀스 단위 계산 ────────────────────────────────────────────────
def _tank_for_method_a(f):
    """급유가 있으면 급유 후 탱크, 없으면 블록오프 연료량 (지침 별표2 제1호)"""
    if f.get('uplift_tonnes') not in (None, ''):
        return f.get('tank_after_uplift_t')
    return f.get('tank_block_off_t')


def compute_sequence(flights, method):
    """항공기 1대의 시간순 항공편 목록에 대해 편별 연료 사용량을 계산한다.

    flights : dict 리스트. 필요한 키는 method에 따라 다르다.
    반환    : flights와 같은 길이의 리스트. 계산 불가한 편은 None.
    """
    if method not in METHODS:
        raise ValueError(f'알 수 없는 방법: {method}')
    n = len(flights)
    out = [None] * n

    if method == 'BLOCK_OFF_ON':
        for i, f in enumerate(flights):
            out[i] = block_off_on(f.get('tank_block_off_t'), f.get('tank_block_on_t'))

    elif method == 'METHOD_A':
        for i, f in enumerate(flights):
            if i + 1 >= n:
                break  # 마지막 편은 후속편 자료가 없어 계산 불가
            nxt = flights[i + 1]
            out[i] = method_a(_tank_for_method_a(f), _tank_for_method_a(nxt),
                              nxt.get('uplift_tonnes'))

    elif method == 'METHOD_B':
        for i, f in enumerate(flights):
            if i == 0:
                continue  # 직전편 자료 없음
            out[i] = method_b(flights[i - 1].get('tank_block_on_t'),
                              f.get('tank_block_on_t'), f.get('uplift_tonnes'))

    elif method == 'FUEL_UPLIFT':
        i = 0
        while i < n:
            if flights[i].get('uplift_tonnes') in (None, ''):
                i += 1
                continue
            j = i + 1
            while j < n and flights[j].get('uplift_tonnes') in (None, ''):
                j += 1
            grp = flights[i:j]
            try:
                vals = fuel_uplift_group(flights[i]['uplift_tonnes'],
                                         [g.get('block_hours') for g in grp])
                for k, v in enumerate(vals):
                    out[i + k] = v
            except ValueError:
                pass
            i = j

    elif method == 'BLOCK_HOUR':
        su = sum((to_dec(f.get('uplift_tonnes'), Decimal('0')) for f in flights),
                 Decimal('0'))
        sb = sum((to_dec(f.get('block_hours'), Decimal('0')) for f in flights),
                 Decimal('0'))
        a = afbr(su, sb)
        for i, f in enumerate(flights):
            out[i] = block_hour_alloc(a, f.get('block_hours'))

    return out
