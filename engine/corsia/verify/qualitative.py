"""
정성 검증 (Q) - LLM 판정 영역
근거: verification_rules_v1.md §4

이 계층은 코드가 판정하지 않는다. 코드가 하는 일은 두 가지뿐이다.
  1) 서술형 항목이 비어 있는지 확인 (이건 코드가 할 수 있다)
  2) 비어 있지 않은 서술을 LLM에게 넘길 프롬프트 페이로드로 만들기

실제 판정은 7단계 LLM 파이프라인에서 수행한다. 판정 결과는
{verdict: 충분|보완 필요|판단 불가, reason, cited_clause, missing_points} 형식이다.
'판단 불가'를 반드시 남겨 둔다. LLM이 억지로 고르는 것보다 사람에게 넘기는 편이 안전하다.
"""
from .rules import rule, MAJOR, MINOR, LAYER_QUALITATIVE, SKIPPED, MissingInput

# (규칙 ID, 대상 문서, 항목 키, 평가 질문, 근거)
QUALITATIVE_ITEMS = [
    ('Q-01', 'EMP', 'data_management',
     '원천자료에서 보고서까지 자료 흐름의 각 단계와 담당부서가 식별 가능하게 기술되었는가',
     '지침 별표1 5a'),
    ('Q-02', 'EMP', 'data_gap_procedure',
     '데이터갭 식별 방법과 5% 이하 달성을 위한 평가 시스템·절차가 구체적인가',
     '지침 별표1 5b'),
    ('Q-03', 'EMP', 'route_determination',
     '국제선 / 상쇄의무 대상 국제선 / 모니터링 제외 노선을 구분하는 절차가 SARPs 정의에 부합하는가',
     '지침 별표1 3f~3h'),
    ('Q-04', 'EMP', 'measurement_timing',
     '선택한 방법의 측정 시점·측정장비·자료 기록/전달/저장 절차가 특정 가능한 수준인가',
     '지침 별표1 4.1~4.6'),
    ('Q-05', 'EMP', 'risk_assessment',
     '자료관리시스템의 위험 발생요인이 목록화되고 통제활동과 연결되는가',
     '지침 별표1 5d'),
    ('Q-06', 'ER', 'data_gap_explanation',
     '데이터갭 5% 초과 사유·원인·대체방법 설명이 타당한가',
     '지침 별표4 6-b2'),
    ('Q-07', 'VR', 'verification_opinion',
     '발견사항과 결론 사이에 논리적 연결이 있는가, 3종 검증의견 중 선택이 타당한가',
     'SARPs App.6 3.10·3.11.1(r)'),
    ('Q-08', 'VR', 'findings_and_actions',
     '조치 요구사항과 시정결과가 대응되고 미해결 사항이 명시되었는가',
     'SARPs App.6 3.11.1(n)'),
]

VERDICTS = ('충분', '보완 필요', '판단 불가')


# 조건부 항목 - 해당 상황이 아니면 서술 자체가 불필요하다
CONDITIONS = {
    # Q-06: 데이터갭이 5%를 넘은 경우에만 사유 서술이 요구된다 (지침 별표4 6-b2)
    'Q-06': lambda ctx: ctx.get('data_gap_pct', 0) > 5,
}


def _make_checker(rid, doc, key, question, source):
    def checker(ctx):
        cond = CONDITIONS.get(rid)
        if cond and not cond(ctx):
            return []                      # 요건 미발생 → 서술 불필요
        docs = ctx.get('documents') or {}
        d = docs.get(doc)
        if d is None:
            raise MissingInput(f'documents.{doc}')   # 서류가 없으면 통과가 아니라 SKIPPED
        text = (d.get(key) or '').strip()
        if not text:
            return [{'document': doc, 'item': key, 'issue': '서술 항목이 비어 있음'}]
        return []
    checker.__name__ = f'q_{rid.replace("-", "_")}'
    return checker


for _rid, _doc, _key, _q, _src in QUALITATIVE_ITEMS:
    rule(_rid, LAYER_QUALITATIVE, MAJOR,
         f'[{_doc}] {_q[:40]}...', _src, needs=('documents',))(
        _make_checker(_rid, _doc, _key, _q, _src))


def build_llm_payload(documents):
    """LLM에 넘길 정성 검증 요청 목록을 만든다.
    실제 호출은 7단계 파이프라인의 몫이고, 여기서는 페이로드만 만든다."""
    payload = []
    for rid, doc, key, question, source in QUALITATIVE_ITEMS:
        d = (documents or {}).get(doc)
        if not d:
            continue
        text = (d.get(key) or '').strip()
        if not text:
            continue
        payload.append({
            'rule_id': rid, 'document': doc, 'item': key,
            'question': question, 'cited_clause': source,
            'text': text,
            'expected_response_schema': {
                'rule_id': rid, 'verdict': list(VERDICTS),
                'reason': 'str (2~3문장)', 'cited_clause': source,
                'missing_points': ['str'],
            },
        })
    return payload
