"""분류기 프롬프트: 참고 카드 + 견본 5건 + 항목 묶음 (8단계)."""
import json
import re

from . import catalog as C
from . import rules as R

SYSTEM = ('당신은 CORSIA 서류(ICAO 템플릿과 국내 별지 서식) 항목의 성격을 판정 기준에 따라 채우는 보조입니다. '
          '주어진 정보와 판정 기준만 쓰고, 기준에 없으면 「기준 없음」이라고 쓰며, 추측해서 지어내지 않습니다. '
          '답은 JSON 하나만 쓰고 앞뒤에 설명을 붙이지 않습니다.')

VARIANT_AI, VARIANT_HYBRID = 'ai', 'hybrid'


def reference_card():
    """판정 기준 카드 + 템플릿 상수표(template_rules_v1.json에서 생성)"""
    text = (C.CLASSIFIER_DIR / 'criteria_card_v1.md').read_text(encoding='utf-8').strip()
    rules = C.template_rules()
    lines = ['', '## 템플릿별 상수표', '',
             '| 템플릿 | 서류 | 주체 | 단계 | 시점·기한 | 국내 서식 | 별표 | 지침 조문 | 법령 |', '|---|---|---|---|---|---|---|---|---|']
    for t in ('1.1 EMP', '1.2 ER', '1.3 CEF', '1.5 EUCR', '1.6 VR', '1.4 OoM'):
        r = rules[t]
        d = r.get('국내') or {}
        dl = r.get('시점·기한') or C.NO_RULE
        if t == '1.6 VR':
            dl = f"{r['시점·기한']} (배출량 검증보고서) / {r['시점·기한_이행']} (배출권 취소 보고서 검증)"
        lines.append(f"| {t} | {r['한글명']} | {r.get('주체') or C.NO_RULE} | {r.get('단계') or C.NO_RULE} | {dl} | "
                     f"{d.get('서식', '없음')} | {d.get('별표', '-')} | {d.get('지침조문', '-')} | {' / '.join(d.get('법령', [])) or '-'} |")
    lines += ['', '## 근거 문자열 만드는 법 (국내 서식이 있는 칸)',
              '「지침 {국내 서식} {섹션}.{항목번호} / 지침 {별표} {섹션}.{항목번호} / {지침 조문} / {법령들}」 — 예: 「지침 별지 제2호서식 3.a) / 지침 별표 4 3.a) / 지침 제5조제1항 / 법 제8조제4항 / 시행규칙 제7조」',
              '', '## VR(검증보고서) 근거에 쓸 ICAO 절 색인']
    lines += [f'- {x}' for x in rules['1.6 VR']['ICAO_색인']]
    ex = C.card_examples()
    if ex:                                                   # OoM 작성 예(골든셋 항목은 채점에서 자동 제외)
        by = C.context()['by_id']
        lines += ['', '## OoM 문항 작성 예 (팀이 쓴 값)']
        for e in ex:
            it = by[e['id']]
            name = re.sub(r'\s+', ' ', str(it.get('항목명') or ''))[:80]
            lines.append(f"- OoM {str(it.get('항목번호') or '').strip()} 「{name}」 → 연결필드 {e['연결필드']}"
                         + (f" ({e['연결_이유']})" if e.get('연결_이유') else '') + f" · 의무성 {e['의무성']}"
                         + (f" ({e['의무성_이유']})" if e.get('의무성_이유') else ''))
    return text + '\n' + '\n'.join(lines)


def _short(text, n=260):
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    return t if len(t) <= n else t[:n] + '…'


def item_payload(item_id, variant, rule=None, with_candidates=True):
    ctx = C.context()
    it = ctx['by_id'][item_id]
    K = ctx['K']
    L = [f"[{item_id}] 템플릿 {it['템플릿']} · 시트 {it['시트']} · 섹션 {_short(it.get('섹션'), 60) or '-'} · 항목번호 {it.get('항목번호') or '-'} · 유형 {it['유형']}",
         f"  항목명: {_short(it.get('항목명'), 200)}"]
    if it.get('세부라벨'):
        L.append(f"  세부라벨: {_short(it['세부라벨'], 160)}")
    if it.get('원문 설명'):
        L.append(f"  원문 설명: {_short(it['원문 설명'])}")
    if it.get('국내 서식'):
        k = C.domestic_row(it, K)
        L.append(f"  국내 서식 대응: {it['국내 서식']} {it.get('국내 항목번호') or ''} {_short(it.get('국내 항목명'), 120)}"
                 + (f" (작성방법: {_short(k.get('작성방법(별표) 설명'), 160)})" if k and k.get('작성방법(별표) 설명') else ''))
    else:
        L.append('  국내 서식 대응: 없음')
    p = C.parent_of(it, ctx['by_key'])
    if p:
        L.append(f"  상위 항목({p.get('항목번호')}): {_short(p.get('항목명'), 140)}" + (f" — {_short(p.get('국내 항목명'), 100)}" if p.get('국내 항목명') else ''))
    if with_candidates:
        cands = C.candidates(item_id)
        if cands:
            L.append('  연결 후보(다른 템플릿의 비슷한 칸): ' + ' | '.join(f"{C.link_label(c)} {_short(c.get('항목명'), 50)}" for c, _ in cands))
        else:
            L.append('  연결 후보: 없음')
    if variant == VARIANT_HYBRID and rule is not None:
        fixed = {a: d['value'] for a, d in rule.items() if d['fixed']}
        L.append('  코드가 정한 값(확정, 바꾸지 마세요): ' + (', '.join(f'{a}={v}' for a, v in fixed.items()) or '없음'))
        hints = [f"{a}: 잠정 {d['value']} ({d['why']})" for a, d in rule.items() if not d['fixed'] and a in ('의무성', '입력유형', '시점·기한', '근거')]
        L.append('  코드의 잠정 판단(참고): ' + ' / '.join(hints))
        cell = C.template_cells().get(item_id)
        if cell:
            L.append(f"  원본 칸 모양(코드가 읽음): 선택 목록 {'있음' if cell['dropdown'] else '없음'}, 수식 {'있음' if cell['formula'] else '없음'}, 병합 칸 {cell['merged_w']}×{cell['merged_h']}")
    return '\n'.join(L)


def unfixed_attrs(rule):
    return [a for a in C.ATTRS if not rule[a]['fixed']]


def examples_block():
    gold = C.gold_of_examples()
    L = ['## 견본 5건 (팀이 확정한 정답. 채점에 쓰지 않는 예시)']
    for iid in C.EXAMPLE_IDS:
        L.append(item_payload(iid, VARIANT_AI, with_candidates=False))
        L.append('  정답: ' + json.dumps(gold[iid], ensure_ascii=False))
    return '\n'.join(L)


def output_spec(variant, batch_rules):
    if variant == VARIANT_AI:
        return ('## 답 형식\n각 항목의 7개 속성을 모두 채웁니다. 코드값은 주체(이행의무자·검증기관·국토교통부·한국교통안전공단·기후에너지환경부·ICAO·연료공급자), '
                '단계(계획·측정·보고·검증·산정·이행·보관), 의무성(강행·권고·임의·조건부), 입력유형(수치·선택·텍스트·서술·첨부·자동계산) 중에서 고릅니다.\n'
                '이유를 먼저 쓰고 값은 그 이유와 같은 결론으로 씁니다(이유가 강행이면 값도 강행).\n'
                '```\n{"items":[{"id":"F0003","이유":"의무성·입력유형·연결필드를 어떻게 정했는지 한 줄","값":{"주체":"…","단계":"…","의무성":"…","시점·기한":"…","입력유형":"…","근거":"…","연결필드":"-"},'
                '"확신":"높음|보통|낮음"}]}\n```')
    need = {iid: unfixed_attrs(r) for iid, r in batch_rules.items()}
    spec = '\n'.join(f'- {iid}: {", ".join(a) if a else "(채울 속성 없음)"}' for iid, a in need.items())
    return ('## 답 형식\n각 항목에서 아래 「채울 속성」만 답합니다. 코드가 정한 값이 틀렸다고 판단하면 「이의」에 속성과 이유를 적되 값은 바꾸지 않습니다. '
            '코드의 잠정 판단과 다르게 정했으면 그 이유를 「이유」에 적습니다. 이유를 먼저 쓰고 값은 그 이유와 같은 결론으로 씁니다(이유가 강행이면 값도 강행).\n'
            '코드값은 의무성(강행·권고·임의·조건부), 입력유형(수치·선택·텍스트·서술·첨부·자동계산) 중에서 고릅니다. 연결필드는 연결 후보 라벨만 쓰거나 「-」.\n'
            f'채울 속성:\n{spec}\n'
            '```\n{"items":[{"id":"F0003","이유":"한 줄","값":{"의무성":"…","입력유형":"…","연결필드":"-"},"확신":"높음|보통|낮음","이의":{}}]}\n```')


def build_prompt(item_ids, variant):
    """(system, user) — 한 번 호출에 항목 여러 개를 묶어 보낸다."""
    rules = {iid: R.rule_predict(iid) for iid in item_ids} if variant == VARIANT_HYBRID else {}
    parts = ['# 판정 기준 카드', reference_card(), '', examples_block(), '',
             f'## 분류할 항목 {len(item_ids)}개']
    parts += [item_payload(iid, variant, rule=rules.get(iid)) for iid in item_ids]
    parts += ['', output_spec(variant, rules)]
    return SYSTEM, '\n'.join(parts)


_JSON = re.compile(r'\{')


def extract_items_json(text):
    """응답 글에서 'items' 목록이 있는 첫 JSON 객체를 찾는다. (객체, 오류 문구)"""
    if not text or not text.strip():
        return None, '빈 응답'
    dec = json.JSONDecoder()
    for m in _JSON.finditer(text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('items'), list):
            return obj, None
    return None, 'JSON을 찾지 못함'
