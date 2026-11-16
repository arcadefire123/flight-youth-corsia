"""
정성 검증 AI 지시문 조각 (7단계 E 준비)

기준표 v1.2의 결정 중 AI가 알아야 하는 것을 지시문으로 옮긴다. E 단계에서 항목별 확인 목록
(기준표 Part 2)과 출력 형식을 더해 완성한다. 이 파일은 AI를 부르지 않는다.

  - 입력 칸마다 「[칸 이름: …]」 머리표를 붙여, AI가 근거를 인용할 때 칸 이름을 적을 수 있게 한다
    (기준표 규칙 3·5: 판정 칸 밖에서 찾은 근거는 인용에 칸 이름을 붙인다).
  - 그 방식에서 주지 않는 칸은 지시문에도 입력에도 나오지 않는다(한 칸만 / 함께 줌).
  - 규칙별로 따로 알려야 하는 결정은 RULE_NOTES에 둔다(기준표 v1.3):
    Q-04 C3·C4 측정 변수별(2026-11-13 팀 결정 (가)), Q-07 C4 근거 칸(2026-11-13 팀 회의 (나), 두 칸에 나뉜 경우 합쳐서 인정).
"""
from .rubric import RULES, evidence_fields

FIELD_TITLES = {
    'EMP.data_management': '자료관리 서술 (모니터링 계획서 5a)',
    'EMP.records_retained': '기록·보관 목록 (모니터링 계획서 5c)',
    'EMP.data_gap_procedure': '자료 누락의 기준 (모니터링 계획서 5b)',
    'EMP.measurement_timing': '측정 시점 서술 (모니터링 계획서 4.x)',
    'EMP.monitoring_method': '적용하는 모니터링 방법 (서류의 값)',
    'EMP.route_determination': '노선 결정 절차 (모니터링 계획서 3f~3h)',
    'EMP.risk_assessment': '위험도 설명 (모니터링 계획서 5d)',
    'ER.data_gap_explanation': '데이터갭 5% 초과 내역 (배출량보고서)',
    'ER.data_gap_pct': '코드가 계산한 데이터갭 비율(%)',
    'VR.verification_opinion': '최종 검증의견 (검증보고서)',
    'VR.findings_and_actions': '발견사항과 조치 (검증보고서)',
}

MODES = ('single', 'together')

Q07_C4_NOTE = (
    "Q-07 C4 (의견이 「의견부 만족」일 때만 판정하고, 아니면 「해당 없음」): 요구 내용은 ① 해당 사항(어떤 허위진술·부적합인지), "
    "② 중대하지 않다는 확인, ③ 수정하지 못한 이유입니다. 이 내용은 [VR.verification_opinion]에 있어도, "
    "[VR.findings_and_actions]에만 있어도 「있음」으로 인정합니다. 발견사항 칸에서 찾은 근거는 인용에 칸 이름 "
    "VR.findings_and_actions를 붙입니다. 세 가지가 모두 확인되면 「있음」, 일부만 확인되면 「부족」, "
    "어느 칸에서도 하나도 확인되지 않으면 「없음」입니다."
)

Q04_C3C4_NOTE = (
    "Q-04 C3(측정장비)·C4(기록·전달·수신·저장): 측정 변수가 둘 이상인 방법(예: 방법 B의 탱크 잔량과 급유량)은 "
    "변수마다 측정장비와 기록·전달·저장 절차가 있어야 「있음」입니다. 일부 변수만 지목되면 「부족」입니다. "
    "적용하는 방법이 CERT면 C3·C4는 「해당 없음」입니다."
)

RULE_NOTES = {
    'Q-04': [Q04_C3C4_NOTE],
    'Q-07': [Q07_C4_NOTE],
}

_STATUS_TEXT = (
    "「있음」: 확인할 내용이 있고, 제3자가 그 글만 보고 누가·언제·무엇을·어떻게를 지목할 수 있음. "
    "「부족」: 언급은 있으나 지목할 수 없음. 「없음」: 언급이 없음. "
    "「해당 없음」: 그 서류에 적용되지 않는 항목(결함이 아님). "
    "「판단 불가」: 판정에 필요한 정보가 글과 함께 준 정보 어디에도 없음."
)


def fields_given(rule_id, mode):
    """이 방식에서 AI에게 주는 칸 [(칸 이름, 역할)]. 판정 칸, 함께 주는 정보, (함께 줌이면) 함께 주는 칸 순서."""
    if mode not in MODES:
        raise ValueError(f'모르는 방식: {mode!r} (가능: {MODES})')
    spec = RULES[rule_id]
    out = [(spec['field'], '판정 칸')]
    out += [(f, '함께 주는 정보') for f in spec.get('info', [])]
    if mode == 'together':
        out += [(f, '함께 주는 칸') for f in spec['companions']]
    return out


def _render(value):
    if value is None:
        return '(값 없음)'
    if isinstance(value, list):
        return '\n'.join(f'- {v}' for v in value) if value else '(빈 목록)'
    text = str(value)
    return text if text.strip() else '(빈 칸)'


def build_input_block(rule_id, values, mode):
    """칸마다 머리표를 붙인 입력. values = {칸 이름: 값}. 이 방식에서 주지 않는 칸은 넣지 않는다."""
    parts = []
    for name, role in fields_given(rule_id, mode):
        title = FIELD_TITLES.get(name, name)
        parts.append(f'[칸 이름: {name}] {title} — {role}\n{_render(values.get(name))}')
    return '\n\n'.join(parts)


def build_instructions(rule_id, mode):
    """지시문 조각: 입력 칸 설명, 상태 정의, 근거 인용과 칸 이름 규칙, 규칙별 알아둘 것."""
    given = fields_given(rule_id, mode)
    lines = ['[입력 칸]']
    for name, role in given:
        lines.append(f'- {name}: {FIELD_TITLES.get(name, name)} ({role})')
    if mode == 'together':
        lines.append('「함께 주는 칸」은 원문상 내용이 나뉜 칸입니다. 판정 칸에 없는 내용이 거기 있으면 근거로 인정하되 칸 이름을 붙입니다.')
    else:
        lines.append('위에 적힌 칸만 근거로 씁니다. 입력에 없는 칸의 내용은 짐작하지 않습니다.')
    lines += ['', '[항목별 상태]', _STATUS_TEXT, '',
              '[근거 인용과 칸 이름]',
              '- 「있음」·「부족」에는 근거 문장을 글에서 그대로 짧게 인용하고, 그 문장이 있는 칸 이름을 field에 적습니다.',
              '- 판정 칸 밖에서 찾은 근거는 반드시 그 칸 이름을 적습니다. 코드가 그 칸에서 인용문을 찾아 확인하며, '
              '없으면 그 항목을 「판단 불가」로 바꿉니다.',
              '- 서술이 사실인지(그 시스템이 실제로 있는지)나 다른 서류와 값이 맞는지는 판정하지 않습니다.']
    notes = RULE_NOTES.get(rule_id) or []
    if notes:
        lines += ['', '[이 규칙에서 알아둘 것]'] + [f'- {n}' for n in notes]
    return '\n'.join(lines)
