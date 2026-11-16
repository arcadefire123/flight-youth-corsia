"""근거를 붙인 답 만들기와 인용 확인 (7단계 RAG).

변형 3가지
  rag    : 질문과 비슷한 조각 k개(기본 5)만 AI에게 같이 넘긴다
  full   : 법령·고시 전체(약 4.3만 토큰)를 통째로 넘긴다(검색 없음). 앞부분을 캐시해 두 번째 질문부터 입력 단가가 1/20
  closed : 자료를 주지 않는다(AI가 아는 대로). 자료를 붙이는 효과를 재는 대조군
"""
import json
import re

from . import corpus as K
from .search import Index

SYSTEM = ('당신은 국제항공 탄소 배출량 관리에 관한 법령·고시(법률, 시행령, 시행규칙, 국토교통부 지침) 안내 보조입니다. '
          '실무자의 질문에 짧고 정확하게 답합니다.\n'
          '규칙: ① 아래 [자료]에 적힌 내용만으로 답한다. 자료에 근거가 없으면 지어내지 말고 answerable을 false로 하고 '
          '「제공된 자료에서 근거를 찾지 못했습니다」라고 답한다. ② 답은 3문장 이내로 쓰고 기한·수치·대상은 원문 그대로 쓴다. '
          '③ citations에는 근거가 된 [자료]의 ID를 [자료]에 적힌 그대로 쓴다. ④ JSON 하나만 쓰고 앞뒤에 설명을 붙이지 않는다.')
SYSTEM_CLOSED = ('당신은 국제항공 탄소 배출량 관리에 관한 법령·고시 안내 보조입니다. 자료는 주어지지 않으며 아는 대로 답합니다. '
                 '확실하지 않으면 지어내지 말고 answerable을 false로 하고 「확실하지 않습니다」라고 답합니다. '
                 'citations에는 근거 조항을 「법 제8조제4항」 「지침 별표 1 5.b4)」 같은 표기로 씁니다. 답은 3문장 이내, JSON 하나만 씁니다.')
FORMAT = ('## 답 형식 (JSON 하나)\n```\n{"answerable": true, "answer": "…", "citations": ["법 제8조제4항", "시행규칙 제7조"]}\n```')


def material(chunks_scored):
    return '\n'.join(f"[{c['id']}] {c['text']}" for c, _s, _why in chunks_scored)


def build_rag(question, index, k=5):
    hits = index.search(question, k=k)
    user = f'## 질문\n{question}\n\n## 자료\n{material(hits)}\n\n{FORMAT}'
    return SYSTEM, user, hits


def full_prefix(chunks):
    return '## 자료 (법령·고시 전체. 각 조각 앞의 [ ] 안이 ID입니다)\n' + K.full_context(chunks)


def build_full(question, chunks):
    return SYSTEM, f'## 질문\n{question}\n\n{FORMAT}', full_prefix(chunks)


def build_closed(question):
    return SYSTEM_CLOSED, f'## 질문\n{question}\n\n{FORMAT}'


def extract_answer_json(text):
    if not text or not text.strip():
        return None, '빈 응답'
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{', text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and 'answer' in obj:
            return obj, None
    return None, 'JSON을 찾지 못함'


def _strip(i):
    return re.sub(r'#\d+$', '', str(i)).strip()


def check_citations(cites, provided_ids):
    """인용이 넘겨 준 자료 안에 있는지. 인용이 자료 ID와 같거나, 자료 ID의 하위(제6조 → 제6조제3항)·상위(제6조제3항 → 제6조)면 유효.
    반환: (유효 목록, 무효 목록). provided_ids가 None이면(자료 없는 변형) 점검하지 않고 모두 유효로 둔다."""
    if provided_ids is None:
        return list(cites), []
    pids = [_strip(p) for p in provided_ids]
    ok, bad = [], []
    for c in cites:
        c2 = _strip(c)
        if any(c2 == p or c2.startswith(p) or p.startswith(c2) for p in pids):
            ok.append(c)
        else:
            bad.append(c)
    return ok, bad


def parse_result(text, provided_ids):
    obj, err = extract_answer_json(text)
    if obj is None:
        return {'answerable': None, 'answer': '', 'citations': [], 'valid': [], 'invalid': [], 'json_error': err}
    cites = obj.get('citations') or []
    if isinstance(cites, str):
        cites = K.split_cites(cites)
    cites = [str(c).strip() for c in cites if str(c).strip()]
    ok, bad = check_citations(cites, provided_ids)
    return {'answerable': bool(obj.get('answerable', True)), 'answer': str(obj.get('answer', '')).strip(), 'citations': cites,
            'valid': ok, 'invalid': bad, 'json_error': None}


def ask(question, variant, index, chunks, call_fn, k=5, model='sonnet', effort='low', thinking='adaptive', max_tokens=3000):
    """질문 하나에 답한다. call_fn은 corsia.ai.client.call과 같은 모양. 반환: 결과 dict(+ retrieved, cost)."""
    prefix = None
    if variant == 'rag':
        system, user, hits = build_rag(question, index, k)
        provided = [c['id'] for c, _s, _w in hits]
        retrieved = [{'id': c['id'], 'score': s, 'why': w} for c, s, w in hits]
    elif variant == 'full':
        system, user, prefix = build_full(question, chunks)
        provided = [c['id'] for c in chunks]
        retrieved = []
    elif variant == 'closed':
        system, user = build_closed(question)
        provided, retrieved = None, []
    else:
        raise ValueError(variant)
    kw = dict(model_key=model, system=system, max_tokens=max_tokens, thinking=thinking, effort=effort)
    if prefix:
        kw['cache_prefix'] = prefix
    res = call_fn(user, **kw)
    out = parse_result(res.text, provided)
    out.update({'variant': variant, 'retrieved': retrieved, 'raw': res.text[:600],
                'usd': (res.cost or {}).get('usd', 0.0), 'tokens': (res.cost or {}).get('tokens'),
                'truncated': bool(getattr(res, 'truncated', False))})
    return out
