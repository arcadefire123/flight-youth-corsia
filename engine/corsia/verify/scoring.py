"""
종합점수 (보조 지표)

검증의견 3종(만족/의견부 만족/불만족)은 ICAO 기준(SARPs App.6 3.10)이므로 그대로 두고,
화면에서 상태를 한눈에 보기 위한 보조 지표로 점수를 붙인다.
점수는 어떤 경우에도 판정을 대체하지 않는다.

방식: 심각도 가중 감점 + 판정별 상한
근거: verification_rules_v1.md §5.1 · 9_web_design/web_design_v1.md §2

    계층 점수 = 100 × (1 − 해당 계층에서 잃은 배점 ÷ 해당 계층 총 배점)   ← 보정 없음
    원점수    = 100 × (1 − 전체 잃은 배점 ÷ 전체 총 배점)
    전체 점수 = min(원점수, 판정별 상한)

- SKIPPED 규칙은 분모에서 제외한다 (판정 보류를 감점으로도 가점으로도 쓰지 않는다).
- 규칙 단위로 1회만 계산한다. 위반 건수는 점수에 반영하지 않는다.
  (X-19는 1,102건까지 나와, 건수를 넣으면 한 규칙이 점수를 지배한다.)
- 상한은 전체 점수에만 적용한다. 계층별 점수는 원점수 그대로 둔다.

주의: 판정별 상한 구간(100/89/59)은 ICAO 기준이 아니라 **팀이 정한 보조 기준**이다.
"""
from decimal import Decimal, ROUND_HALF_UP

from ..constants import (OPINION_SATISFACTORY, OPINION_WITH_COMMENTS,
                         OPINION_UNSATISFACTORY)
from .rules import CRITICAL, MAJOR, MINOR, BLOCKING, INFO, FAIL, SKIPPED

# 심각도별 배점
WEIGHT = {
    CRITICAL: Decimal('5'),
    BLOCKING: Decimal('5'),
    MAJOR: Decimal('2'),
    MINOR: Decimal('1'),
    INFO: Decimal('0'),
}

# 판정별 상한 (팀 자체 보조 기준 - ICAO 기준 아님)
OPINION_CAP = {
    OPINION_SATISFACTORY: Decimal('100'),
    OPINION_WITH_COMMENTS: Decimal('89'),
    OPINION_UNSATISFACTORY: Decimal('59'),
}

CAP_BASIS = '팀 자체 보조 기준 (ICAO 기준 아님)'
LAYER_ORDER = ['형식', '임계값', '일관성', '정성']


def _round1(v):
    return Decimal(v).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def _score(total, lost):
    """잃은 배점 비율을 100점으로 환산. 총 배점이 0이면 점수 없음(None)."""
    if total <= 0:
        return None
    return _round1(Decimal('100') * (Decimal('1') - lost / total))


def compute_score(findings, opinion):
    """findings(dict 또는 Finding)와 검증의견으로 종합점수를 낸다.

    돌려주는 것
      overall      : 상한 적용 후 전체 점수
      raw          : 상한 적용 전 원점수
      capped       : 상한이 실제로 걸렸는가
      cap / cap_reason / cap_basis
      by_layer     : 계층별 점수 (보정 없음) + 산식 재료
      deductions   : 규칙별 감점 근거
    """
    rows = [f if isinstance(f, dict) else f.as_dict() for f in findings]
    scored = [f for f in rows if f['status'] != SKIPPED]

    total = sum((WEIGHT.get(f['severity'], Decimal('0')) for f in scored), Decimal('0'))
    lost = sum((WEIGHT.get(f['severity'], Decimal('0'))
                for f in scored if f['status'] == FAIL), Decimal('0'))

    # 계층별 (상한 보정 없음)
    by_layer = {}
    layers = [L for L in LAYER_ORDER if any(f['layer'] == L for f in rows)]
    layers += sorted({f['layer'] for f in rows} - set(layers))
    for L in layers:
        g = [f for f in scored if f['layer'] == L]
        t = sum((WEIGHT.get(f['severity'], Decimal('0')) for f in g), Decimal('0'))
        l = sum((WEIGHT.get(f['severity'], Decimal('0'))
                 for f in g if f['status'] == FAIL), Decimal('0'))
        by_layer[L] = {
            'score': _score(t, l),
            'weight_total': t, 'weight_lost': l,
            'rules': len(g), 'failed': sum(1 for f in g if f['status'] == FAIL),
            'skipped': sum(1 for f in rows if f['layer'] == L and f['status'] == SKIPPED),
            'formula': f'100 × (1 − {l}/{t})' if t else '배점 없음',
        }

    raw = _score(total, lost)

    # 규칙별 감점 근거 (심각도 → ID 순)
    sev_order = {CRITICAL: 0, BLOCKING: 0, MAJOR: 1, MINOR: 2, INFO: 3}
    deductions = []
    for f in sorted([x for x in scored if x['status'] == FAIL],
                    key=lambda x: (sev_order.get(x['severity'], 9), x['rule_id'])):
        w = WEIGHT.get(f['severity'], Decimal('0'))
        deductions.append({
            'rule_id': f['rule_id'], 'layer': f['layer'], 'severity': f['severity'],
            'weight': w,
            'points': _round1(Decimal('100') * w / total) if total else Decimal('0'),
            'basis': f'{f["severity"]} · 배점 {w} / 전체 {total}',
        })

    cap = OPINION_CAP.get(opinion, Decimal('100'))
    if raw is None:
        overall, capped = None, False
    elif raw > cap:
        overall, capped = cap.quantize(Decimal('0.1')), True
    else:
        overall, capped = raw, False

    if capped:
        if opinion == OPINION_UNSATISFACTORY:
            reason = f'치명 위반으로 상한 적용 (원점수 {raw})'
        else:
            reason = f'위반 있음으로 상한 적용 (원점수 {raw})'
    else:
        reason = None

    return {
        'method': '심각도 가중 감점 + 판정별 상한',
        'overall': overall,
        'raw': raw,
        'capped': capped,
        'cap': cap,
        'cap_reason': reason,
        'cap_basis': CAP_BASIS,
        'weight_total': total,
        'weight_lost': lost,
        'weights': {k: v for k, v in WEIGHT.items() if k != BLOCKING},
        'scored_rules': len(scored),
        'skipped_rules': len(rows) - len(scored),
        'by_layer': by_layer,
        'deductions': deductions,
    }
