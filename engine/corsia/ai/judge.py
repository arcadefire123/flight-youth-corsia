"""
정성 검증 AI 판정 (7단계 E1)

한 사례를 AI에게 판정시키고, 응답을 기준표 규칙대로 정리한다. AI 호출은 call_fn으로 받으므로
실제 API(client.call) 대신 모의 함수를 넣어 비용 없이 시험할 수 있다.

순서
  1. 지시문 만들기: 공통 규칙(system) + 규칙별 기준표(rubric_text가 기준표 문서에서 읽음) + prompts의 칸 안내·
     규칙별 결정 + 칸 머리표를 붙인 입력(user)
  2. 응답에서 JSON 찾기: 앞뒤에 설명이나 ``` 가 붙어도 첫 번째 올바른 JSON 객체를 찾는다
  3. 항목 정리 (기준표 공통 규칙 1·3과 결정 사항)
       - 빠진 항목, 모르는 상태 → 판단 불가
       - 「있음」「부족」인데 인용이 없거나, 그 방식에서 주지 않은 칸을 인용했거나, 그 항목의 근거 칸이 아니거나,
         인용문이 적힌 칸에 실제로 없으면 → 판단 불가 (다른 칸에 있었으면 그 사실을 기록)
       - 필수 항목에 해당 없음 → 판단 불가
  4. 최종 판정: rubric.compute_verdict (공통 규칙 2). AI가 쓴 verdict는 쓰지 않는다
  5. 시험 세트 정답과 비교(있을 때)
"""
import json
import re

from .rubric import RULES, STATUSES, compute_verdict, evidence_fields
from .prompts import build_instructions, build_input_block, fields_given, FIELD_TITLES, RULE_NOTES

try:
    from corsia.verify.qualitative import QUALITATIVE_ITEMS
    _QUESTIONS = {r[0]: r[3] for r in QUALITATIVE_ITEMS}
except Exception:            # 엔진 없이 쓸 때
    _QUESTIONS = {}

SYSTEM_TEXT = (
    "당신은 국제항공 탄소상쇄제도(CORSIA) 제출 서류의 서술을 팀이 확정한 기준표로 판정하는 검토 보조자입니다.\n"
    "- 항목(C1, C2 …)마다 상태를 하나만 고릅니다. 최종 판정(충분·보완 필요·판단 불가)은 코드가 계산하므로 쓰지 않습니다.\n"
    "- 근거는 입력 글에서 그대로 짧게 인용합니다. 고쳐 쓰거나 요약하지 않습니다.\n"
    "- 답은 아래 형식의 JSON 객체 하나만 씁니다. 그 밖의 설명이나 글은 쓰지 않습니다.\n\n"
    '{"rule_id": "규칙 ID", "checks": [{"id": "C1", "status": "있음|부족|없음|해당 없음|판단 불가", '
    '"evidence": "있음·부족일 때 입력 글에서 그대로 옮긴 인용", "field": "인용한 칸 이름", '
    '"note": "부족·판단 불가일 때 이유 한 문장"}], '
    '"missing_points": ["보완할 점. 필수 항목 먼저, 권장 항목은 앞에 \'권장:\'"], '
    '"reason": "2~3문장", "cited_clause": "근거 조항"}'
)

_STATUS_ALIASES = {'해당없음': '해당 없음', '판단불가': '판단 불가', '보완필요': '보완 필요'}


def build_messages(rule_id, values, mode, rubric):
    """(system, user). rubric = rubric_text.load_rubric() 결과."""
    sec = rubric['rules'][rule_id]
    question = sec['question'] or _QUESTIONS.get(rule_id, '')
    lines = [f'[규칙] {rule_id}. {sec["title"]}']
    if question:
        lines.append(f'판정 질문: {question}')
    lines += ['', '[확인할 항목] (구분: 필수·조건부는 최종 판정에 쓰이고, 권장은 참고)']
    for cid, ch in sec['checks'].items():
        lines.append(f'- {cid} ({ch["kind"]}) {ch["text"]} — 근거: {ch["basis"]}')
    for tb in sec['tables']:
        lines += ['', tb]
    if sec['guide']:
        lines += ['', '[판정할 때 알아둘 것]'] + [f'- {g}' for g in sec['guide']]
    lines += ['', build_instructions(rule_id, mode), '', '[입력]', build_input_block(rule_id, values, mode), '',
              f'위 형식의 JSON 하나만 답하세요. checks에는 {", ".join(sec["checks"])} 모든 항목을 넣습니다.']
    return SYSTEM_TEXT, '\n'.join(lines)


def extract_json(text):
    """응답 글에서 checks가 있는 첫 JSON 객체를 찾는다. (객체, 오류 문구)"""
    if not text or not text.strip():
        return None, '빈 응답'
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{', text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('checks'), list):
            return obj, None
    return None, 'JSON을 찾지 못함'


def _norm(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _text_of(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return '\n'.join(str(v) for v in value)
    return str(value)


_LOOSE_DROP = re.compile(r'[\s,.·/\-–—"\'「」『』“”‘’()\[\]:;!?]+')


def _loose(s):
    """공백·구두점을 모두 뺀 글자 열(목록 항목을 쉼표로 이어 인용한 경우 등을 확인하려는 느슨한 비교용)."""
    return _LOOSE_DROP.sub('', str(s or ''))


def _match_parts(parts, hay, hay_l):
    how = 'exact'
    for p in parts:
        p = p.strip().strip('"\'「」『』“”‘’').rstrip('.').strip()
        if len(p) >= 2 and p in hay:
            continue
        pl = _loose(p)
        if len(pl) >= 4 and pl in hay_l:
            how = 'loose'
            continue
        return None
    return how


def quote_match(evidence, value):
    """인용문이 그 칸 글에 있는지. 'exact'(공백 차이와 앞뒤 따옴표·마침표만 무시) | 'loose'(구두점까지 무시) |
    'pieced'(문장 여러 개를 이어 붙인 인용: 문장마다 그 칸에 그대로 있음) | None.
    「…」·「 / 」로 이어 붙인 인용은 조각마다 확인한다(기준표 규칙 3, v1.5). 중간 문장을 건너뛰고 이어 붙인 인용은
    문장마다 확인한다(v1.6). 한 문장이라도 그 칸에 없으면 None."""
    text = _text_of(value)
    hay, hay_l = _norm(text), _loose(text)
    ev = _norm(evidence)
    parts = [p for p in re.split(r'…|\.\.\.|\s/\s|\s\|\s', ev) if p.strip()]
    if not parts or not hay:
        return None
    how = _match_parts(parts, hay, hay_l)
    if how:
        return how
    sents = [x.strip() for x in re.split(r'(?<=[.!?])\s+', ev) if x.strip()]
    if len(sents) >= 2 and all(len(_loose(x)) >= 6 for x in sents) and _match_parts(sents, hay, hay_l):
        return 'pieced'
    return None


def quote_found(evidence, value):
    return quote_match(evidence, value) is not None


def _norm_status(s):
    t = str(s or '').strip().strip('「」"\'')
    return _STATUS_ALIASES.get(t.replace(' ', ''), t) if t.replace(' ', '') in _STATUS_ALIASES else t


def _norm_field(f):
    if not f:
        return None
    t = str(f).strip().strip('[]` ')
    t = re.sub(r'^칸 이름\s*:\s*', '', t).strip()
    if t in FIELD_TITLES:
        return t
    for name, title in FIELD_TITLES.items():      # 사람 이름으로 적은 경우
        if t and (t == title or t == title.split(' (')[0]):
            return name
    return t


def evaluate(rule_id, obj, values, mode):
    """AI 응답(JSON 객체)을 기준표 규칙대로 정리하고 최종 판정을 계산한다."""
    spec = RULES[rule_id]
    given = [n for n, _ in fields_given(rule_id, mode)]
    raw = {}
    for ch in obj.get('checks') or []:
        if isinstance(ch, dict) and ch.get('id') in spec['checks'] and ch['id'] not in raw:
            raw[ch['id']] = ch
    items, flags = {}, []
    for cid, kind in spec['checks'].items():
        ch = raw.get(cid)
        if ch is None:
            items[cid] = {'status': '판단 불가', 'ai_status': None, 'flag': '항목 누락'}
            flags.append(f'{cid}: 항목 누락')
            continue
        st = _norm_status(ch.get('status'))
        rec = {'status': st, 'ai_status': st}
        if ch.get('note'):
            rec['note'] = ch['note']
        if st not in STATUSES:
            rec.update(status='판단 불가', flag=f'모르는 상태 {ch.get("status")!r}')
        elif st in ('있음', '부족'):
            ev = ch.get('evidence')
            if isinstance(ev, list):
                ev = ' … '.join(str(x) for x in ev)
            fld = _norm_field(ch.get('field')) or spec['field']
            allowed = evidence_fields(rule_id, cid)
            rec.update(evidence=ev, field=fld)
            problem = None
            if not ev or not str(ev).strip():
                if st == '부족':          # 확인할 언급이 없다는 뜻 → 없음 (규칙 3, v1.5). 최종 판정은 같은 결과
                    rec.update(status='없음', flag='「부족」인데 인용이 없어 「없음」으로 처리')
                    flags.append(f'{cid}: {rec["flag"]}')
                    items[cid] = rec
                    continue
                problem = '인용 없음'
            elif fld not in given:
                problem = f'주지 않은 칸 인용({fld})'
            elif allowed and fld not in allowed:
                problem = f'이 항목의 근거 칸이 아님({fld})'
            elif quote_match(ev, values.get(fld)) is None:
                other = [f for f in given if f != fld and quote_match(ev, values.get(f))]
                problem = f'인용문이 {fld}에 없음' + (f' (다른 칸 {", ".join(other)}에 있음: 칸 이름 누락·잘못)' if other else '')
            elif quote_match(ev, values.get(fld)) in ('loose', 'pieced'):
                rec['quote_loose' if quote_match(ev, values.get(fld)) == 'loose' else 'quote_pieced'] = True
            if problem:
                rec.update(status='판단 불가', flag=problem)
        elif st == '해당 없음' and kind == '필수':
            rec.update(status='판단 불가', flag='필수 항목에 해당 없음')
        if rec.get('flag'):
            flags.append(f'{cid}: {rec["flag"]}')
        items[cid] = rec
    verdict = compute_verdict(rule_id, {k: v['status'] for k, v in items.items()})
    return {'items': items, 'verdict': verdict, 'flags': flags,
            'missing_points': obj.get('missing_points'), 'reason': obj.get('reason'),
            'cited_clause': obj.get('cited_clause')}


def skip_reason(case_or_rule, values):
    """AI를 부르지 않는 경우 (Q-06: 데이터갭 5% 이하면 규칙 자체를 판정하지 않음)."""
    rule_id = case_or_rule if isinstance(case_or_rule, str) else case_or_rule['rule_id']
    if rule_id == 'Q-06':
        pct = values.get('ER.data_gap_pct')
        if pct is not None and float(pct) <= 5:
            return f'데이터갭 {pct}% ≤ 5% (조건 미발생)'
    return None


def compare(result, expected):
    """판정 결과와 정답 비교. expected = case['expected'][mode]"""
    spec_items = expected['checks']
    rows = {}
    for cid, x in spec_items.items():
        got = result['items'][cid]
        rows[cid] = {'expected': x['status'], 'got': got['status'], 'ai_status': got.get('ai_status'),
                     'match': got['status'] == x['status']}
    return {'verdict_expected': expected['verdict'], 'verdict_match': result['verdict'] == expected['verdict'],
            'items': rows}


def judge_case(case, mode, call_fn, rubric, model_key='sonnet', thinking=None, effort=None, max_tokens=4000):
    """시험 세트 사례 하나를 한 방식으로 판정. call_fn(user, model_key=, system=, max_tokens=, thinking=, effort=)
    → client.CallResult 같은 객체(text, cost, stop_reason, truncated)."""
    values = {**case['inputs'], **(case.get('companions') or {})}
    rec = {'case_id': case.get('case_id'), 'rule_id': case['rule_id'], 'group': case.get('group'), 'mode': mode,
           'model': model_key, 'thinking': thinking or '기본', 'effort': effort or '기본'}
    why = skip_reason(case, values)
    if why:
        rec.update(skipped=why)
        return rec
    system, user = build_messages(case['rule_id'], values, mode, rubric)
    rec['prompt_chars'] = len(system) + len(user)
    r = call_fn(user, model_key=model_key, system=system, max_tokens=max_tokens, thinking=thinking, effort=effort)
    rec.update(raw_text=r.text, stop_reason=getattr(r, 'stop_reason', ''), truncated=bool(getattr(r, 'truncated', False)),
               cost=r.cost, request=getattr(r, 'settings', None))
    obj, err = extract_json(r.text)
    if obj is None:
        rec.update(json_ok=False, json_error=err, verdict=None)
        return rec
    ev = evaluate(case['rule_id'], obj, values, mode)
    rec.update(json_ok=True, verdict=ev['verdict'], items=ev['items'], flags=ev['flags'],
               missing_points=ev['missing_points'], reason=ev['reason'], cited_clause=ev['cited_clause'])
    if case.get('expected'):
        rec['compare'] = compare(ev, case['expected'][mode])
    return rec


__all__ = ['SYSTEM_TEXT', 'RULE_NOTES', 'build_messages', 'extract_json', 'quote_found', 'evaluate',
           'skip_reason', 'compare', 'judge_case']
