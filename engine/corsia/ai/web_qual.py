"""
웹 연결용 정성 검증 (AI 참고 의견)  — 2026-11-17

서류 JSON의 서술 8개(규칙 Q-01~Q-08)를 확정 설정으로 판정시키고, 항목별 결과를 돌려준다.
웹(app.py)은 이 파일의 함수만 부르면 된다. 사용법은 아래 USAGE 또는 7_qualitative/web_qual_usage.md.

원칙
  1. 판정·종합점수·위반 목록은 검증엔진(코드)이 계산하고, 이 모듈은 그 값에 손대지 않는다(엔진을 부르지도 않는다).
     AI 결과는 참고용이며 별도 구조(qualitative_result_v1)에만 담긴다.
  2. 실패해도 멈추지 않는다. 키 없음·네트워크 오류·응답 해석 실패·비용 상한 초과는 예외가 아니라 상태값이다.
  3. API 키는 환경 변수 ANTHROPIC_API_KEY에서만 읽는다(corsia.ai.client가 읽음). 이 모듈은 키를 읽거나 저장하지 않는다.
  4. 규칙마다 호출 1회(서술 8개 → 최대 8회)를 동시에 보내고, 끝나는 항목부터 하나씩 알려 준다(진행 표시용).
  5. 같은 입력(같은 지시문·설정)은 디스크에 기억해 다시 부르지 않는다(비용 0). 기억 폴더: engine/ai_cache/ (.gitignore에 넣을 것).
     기억하는 것은 AI의 원문 답뿐이고, 해석·최종 판정은 읽을 때마다 지금의 코드로 다시 계산한다.
  6. 파일에 PC 경로·키를 넣지 않는다.

확정 설정: Sonnet 5.5 · adaptive · low, 기준표 v1.8, 「규칙 5 표대로 함께 줌」(방식 together: 판정 칸 + 기준표가 정한 함께 주는 칸).
"""
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import client as _client
from . import judge, rubric_text
from .models import estimate_cost, get_model
from .prompts import fields_given
from .rubric import RULES

try:
    from corsia.verify.qualitative import QUALITATIVE_ITEMS
except Exception:                      # 엔진 없이 쓸 때
    QUALITATIVE_ITEMS = []

SCHEMA = 'qualitative_result_v1'
BADGE_TEXT = 'AI 참고용 · 최종 판정 아님'
NO_KEY_TEXT = 'AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다'
DISCLAIMER = ('AI 참고 의견입니다. 검증 판정·종합점수·위반 목록은 코드가 계산하며 이 결과와 무관합니다. '
              '서술이 사실인지, 다른 서류와 값이 맞는지는 판정하지 않습니다.')

SETTINGS = {'model_alias': 'sonnet', 'thinking': 'adaptive', 'effort': 'low', 'mode': 'together', 'max_tokens': 4000}
MAX_USD_DEFAULT = 0.30                 # 웹 1회 실행 비용 상한(팀 결정 2026-11-17)
MAX_WORKERS_DEFAULT = 4                # 동시 호출 수(8회 → 약 2묶음, 순차 약 40초 → 약 11초)
CALL_TIMEOUT = 60
RETRY_SLEEP = 2.0                      # 네트워크·속도 제한 오류 뒤 한 번 다시 시도하기 전 쉬는 시간(초)
CACHE_DIR = Path(__file__).resolve().parents[2] / 'ai_cache'     # engine/ai_cache  (.gitignore에 넣을 것)

RULE_IDS = ('Q-01', 'Q-02', 'Q-03', 'Q-04', 'Q-05', 'Q-06', 'Q-07', 'Q-08')
METHOD_KEY = 'EMP.monitoring_method'
METHOD_FALLBACK = 'ER.monitoring_method'                           # EMP에 없으면 ER의 값(팀 결정)
METHOD_REQUIRED_RULES = tuple(r for r in RULE_IDS if METHOD_KEY in RULES[r].get('info', []))   # Q-02, Q-04

# 항목 상태(item.status)와 전체 상태(result.status)
ITEM_STATUSES = ('ok', 'no_text', 'skipped_condition', 'method_missing', 'budget_exceeded', 'network_error', 'api_error', 'parse_error')
RESULT_STATUSES = ('ok', 'partial', 'no_key', 'no_text', 'budget_exceeded', 'network_error', 'api_error', 'parse_error', 'config_error')

STATUS_MESSAGES = {
    'ok': '',
    'partial': '일부 항목은 AI 판정을 받지 못했습니다(항목별 상태 참고). 다시 실행하면 받은 항목은 다시 부르지 않습니다.',
    'no_key': NO_KEY_TEXT,
    'no_text': '판정할 서술이 없어 AI를 부르지 않았습니다.',
    'budget_exceeded': '예상 비용이 1회 실행 상한을 넘어 실행하지 않았습니다.',
    'network_error': '네트워크 연결이나 일시적 오류로 AI 판정을 받지 못했습니다. 잠시 뒤 다시 실행하세요.',
    'api_error': 'AI 호출이 거절되었습니다(키·크레딧·설정). 자세한 내용은 detail을 보세요. 결제·충전은 하지 말고 팀장에게 알리세요.',
    'parse_error': 'AI의 답을 해석하지 못했습니다. 다시 실행해 보세요.',
    'config_error': '기준표 파일을 읽지 못했습니다(7_qualitative/qualitative_rubric_v1.md).',
}

USAGE = """\
from corsia.ai import web_qual

# 1) 실행 전: 예상 비용(호출 없음)
est = web_qual.estimate_qualitative(documents, data_gap_pct=c['data_gap_pct'])
#    → {'n_rules': 8, 'n_calls': 8, 'n_cached': 0, 'estimated_usd': 0.1, 'max_usd': 0.3, 'key_set': True, 'within_budget': True, ...}

# 2) 버튼을 눌렀을 때: 항목이 끝날 때마다 하나씩 받기(진행 표시)
for ev in web_qual.iter_qualitative(documents, data_gap_pct=c['data_gap_pct']):
    if ev['type'] == 'plan':   ...   # {'n_total', 'n_calls', 'n_cached', 'estimated_usd', 'max_usd'}
    if ev['type'] == 'item':   ...   # {'item': {...}, 'done': 3, 'total': 8}
    if ev['type'] == 'done':   result = ev['result']   # qualitative_result_v1

# (한 번에 받기) result = web_qual.run_qualitative(documents, data_gap_pct=..., on_item=lambda item, done, total: ...)
"""


# ───────────────────────── 입력 모으기 ─────────────────────────
def _unwrap(documents):
    """서류 JSON 전체({'documents': {...}})나 그 안쪽({'EMP': ...}) 둘 다 받는다."""
    if isinstance(documents, dict) and 'documents' in documents and not any(k in documents for k in ('EMP', 'ER', 'VR')):
        documents = documents['documents']
    return documents if isinstance(documents, dict) else {}


def _get(docs, path):
    doc, key = path.split('.', 1)
    d = docs.get(doc)
    return d.get(key) if isinstance(d, dict) else None


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, tuple)) and not v)


def find_method(docs):
    """모니터링 방법 값. EMP를 우선 쓰고 없으면 ER. 반환 (값 또는 None, 출처 또는 None)"""
    for path in (METHOD_KEY, METHOD_FALLBACK):
        v = _get(docs, path)
        if not _empty(v):
            return str(v).strip(), path
    return None, None


def _pct(docs, data_gap_pct):
    if data_gap_pct is not None:
        return data_gap_pct
    v = _get(docs, 'ER.data_gap_pct')
    return v


def _question_of(rule_id, sec):
    q = sec.get('question') or ''
    if not q:
        q = next((r[3] for r in QUALITATIVE_ITEMS if r[0] == rule_id), '')
    return q


def _source_clause(rule_id):
    return next((r[4] for r in QUALITATIVE_ITEMS if r[0] == rule_id), '')


def build_plans(documents, data_gap_pct=None, rubric=None):
    """규칙마다 AI를 부를지, 안 부르면 이유가 무엇인지 정한다. 반환 (계획 목록, 모니터링 방법 정보)"""
    docs = _unwrap(documents)
    rubric = rubric or rubric_text.load_rubric()
    method, method_src = find_method(docs)
    pct = _pct(docs, data_gap_pct)
    plans = []
    for rid in RULE_IDS:
        spec = RULES[rid]
        sec = rubric['rules'][rid]
        main = spec['field']
        values = {}
        for name, _role in fields_given(rid, SETTINGS['mode']):
            if name == METHOD_KEY:
                values[name] = method
            elif name == 'ER.data_gap_pct':
                values[name] = pct
            else:
                values[name] = _get(docs, name)
        doc, item_key = main.split('.', 1)
        plan = {'rule_id': rid, 'document': doc, 'item': item_key, 'field': main, 'question': _question_of(rid, sec),
                'source_clause': _source_clause(rid), 'values': values, 'state': 'call', 'message': ''}
        if rid == 'Q-06':
            why = judge.skip_reason(rid, {'ER.data_gap_pct': pct})
            if why:
                plan.update(state='skipped_condition', message=why)
        if plan['state'] == 'call' and _empty(values.get(main)):
            plan.update(state='no_text', message='서술이 비어 있어 AI를 부르지 않았습니다(비어 있음은 검증엔진의 정성 규칙이 따로 알립니다).')
        if plan['state'] == 'call' and rid in METHOD_REQUIRED_RULES and method is None:
            plan.update(state='method_missing',
                        message='모니터링 방법 값이 서류에 없어 판정하지 않았습니다(EMP.monitoring_method, ER.monitoring_method 모두 없음).')
        plans.append(plan)
    return plans, {'value': method, 'source': method_src}


def _prompt_of(plan, rubric):
    return judge.build_messages(plan['rule_id'], plan['values'], SETTINGS['mode'], rubric)


def _cache_key(system, user):
    blob = json.dumps({'system': system, 'user': user, 'model': SETTINGS['model_alias'], 'thinking': SETTINGS['thinking'],
                       'effort': SETTINGS['effort'], 'max_tokens': SETTINGS['max_tokens']}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


# ───────────────────────── 디스크 기억 ─────────────────────────
def _cache_path(cache_dir, key):
    return Path(cache_dir) / f'{key}.json'


def _cache_read(cache_dir, key):
    if cache_dir is None:
        return None
    try:
        data = json.loads(_cache_path(cache_dir, key).read_text(encoding='utf-8'))
        if not (isinstance(data, dict) and isinstance(data.get('raw_text'), str)):
            return None
        if data.get('source') == 'mock':                                   # 모의 응답으로 만든 기억은 진짜 실행에서 쓰지 않는다
            return None
        if data.get('source') is None and _MOCK_MARK in data['raw_text']:   # 출처 표시가 생기기 전(11/17 이전)에 모의 실행이 남긴 기억
            return None
        return data
    except Exception:
        return None


def _cache_write(cache_dir, key, record):
    if cache_dir is None:
        return
    try:
        d = Path(cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f'{key}.tmp{threading.get_ident()}'
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_cache_path(d, key))
    except Exception:
        pass                                   # 기억에 실패해도 판정 결과에는 영향 없음


# ───────────────────────── 항목 결과 만들기 ─────────────────────────
_MOCK_MARK = '모의 응답입니다'            # 모의 호출(mock_call)이 항상 reason에 넣는 문구. 옛 기억 중 모의 출처를 알아보는 데만 쓴다
_CITE_PROBLEM = re.compile(r'인용 없음|주지 않은 칸|근거 칸이 아님|인용문이')


def _blank_item(plan, status, message='', verdict=None):
    return {'rule_id': plan['rule_id'], 'document': plan['document'], 'item': plan['item'], 'question': plan['question'],
            'cited_clause': plan['source_clause'], 'status': status, 'message': message, 'verdict': verdict,
            'reason': message if status in ('skipped_condition', 'method_missing', 'no_text') else None,
            'missing_points': [], 'checks': [], 'flags': [], 'fake_citations': 0, 'clause_matches': None, 'ai_cited_clause': None,
            'source': None,                              # api(이번에 실제 호출) | cache(기억에서) | mock(모의 응답) | None(호출하지 않은 항목)
            'cached': False, 'cost_usd': 0.0, 'tokens': {'input': 0, 'output': 0}, 'seconds': 0.0}


def _checks_of(rule_id, ev, rubric):
    sec = rubric['rules'][rule_id]['checks']
    out = []
    for cid, rec in ev['items'].items():
        flag = rec.get('flag') or ''
        out.append({'id': cid, 'kind': sec.get(cid, {}).get('kind'), 'text': sec.get(cid, {}).get('text'),
                    'status': rec.get('status'), 'ai_status': rec.get('ai_status'),
                    'evidence': rec.get('evidence'), 'field': rec.get('field'), 'note': rec.get('note'),
                    'flag': flag or None,
                    'citation_problem': bool(_CITE_PROBLEM.search(flag)),
                    'fake_citation': flag.startswith('인용문이')})
    return out


def _item_from_raw(plan, raw_text, rubric):
    """AI의 원문 답을 판정 코드로 정리해 항목 결과를 만든다(판정 코드는 그대로 재사용). 해석에 실패하면 None"""
    obj, err = judge.extract_json(raw_text)
    if obj is None:
        return None, err
    ev = judge.evaluate(plan['rule_id'], obj, plan['values'], SETTINGS['mode'])
    item = _blank_item(plan, 'ok')
    checks = _checks_of(plan['rule_id'], ev, rubric)
    ai_clause = ev.get('cited_clause')
    expected = plan['source_clause']
    item.update(verdict=ev['verdict'], reason=ev.get('reason'), missing_points=ev.get('missing_points') or [],
                checks=checks, flags=ev.get('flags') or [], fake_citations=sum(1 for c in checks if c['fake_citation']),
                ai_cited_clause=ai_clause,
                clause_matches=(None if not ai_clause or not expected else (_loose_clause(expected) in _loose_clause(ai_clause))))
    return item, None


def _loose_clause(s):
    return re.sub(r'[\s·~,\-–]+', '', str(s or '')).lower()


def _status_of_error(exc):
    s = getattr(exc, 'status', 0) or 0
    kind = str(getattr(exc, 'kind', ''))
    if s == 429 or s >= 500 or any(w in kind for w in ('네트워크', '시간 초과', '속도 제한', '서버', '응답을 푸는')):
        return 'network_error'
    return 'api_error'


# ───────────────────────── 호출 ─────────────────────────
def _call_once(call_fn, plan, rubric, cache_dir, use_cache, state, src='api'):
    """규칙 하나를 판정한다. 예외를 던지지 않고 항목 결과를 돌려준다."""
    t0 = time.time()
    system, user = _prompt_of(plan, rubric)
    key = _cache_key(system, user)
    if use_cache:
        hit = _cache_read(cache_dir, key)
        if hit:
            item, _err = _item_from_raw(plan, hit['raw_text'], rubric)
            if item is not None:
                item.update(cached=True, source='cache', cost_usd=0.0, original_cost_usd=hit.get('cost_usd'), tokens={'input': 0, 'output': 0},
                            seconds=round(time.time() - t0, 2))
                return item
    with state['lock']:
        if state['spent'] >= state['max_usd']:
            return _blank_item(plan, 'budget_exceeded', '1회 실행 비용 상한에 닿아 이 항목은 호출하지 않았습니다.')
    cost, tokens, last_err, raw = 0.0, {'input': 0, 'output': 0}, None, None
    for attempt in (1, 2):
        try:
            r = call_fn(user, model_key=SETTINGS['model_alias'], system=system, max_tokens=SETTINGS['max_tokens'],
                        thinking=SETTINGS['thinking'], effort=SETTINGS['effort'], timeout=CALL_TIMEOUT)
        except _client.AIError as exc:
            st = _status_of_error(exc)
            last_err = (st, f'{exc.kind} — {exc.advice}', exc.detail)
            if st == 'network_error' and attempt == 1:
                time.sleep(RETRY_SLEEP)
                continue
            break
        except Exception as exc:                                   # 예상하지 못한 오류도 상태값으로
            last_err = ('api_error', f'{type(exc).__name__}', str(exc)[:160])
            break
        c = (getattr(r, 'cost', None) or {})
        cost += float(c.get('usd', 0.0) or 0.0)
        tk = c.get('tokens') or {}
        tokens = {'input': tokens['input'] + int(tk.get('input', 0) or 0), 'output': tokens['output'] + int(tk.get('output', 0) or 0)}
        with state['lock']:
            state['spent'] += float(c.get('usd', 0.0) or 0.0)
        text = getattr(r, 'text', '') or ''
        item, err = _item_from_raw(plan, text, rubric)
        if item is not None:
            if use_cache and src != 'mock':                              # 모의 응답은 기억에 쓰지 않는다
                _cache_write(cache_dir, key, {'raw_text': text, 'source': src, 'cost_usd': round(cost, 6), 'tokens': tokens,
                                              'created': datetime.now().isoformat(timespec='seconds')})
            item.update(source=src, cost_usd=round(cost, 6), tokens=tokens, seconds=round(time.time() - t0, 2),
                        truncated=bool(getattr(r, 'truncated', False)))
            return item
        last_err = ('parse_error', f'AI 답에서 {err}', text[:120])
        if attempt == 2 or state['spent'] >= state['max_usd']:
            break
    st, msg, detail = last_err or ('api_error', '알 수 없는 오류', '')
    item = _blank_item(plan, st, msg)
    item.update(detail=detail, cost_usd=round(cost, 6), tokens=tokens, seconds=round(time.time() - t0, 2))
    return item


# ───────────────────────── 공개 함수 ─────────────────────────
def estimate_qualitative(documents, data_gap_pct=None, max_usd=MAX_USD_DEFAULT, cache_dir=CACHE_DIR, use_cache=True):
    """AI를 부르지 않고 예상 비용·호출 수를 알려 준다(버튼 옆 표시용). 예외를 던지지 않는다."""
    out = {'n_rules': len(RULE_IDS), 'n_calls': 0, 'n_cached': 0, 'n_skipped': 0, 'estimated_usd': 0.0, 'max_usd': max_usd,
           'within_budget': True, 'key_set': _client.key_is_set(), 'status': 'ok', 'status_message': '', 'skipped': [],
           'input_hash': None}                                  # 결과와 같은 값: 웹이 「같은 서류」를 알아보는 열쇠(세션 기억용)
    try:
        rubric = rubric_text.load_rubric()
        plans, _m = build_plans(documents, data_gap_pct, rubric)
    except Exception:
        out.update(status='config_error', status_message=STATUS_MESSAGES['config_error'], within_budget=False)
        return out
    m = get_model(SETTINGS['model_alias'])
    total = 0.0
    out['input_hash'] = _input_hash(plans, rubric)
    for p in plans:
        if p['state'] != 'call':
            out['n_skipped'] += 1
            out['skipped'].append({'rule_id': p['rule_id'], 'state': p['state'], 'message': p['message']})
            continue
        system, user = _prompt_of(p, rubric)
        if use_cache and _cache_read(cache_dir, _cache_key(system, user)):
            out['n_cached'] += 1
            continue
        n_in = int((len(system) + len(user)) * 0.95)
        total += n_in * m.input / 1e6 + 900 * m.output / 1e6          # 출력은 실측 평균(약 570토큰)보다 넉넉히 900
        out['n_calls'] += 1
    out['estimated_usd'] = round(total, 4)
    out['within_budget'] = total <= max_usd
    if not out['key_set'] and out['n_calls'] > 0:
        out['status'], out['status_message'] = 'no_key', NO_KEY_TEXT
    elif not out['within_budget']:
        out['status'], out['status_message'] = 'budget_exceeded', STATUS_MESSAGES['budget_exceeded']
    return out


def iter_qualitative(documents, data_gap_pct=None, call_fn=None, max_usd=MAX_USD_DEFAULT, max_workers=MAX_WORKERS_DEFAULT,
                     cache_dir=CACHE_DIR, use_cache=True, now=None):
    """정성 검증을 실행하며 사건을 하나씩 내보낸다(진행 표시용).
       {'type': 'plan', ...}  →  {'type': 'item', 'item': {...}, 'done': i, 'total': 8} 를 항목마다  →  {'type': 'done', 'result': {...}}
    끝나는 순서대로 나오므로 규칙 번호 순서가 아닐 수 있다. 최종 result['items']는 Q-01~Q-08 순서다.
    call_fn을 안 주면 corsia.ai.client.call을 쓴다(시험에서는 모의 함수를 넣는다)."""
    t0 = time.time()
    created = (now or datetime.now()).isoformat(timespec='seconds')
    try:
        rubric = rubric_text.load_rubric()
        plans, method = build_plans(documents, data_gap_pct, rubric)
    except Exception:
        yield {'type': 'done', 'result': _result('config_error', [], {}, None, max_usd, 0.0, 0, 0, t0, created, None)}
        return
    calls_needed = [p for p in plans if p['state'] == 'call']
    if calls_needed and call_fn is None and not _client.key_is_set():          # 키가 없으면 호출 없이 안내만 (기억이 있어도 같은 동작)
        yield {'type': 'done', 'result': _result('no_key', [], {}, None, max_usd, 0.0, 0, 0, t0, created, method)}
        return
    est = estimate_qualitative(documents, data_gap_pct, max_usd, cache_dir, use_cache)
    if est['estimated_usd'] > max_usd:
        yield {'type': 'done', 'result': _result('budget_exceeded', [], {}, est['estimated_usd'], max_usd, 0.0, 0, 0, t0, created, method)}
        return
    call_fn = call_fn or _client.call
    src = getattr(call_fn, 'source', 'api')                    # 모의 호출은 mock, 실제 호출(corsia.ai.client.call)은 api
    if src == 'mock':
        cache_dir, use_cache = None, False                      # 모의 실행은 디스크 기억을 읽지도 쓰지도 않는다
    yield {'type': 'plan', 'n_total': len(plans), 'n_calls': est['n_calls'], 'n_cached': est['n_cached'],
           'estimated_usd': est['estimated_usd'], 'max_usd': max_usd}
    state = {'lock': threading.Lock(), 'spent': 0.0, 'max_usd': max_usd}
    done_items, total = {}, len(plans)

    def emit(item):
        done_items[item['rule_id']] = item
        return {'type': 'item', 'item': item, 'done': len(done_items), 'total': total}

    to_run = []
    for p in plans:
        if p['state'] == 'call':
            to_run.append(p)
        else:
            verdict = '판단 불가' if p['state'] == 'method_missing' else None
            yield emit(_blank_item(p, p['state'], p['message'], verdict))
    if to_run:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(to_run)))) as pool:
            futs = {pool.submit(_call_once, call_fn, p, rubric, cache_dir, use_cache, state, src): p for p in to_run}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    item = fut.result()
                except Exception as exc:                          # 방어: 어떤 경우에도 상태값으로
                    item = _blank_item(p, 'api_error', f'{type(exc).__name__}')
                yield emit(item)
    items = [done_items[r] for r in RULE_IDS if r in done_items]
    result = _result(None, items, {'spent': state['spent']}, est['estimated_usd'], max_usd, state['spent'], len(to_run), est['n_cached'],
                     t0, created, method, rubric=rubric, plans=plans, workers=max_workers)
    yield {'type': 'done', 'result': result}


def run_qualitative(documents, data_gap_pct=None, call_fn=None, max_usd=MAX_USD_DEFAULT, max_workers=MAX_WORKERS_DEFAULT,
                    cache_dir=CACHE_DIR, use_cache=True, on_item=None, now=None):
    """iter_qualitative를 끝까지 돌려 최종 결과(qualitative_result_v1)만 돌려준다. on_item(item, done, total)로 진행을 알릴 수 있다."""
    result = None
    for ev in iter_qualitative(documents, data_gap_pct, call_fn, max_usd, max_workers, cache_dir, use_cache, now):
        if ev['type'] == 'item' and on_item:
            on_item(ev['item'], ev['done'], ev['total'])
        elif ev['type'] == 'done':
            result = ev['result']
    return result


def _overall_status(items):
    called = [i for i in items if i['status'] not in ('no_text', 'skipped_condition', 'method_missing')]
    # 판정할 서술이 하나도 없으면(모두 서술 없음, 또는 서술 없음과 조건 미발생뿐) AI를 부르지 않은 것이므로 no_text
    if not items or all(i['status'] in ('no_text', 'skipped_condition') for i in items) and any(i['status'] == 'no_text' for i in items):
        return 'no_text'
    if not called:
        return 'ok'
    bad = [i for i in called if i['status'] != 'ok']
    if not bad:
        return 'ok'
    if len(bad) < len(called):
        return 'partial'
    for st in ('budget_exceeded', 'api_error', 'network_error', 'parse_error'):
        if any(i['status'] == st for i in bad):
            return st
    return 'api_error'


def _input_hash(plans, rubric):
    blob = json.dumps({'rubric': rubric.get('version'), 'settings': SETTINGS,
                       'inputs': {p['rule_id']: p['values'] for p in plans if p['state'] == 'call'}}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def _result(status, items, extra, estimated, max_usd, spent, n_calls, n_cached, t0, created, method, rubric=None, plans=None, workers=0):
    status = status or _overall_status(items)
    verdicts = {'충분': 0, '보완 필요': 0, '판단 불가': 0}
    for i in items:
        if i.get('verdict') in verdicts:
            verdicts[i['verdict']] += 1
    tok_in = sum(i['tokens']['input'] for i in items)
    tok_out = sum(i['tokens']['output'] for i in items)
    m = get_model(SETTINGS['model_alias'])
    return {
        'schema': SCHEMA, 'status': status, 'status_message': STATUS_MESSAGES.get(status, ''),
        'ai_reference_only': True, 'badge': BADGE_TEXT, 'disclaimer': DISCLAIMER,
        'created': created,
        'settings': {'model': m.model_id, 'model_alias': SETTINGS['model_alias'], 'thinking': SETTINGS['thinking'], 'effort': SETTINGS['effort'],
                     'mode': SETTINGS['mode'], 'max_tokens': SETTINGS['max_tokens'], 'rubric_version': (rubric or {}).get('version')},
        'input_hash': _input_hash(plans, rubric) if plans is not None and rubric else None,
        'monitoring_method': method,
        'cost': {'estimated_usd': estimated, 'actual_usd': round(spent, 6), 'max_usd': max_usd, 'n_calls': n_calls,
                 'n_cached': n_cached, 'tokens': {'input': tok_in, 'output': tok_out}},
        'timing': {'seconds': round(time.time() - t0, 2), 'workers': workers},
        'summary': {'n_rules': len(RULE_IDS), 'n_items': len(items), 'verdicts': verdicts,
                    'n_failed': sum(1 for i in items if i['status'] in ('network_error', 'api_error', 'parse_error', 'budget_exceeded')),
                    'fake_citations': sum(i.get('fake_citations', 0) for i in items),
                    'sources': {s: sum(1 for i in items if i.get('source') == s) for s in ('api', 'cache', 'mock')}},
        'items': items,
    }


def status_message(status):
    """화면에 한 줄로 보여 줄 안내 문구"""
    return STATUS_MESSAGES.get(status, '')


# ───────────────────────── 엑셀용 표 ─────────────────────────
def excel_rows(result):
    """엑셀 「정성 검증」 시트용 행 목록. 키가 없거나 실행하지 않았으면 빈 목록. 열 이름은 한글."""
    rows = []
    for it in (result or {}).get('items', []):
        verdict = it.get('verdict') or {'no_text': '서술 없음', 'skipped_condition': '해당 없음(조건 미발생)'}.get(it['status'], f"판정 못 받음({it['status']})")
        checks = ' / '.join(f"{c['id']} {c['status']}" for c in it.get('checks', []))
        rows.append({'규칙': it['rule_id'], '서류': it['document'], '항목': it['item'], 'AI 참고 판정': verdict,
                     '이유': it.get('reason') or it.get('message') or '', '근거 조항': it.get('cited_clause') or '',
                     '보완할 점': '\n'.join(it.get('missing_points') or []), '확인 항목별 상태': checks,
                     '인용 문제': '있음' if (it.get('fake_citations') or any(c.get('citation_problem') for c in it.get('checks', []))) else '',
                     '출처': {'api': 'AI 호출', 'cache': '기억', 'mock': '모의(시험용)'}.get(it.get('source'), ''),
                     '캐시': '예' if it.get('cached') else ''})
    return rows


# ───────────────────────── 시험용 모의 호출 ─────────────────────────
class _MockResult:
    def __init__(self, text, usage, model_key='sonnet'):
        self.text, self.stop_reason = text, 'end_turn'
        self.cost = estimate_cost(model_key, usage)
        self.settings = {}

    truncated = False


def mock_call(kind='ok', source='mock'):
    """AI를 부르지 않는 모의 호출(시험·시연 틀 확인용). kind: ok | weak | fake_quote | bad_json | network | flaky | rate_limit_once | auth_error | slow | expensive"""
    state = {'n': 0, 'lock': threading.Lock()}

    def fn(user, model_key='sonnet', system=None, max_tokens=0, thinking=None, effort=None, timeout=0, **_kw):
        with state['lock']:
            state['n'] += 1
            n = state['n']
        if kind == 'network' or (kind == 'rate_limit_once' and n == 1):
            raise _client.AIError('네트워크 연결 실패' if kind == 'network' else '속도 제한', '잠시 뒤 다시 실행하세요.', 0 if kind == 'network' else 429, '')
        if kind == 'auth_error':
            raise _client.AIError('인증 오류(키가 틀렸거나 삭제·만료됨)', 'Console의 키 상태를 확인하세요.', 401, '')
        if kind == 'slow':
            time.sleep(0.15)
        if kind == 'bad_json' or (kind == 'flaky' and n % 2 == 1):
            return _MockResult('죄송하지만 답할 수 없습니다.', {'input_tokens': 3000, 'output_tokens': 40})
        rid = re.search(r'\[규칙\] (Q-0\d)', user).group(1)
        checks = re.findall(r'^- (C\d) \(', user, re.M)
        m = re.search(r'\[칸 이름: ([A-Z]+\.[a-z_]+)\][^\n]*— 판정 칸\n(.*?)(?:\n\n\[칸 이름|\n\n위 형식)', user, re.S)
        field, body = (m.group(1), m.group(2)) if m else ('', '')
        sent = re.split(r'(?<=[.!?다])\s', body.strip())[0][:60] if body.strip() else ''
        out = []
        for i, cid in enumerate(checks):
            if kind == 'weak':
                out.append({'id': cid, 'status': '부족' if i == 0 else '없음', 'evidence': sent if i == 0 else '', 'field': field, 'note': '지목할 수 없음'})
            else:
                ev = '입력에 없는 문장입니다 가짜 인용' if (kind == 'fake_quote' and i == 0) else sent
                out.append({'id': cid, 'status': '있음', 'evidence': ev, 'field': field})
        obj = {'rule_id': rid, 'checks': out, 'missing_points': ['보완할 점 예시'] if kind == 'weak' else [],
               'reason': '모의 응답입니다.', 'cited_clause': _source_clause(rid)}
        usage = {'input_tokens': 20000, 'output_tokens': 3000} if kind == 'expensive' else {'input_tokens': 3500, 'output_tokens': 600}
        return _MockResult(json.dumps(obj, ensure_ascii=False), usage)
    fn.calls = state
    fn.source = source               # 'mock'이면 web_qual이 디스크 기억을 읽지도 쓰지도 않고 항목 출처를 mock으로 표시한다. 시험에서 「진짜처럼」 쓸 때만 'api'
    return fn


__all__ = ['SCHEMA', 'SETTINGS', 'MAX_USD_DEFAULT', 'CACHE_DIR', 'BADGE_TEXT', 'NO_KEY_TEXT', 'estimate_qualitative', 'iter_qualitative',
           'run_qualitative', 'status_message', 'excel_rows', 'find_method', 'build_plans', 'mock_call', 'USAGE']
