"""
정성 검증 기준표 v1.5 (확정 2026-11-08, 개정 2026-11-13 v1.1~v1.5)의 체크 항목과 최종 판정 계산 (7단계)

원문: 7_qualitative/qualitative_rubric_v1.md
  - AI는 체크 항목마다 상태(있음·부족·없음·해당 없음·판단 불가)만 답한다.
  - 최종 판정(충분·보완 필요·판단 불가)은 이 파일의 compute_verdict()가 공통 규칙 2로 계산한다.
  - 권장 항목은 최종 판정에 쓰지 않는다.
기준표를 고치면 이 파일의 RULES도 같이 고친다.
"""

STATUSES = ('있음', '부족', '없음', '해당 없음', '판단 불가')
VERDICTS = ('충분', '보완 필요', '판단 불가')

# 구분: '필수' | '조건부' | '권장'
#   조건부 = 조건이 맞으면 필수, 아니면 '해당 없음'
RULES = {
    'Q-01': {'field': 'EMP.data_management', 'companions': ['EMP.records_retained'],
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '필수', 'C4': '필수', 'C5': '권장', 'C6': '권장'}},
    'Q-02': {'field': 'EMP.data_gap_procedure', 'companions': ['EMP.measurement_timing'],
             'info': ['EMP.monitoring_method'],       # v1.5: 방법에 따라 C3 요구가 달라짐 (지침 별표1 5b b2))
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '필수', 'C4': '필수', 'C5': '권장', 'C6': '권장'}},
    'Q-03': {'field': 'EMP.route_determination', 'companions': [],
             'checks': {'C1': '필수', 'C2': '권장', 'C3': '필수', 'C4': '권장', 'C5': '필수', 'C6': '권장'}},
    'Q-04': {'field': 'EMP.measurement_timing', 'companions': ['EMP.data_management'],
             'info': ['EMP.monitoring_method'],
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '조건부', 'C4': '조건부', 'C5': '조건부', 'C6': '권장'}},
    'Q-05': {'field': 'EMP.risk_assessment', 'companions': ['EMP.data_management'],
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '권장', 'C4': '권장', 'C5': '권장'}},
    'Q-06': {'field': 'ER.data_gap_explanation', 'companions': [],
             'info': ['ER.data_gap_pct'], 'condition': 'ER.data_gap_pct > 5',
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '필수', 'C4': '권장', 'C5': '권장'}},
    'Q-07': {'field': 'VR.verification_opinion', 'companions': [],
             'info': ['VR.findings_and_actions'],   # 기존 예외: 두 방식 모두 함께 읽음
             'checks': {'C1': '필수', 'C2': '필수', 'C3': '필수', 'C4': '조건부', 'C5': '권장', 'C6': '권장'}},
    'Q-08': {'field': 'VR.findings_and_actions', 'companions': [],
             'checks': {'C1': '필수', 'C2': '조건부', 'C3': '필수', 'C4': '권장', 'C5': '권장'}},  # C2: v1.1
}

# 항목별로 근거를 찾을 수 있는 칸을 따로 정한 경우 (정하지 않은 항목은 판정 칸과 함께 주는 칸·정보 어디서든)
#   Q-07 C4 (v1.2, 팀 회의 결정 (나)): 의견 칸에 있어도, 발견사항 칸에만 있어도 「있음」. 인용에 칸 이름을 붙인다
EVIDENCE_FIELDS = {
    ('Q-07', 'C4'): ['VR.verification_opinion', 'VR.findings_and_actions'],
}


def evidence_fields(rule_id, check_id):
    """이 항목의 근거를 찾을 수 있는 칸 목록. 따로 정하지 않았으면 None."""
    return EVIDENCE_FIELDS.get((rule_id, check_id))


# 측정 변수가 둘 이상이면 변수마다 있어야 「있음」인 항목 (v1.3, 팀 결정 (가)). 일부 변수만 지목되면 「부족」
#   예: 방법 B의 탱크 잔량(FQIS)과 급유량(급유 차량 계량기). 판정 계산에는 영향 없음(AI 지시문과 정답표 작성 기준)
PER_VARIABLE_CHECKS = {('Q-04', 'C3'), ('Q-04', 'C4')}


# Q-04: 방법별로 조건부 항목이 해당하는지 (기준표 Q-04 방법별 표)
DENSITY_METHODS = ('METHOD_A', 'METHOD_B', 'FUEL_UPLIFT', 'BLOCK_HOUR')   # C5
NO_DEVICE_METHODS = ('CERT',)                                            # C3·C4 해당 없음


def conditional_applies(rule_id, check_id, method=None, opinion=None, has_findings=None):
    """조건부 항목이 이 서류에 해당하는지. 모르면 None(판단 불가 후보)."""
    if rule_id == 'Q-04':
        if method is None:
            return None
        if check_id == 'C5':
            return method in DENSITY_METHODS
        if check_id in ('C3', 'C4'):
            return method not in NO_DEVICE_METHODS
    if rule_id == 'Q-07' and check_id == 'C4':
        if opinion is None:
            return None
        return opinion == '의견부 만족'
    if rule_id == 'Q-08' and check_id == 'C2':      # v1.1: 발견사항이 하나도 없으면 해당 없음
        return has_findings
    return True


def compute_verdict(rule_id, statuses):
    """공통 규칙 2. statuses = {'C1': '있음', ...}. 권장 항목은 보지 않는다."""
    kinds = RULES[rule_id]['checks']
    unknown = [c for c in statuses if c not in kinds]
    if unknown:
        raise ValueError(f'{rule_id}에 없는 항목: {unknown}')
    bad = [s for s in statuses.values() if s not in STATUSES]
    if bad:
        raise ValueError(f'알 수 없는 상태: {bad}')
    core = {c: statuses.get(c) for c, k in kinds.items() if k in ('필수', '조건부')}
    missing = [c for c, s in core.items() if s is None]
    if missing:
        raise ValueError(f'{rule_id} 필수·조건부 항목 상태 없음: {missing}')
    for c, s in core.items():
        if kinds[c] == '필수' and s == '해당 없음':
            raise ValueError(f'{rule_id} {c}는 필수라 「해당 없음」이 될 수 없음')
    if any(s in ('없음', '부족') for s in core.values()):
        return '보완 필요'
    if any(s == '판단 불가' for s in core.values()):
        return '판단 불가'
    return '충분'
