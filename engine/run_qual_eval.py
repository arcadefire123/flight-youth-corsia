"""
7단계 E - 정성 검증 시험 세트를 AI로 판정하고 정답과 비교

    engine 폴더에서
    python run_qual_eval.py --set pilot --dry-run               # AI 호출 없이 지시문 크기와 예상 비용만
    python run_qual_eval.py --set pilot --mock perfect          # AI 호출 없이 전체 흐름 시험 (정답을 그대로 답하는 가짜 AI)
    python run_qual_eval.py --set pilot --model sonnet --max-usd 0.5    # 실제 실행 (E2 파일럿)

옵션
  --set     pilot(규칙마다 1개, 8개) | all | claude | team | seed | 사례ID를 쉼표로 (예: Q01-S1,Q04-B)
  --mode    single(한 칸만) | together(함께 줌) | both
  --model   haiku | sonnet | opus          (가격·모델 ID는 corsia/ai/models.py)
  --thinking default | adaptive | off | between_tools | disabled ;  --effort default | low | medium | high
            default = 생각 설정을 보내지 않음(Sonnet 5.5는 adaptive, API 기본 노력 수준 high — 공식 문서)
            off = 그 모델에 맞는 「생각 끄기」(Sonnet 5.5는 between_tools, Haiku 5.5는 disabled). Sonnet 5.5에 disabled를 보내면
                  400 오류라 호출 전에 막는다. between_tools·off는 노력 수준 low·medium·high에서만
  --repeat  같은 입력 반복 횟수 (일관성 측정)
  --max-usd 이번 실행의 비용 상한(USD). 실행 전 추정이 넘으면 시작하지 않고, 실행 중 누계가 넘으면 멈춘다
  --label   결과 폴더 이름에 붙일 말
  --report  기존 결과 폴더를 지정하면 AI를 부르지 않고 summary.md·summary.json만 다시 만든다
  --only-rep N   --report·--rejudge와 함께: N회차 호출만 따로 집계(summary_repN.md, 결과 파일은 그대로)
  --resume  끊긴 실행의 결과 폴더를 지정하면 같은 설정으로 남은 호출만 이어서 한다(이미 한 호출은 다시 하지 않음)

결과: 7_qualitative/runs/<시각>_<label>/ 에 config.json, results.jsonl(호출마다 한 줄), summary.md, summary.json
화면과 결과 파일 어디에도 API 키는 나오지 않는다(키는 corsia/ai/client.py가 환경 변수에서만 읽음).
"""
import argparse
import contextlib
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corsia.ai.models import MODELS, estimate_cost                       # noqa: E402
from corsia.ai.client import resolve_thinking, AIError                   # noqa: E402
from corsia.ai.rubric import RULES                                       # noqa: E402
from corsia.ai.rubric_text import load_rubric                            # noqa: E402
from corsia.ai.judge import judge_case, build_messages, skip_reason      # noqa: E402

ROOT = HERE.parent
TESTSET = ROOT / '7_qualitative' / 'qual_testset_v1.json'
RUNS = ROOT / '7_qualitative' / 'runs'

# 파일럿 8개: 규칙마다 1개, 유형을 고르게 (모호·B 유형·충분·해당 없음·삭제·숫자 불일치·칸 위치·판단 불가 섞임)
PILOT = ['Q01-D2', 'Q02-B', 'Q03-S1', 'Q04-CERT', 'Q05-D1', 'Q06-M', 'Q07-F1', 'Q08-N2']

# 실행 전 추정용. E2 파일럿(2026-11-13, Sonnet 기본 설정 8회) 실측: 입력 17,621토큰/지시문 18,691자 = 0.94토큰/자,
# 출력 평균 1,110토큰(최대 2,016). 상한 판단에 쓰므로 조금 여유 있게 잡음
EST_OUTPUT_TOKENS = 1300     # 설정별 값을 모르는 경우의 기본값
EST_TOKENS_PER_CHAR = 0.95
# 설정별 1회 평균 출력 토큰 (E3 실측, Sonnet 5.5: 기본 adaptive·high 1,201 / low 755 / between_tools·high 855). 추정은 여유를 둠.
# Opus 5.5는 실측 전이라 보수적으로 2,000으로 잡음(파일럿 8회 뒤 실측값으로 고침)
def est_output_tokens(model_key, thinking, effort):
    if model_key == 'opus':
        return 1400          # E4 파일럿 실측 평균 1,216 + 여유
    if model_key == 'sonnet':
        if effort == 'low':
            return 850
        if thinking in ('off', 'between_tools'):
            return 950
        return 1300
    return EST_OUTPUT_TOKENS
MAX_CONSECUTIVE_ERRORS = 3   # 같은 오류가 연달아 나면 멈춤(설정이 맞지 않을 때 헛돌지 않게)


def pick_cases(cases, which):
    if which == 'pilot':
        ids = PILOT
    elif which in ('all',):
        return cases
    elif which in ('claude', 'team', 'seed', 'new'):
        return [c for c in cases if c.get('group') == which]
    elif which == 'borderline':
        return [c for c in cases if c.get('borderline')]
    elif re.fullmatch(r'Q-0\d', which):                    # 규칙 하나의 모든 사례 (예: --set Q-02)
        return [c for c in cases if c['rule_id'] == which]
    else:
        ids = [x.strip() for x in which.split(',') if x.strip()]
    by = {c['case_id']: c for c in cases}
    missing = [i for i in ids if i not in by]
    if missing:
        raise SystemExit(f'시험 세트에 없는 사례: {missing}')
    return [by[i] for i in ids]


# ---- 가짜 AI (비용 없이 흐름 시험) ----
class _FakeResult:
    def __init__(self, text, usage, model_key):
        self.text, self.stop_reason, self.truncated = text, 'end_turn', False
        self.cost = estimate_cost(model_key, usage)


def make_mock(kind, case_lookup):
    """perfect: 정답을 그대로 JSON으로 답함 / noisy: 앞뒤 설명을 붙이고 칸 이름·인용을 일부 틀림"""
    state = {'n': 0}

    def fn(user, model_key='sonnet', system='', max_tokens=0, thinking=None, effort=None):
        cid, mode = case_lookup['current']
        case = case_lookup['cases'][cid]
        exp = case['expected'][mode]
        checks = []
        for k, x in exp['checks'].items():
            d = {'id': k, 'status': x['status']}
            if x.get('evidence'):
                d['evidence'] = x['evidence']
                if x.get('field'):
                    d['field'] = x['field']
            checks.append(d)
        obj = {'rule_id': case['rule_id'], 'checks': checks, 'missing_points': [], 'reason': '모의 응답', 'cited_clause': ''}
        text = json.dumps(obj, ensure_ascii=False)
        if kind == 'noisy':
            state['n'] += 1
            for d in checks:
                if d.get('field') and state['n'] % 2:
                    d.pop('field')                       # 칸 이름 빠뜨리기
                    break
            text = '판정 결과는 다음과 같습니다.\n```json\n' + json.dumps(obj, ensure_ascii=False) + '\n```'
        usage = {'input_tokens': int(len(system) + len(user)), 'output_tokens': 300}
        return _FakeResult(text, usage, model_key)
    return fn


def summarize(records, cases_by_id):
    """그룹(claude/team/seed)·방식별 정확도, 시험 항목, 오류, 비용."""
    done = [r for r in records if not r.get('skipped')]
    retired = sorted({r['case_id'] for r in done if r['case_id'] not in cases_by_id})   # 서술이 바뀐 옛 사례(예: Q04-P) 결과
    done = [r for r in done if r['case_id'] in cases_by_id]
    out = {'calls': len(done), 'skipped': len(records) - len(done) - sum(1 for r in records if r['case_id'] in retired and not r.get('skipped')),
           'retired_excluded': retired}
    out['json_fail'] = sum(1 for r in done if not r.get('json_ok'))
    out['quote_fail_items'] = sum(1 for r in done for f in (r.get('flags') or []) if '인용' in f or '칸' in f)
    out['truncated'] = sum(1 for r in done if r.get('truncated'))
    out['refusals'] = sum(1 for r in done if r.get('stop_reason') == 'refusal')
    tok = Counter()
    usd = 0.0
    for r in done:
        c = r.get('cost') or {}
        usd += c.get('usd', 0)
        for k, v in (c.get('tokens') or {}).items():
            tok[k] += v
    out['tokens'] = dict(tok)
    out['usd'] = usd
    out['usd_per_call'] = usd / len(done) if done else 0
    out['avg_input_tokens'] = tok['input'] / len(done) if done else 0
    out['avg_output_tokens'] = tok['output'] / len(done) if done else 0

    def provisional(cid):
        pv = cases_by_id[cid].get('provisional')
        return bool(pv) and not pv.get('resolved')

    def pending(cid):                           # 교체 대기 사례(예: Q08-T)는 이전 글의 결과라 정확도에서 뺀다
        return bool(cases_by_id[cid].get('replacement_pending'))

    def group_of(r):                            # 경계 사례(팀 결정 ㄹ)는 원래 그룹에서 빼고 따로 보고한다
        return 'borderline' if cases_by_id[r['case_id']].get('borderline') else (cases_by_id[r['case_id']].get('group') or r.get('group'))

    out['pending_excluded'] = sorted({r['case_id'] for r in done if pending(r['case_id'])})
    out['quote_loose_items'] = sum(1 for r in done for it in (r.get('items') or {}).values() if it.get('quote_loose'))
    out['quote_pieced_items'] = sum(1 for r in done for it in (r.get('items') or {}).values() if it.get('quote_pieced'))
    out['known_limitations'] = sorted({r['case_id'] for r in done if cases_by_id[r['case_id']].get('known_limitation')})
    out['stricter_reading_cases'] = sorted({r['case_id'] for r in done if cases_by_id[r['case_id']].get('stricter_reading')})
    acc = {}
    for r in done:
        if not r.get('compare') or provisional(r['case_id']) or pending(r['case_id']):
            continue
        key = f'{group_of(r)}/{r["mode"]}'
        a = acc.setdefault(key, Counter())
        a['n'] += 1
        a['verdict_ok'] += r['compare']['verdict_match']
        spec = RULES[r['rule_id']]['checks']
        divs = cases_by_id[r['case_id']].get('definition_divergent') or []
        for cid, row in r['compare']['items'].items():
            a['items'] += 1
            a['items_ok'] += row['match']
            if spec[cid] in ('필수', '조건부'):
                a['core'] += 1
                a['core_ok'] += row['match']
                if any(d['item'] == cid and r['mode'] in d['modes'] for d in divs):   # ⑱ 필수 항목의 정의가 갈리는 셀
                    a['core_div'] += 1
                    a['core_div_ok'] += row['match']
                else:
                    a['core_nondiv'] += 1
                    a['core_nondiv_ok'] += row['match']
            else:
                a['opt'] += 1
                a['opt_ok'] += row['match']
                if any(d['item'] == cid and r['mode'] in d['modes'] for d in divs):   # ⑯ 정의가 갈리는 셀
                    a['div'] += 1
                    a['div_ok'] += row['match']
                else:
                    a['nondiv'] += 1
                    a['nondiv_ok'] += row['match']
    ratio = lambda ok, n: ok / n if n else None
    out['accuracy'] = {k: {'n': v['n'], 'verdict': ratio(v['verdict_ok'], v['n']),
                           'core_items': ratio(v['core_ok'], v['core']),
                           'core_nondivergent': ratio(v['core_nondiv_ok'], v['core_nondiv']),
                           'core_divergent_n': v['core_div'],
                           'all_items': ratio(v['items_ok'], v['items']),
                           'optional_items': ratio(v['opt_ok'], v['opt']),
                           'optional_nondivergent': ratio(v['nondiv_ok'], v['nondiv']),
                           'optional_divergent': ratio(v['div_ok'], v['div']), 'divergent_n': v['div']}
                       for k, v in sorted(acc.items())}
    # 함께 주는 칸 시험(B·C)과 칸 위치 시험(Q-07 C4)
    probes = []
    for r in done:
        case = cases_by_id[r['case_id']]
        if not r.get('items'):
            continue
        for cid in case.get('probe_checks') or []:
            probes.append({'case': r['case_id'], 'type': case.get('companion_type'), 'mode': r['mode'], 'check': cid,
                           'expected': case['expected'][r['mode']]['checks'][cid]['status'], 'got': r['items'][cid]['status']})
        ip = case.get('info_probe')
        if ip:
            got = r['items'][ip['item']]
            probes.append({'case': r['case_id'], 'type': '칸 위치', 'mode': r['mode'], 'check': ip['item'],
                           'expected': ip['expect'], 'got': got['status'], 'field': got.get('field')})
    out['probes'] = probes
    mism = []
    for r in done:
        if not r.get('compare'):
            continue
        spec = RULES[r['rule_id']]['checks']
        for cid, row in r['compare']['items'].items():
            if not row['match']:
                it = (r.get('items') or {}).get(cid, {})
                dv = any(d['item'] == cid and r['mode'] in d['modes'] for d in (cases_by_id[r['case_id']].get('definition_divergent') or []))
                mism.append({'case': r['case_id'], 'mode': r['mode'], 'check': cid, 'kind': spec[cid] + (' · 정의가 갈림' if dv else ''),
                             'expected': row['expected'], 'got': row['got'], 'ai_status': row.get('ai_status'),
                             'flag': it.get('flag', ''), 'note': it.get('note', ''), 'evidence': it.get('evidence', ''),
                             'field': it.get('field', '')})
    out['item_mismatches'] = mism
    secs = [r['elapsed_s'] for r in done if r.get('elapsed_s') is not None]
    out['avg_seconds'] = sum(secs) / len(secs) if secs else None
    # 반복 일치율
    rep = defaultdict(list)
    for r in done:
        if r.get('json_ok'):
            rep[(r['case_id'], r['mode'])].append(r)
    multi = {k: v for k, v in rep.items() if len(v) > 1}
    if multi:
        same_v = sum(1 for v in multi.values() if len({x['verdict'] for x in v}) == 1)
        item_tot = item_same = 0
        for v in multi.values():
            for cid in v[0]['items']:
                item_tot += 1
                item_same += len({x['items'][cid]['status'] for x in v}) == 1
        out['repeat'] = {'groups': len(multi), 'verdict_same': same_v / len(multi), 'items_same': item_same / item_tot}
    return out


def summary_md(cfg, s, records):
    pct = lambda x: '-' if x is None else f'{x * 100:.1f}%'
    L = [f'# 정성 검증 AI 판정 결과 — {cfg["label"]}', '',
         f'- 실행: {cfg["started"]} · 모델 {cfg["model_id"]} · 생각 {cfg.get("thinking_sent", cfg["thinking"])} · 노력 {cfg.get("effort_effective", cfg["effort"])} · 방식 {cfg["modes"]} · 반복 {cfg["repeat"]}',
         f'- 기준표 {cfg["rubric_version"]} · 시험 세트 {cfg["testset_updated"]}',
         f'- 호출 {s["calls"]}회 (건너뜀 {s["skipped"]}) · JSON 해석 실패 {s["json_fail"]} · 인용·칸 문제로 처리한 항목 {s["quote_fail_items"]} · max_tokens 잘림 {s["truncated"]} · 거절(refusal) {s.get("refusals", 0)} · 느슨한 인용 일치 {s.get("quote_loose_items", 0)} · 조각 인용 일치 {s.get("quote_pieced_items", 0)}',
         f'- 토큰: 입력 {s["tokens"].get("input", 0):,} · 출력 {s["tokens"].get("output", 0):,} (1회 평균 입력 {s["avg_input_tokens"]:.0f} · 출력 {s["avg_output_tokens"]:.0f})',
         f'- 예상 비용: ${s["usd"]:.4f} (1회 ${s["usd_per_call"]:.4f}). 실제 차감액은 Console 비용 페이지가 기준', '',
         *([f'- 1회 평균 걸린 시간: {s["avg_seconds"]:.1f}초'] if s.get('avg_seconds') else []),
         '', '## 정확도 (그룹/방식)', '', '| 그룹/방식 | 사례 | 최종 판정 일치 | 필수·조건부 항목 일치 | (참고) 정의가 갈리는 셀 제외 |', '|---|---|---|---|---|']
    for k, v in s['accuracy'].items():
        nd = v.get('core_divergent_n', 0)
        L.append(f'| {k} | {v["n"]} | {pct(v["verdict"])} | {pct(v["core_items"])} | ' + (f'{pct(v.get("core_nondivergent"))} (셀 {nd})' if nd else '-') + ' |')
    L += ['', '## 권장 항목 (참고 값 — 정의가 갈리는 셀을 포함하며 최종 판정에 쓰이지 않고 headline도 아님)', '',
          '| 그룹/방식 | 권장 항목 일치(전체) | 정의가 갈리는 셀 제외 | 정의가 갈리는 셀만 (셀 수) | 전체 항목 일치(권장 포함, 참고) |', '|---|---|---|---|---|']
    for k, v in s['accuracy'].items():
        L.append(f'| {k} | {pct(v.get("optional_items"))} | {pct(v.get("optional_nondivergent"))} | {pct(v.get("optional_divergent"))} ({v.get("divergent_n", 0)}) | {pct(v["all_items"])} |')
    L += ['', '정의가 갈리는 셀: 「부족」과 「없음」의 경계가 정의 해석에 따라 갈리는 권장 항목 셀(기준표 규칙 1, Part 4 ⑯). 정답표는 「요구하는 내용 자체에 대한 언급이 없음 = 없음」으로 보며 정답은 바꾸지 않았다. 글자 그대로의 정의에 맞춘 것이며 AI에 맞춘 것이 아니다.']
    L += ['', '그룹: claude = Claude 작성분, team = 팀원 수정분(참고 수준), seed = 팀원용 충분본(따로 표시), new = v1.5 개선 확인용 새 사례(따로 표시), '
          'borderline = 경계 사례(팀 결정 ㄹ: 정답 유지, 따로 보고)']
    if s.get('retired_excluded'):
        L.append(f'서술이 바뀌어 옛 결과를 정확도에서 뺀 사례: {", ".join(s["retired_excluded"])} (새 글로 다시 실행해야 포함)')
    if s.get('pending_excluded'):
        L.append(f'교체 대기로 정확도에서 뺀 사례: {", ".join(s["pending_excluded"])} (새 글로 교체되면 포함)')
    if s.get('stricter_reading_cases'):
        L.append(f'더 엄격한 읽기(참고, 정답 유지) 사례: {", ".join(s["stricter_reading_cases"])} — Opus가 원문으로 방어되는 더 엄격한 읽기를 한 항목(팀 결정 ㉓ (A): 현재 입도 유지, 원문에 맞춰 입도를 올리는 (B)는 PPT 이후 후속 과제)')
    if s.get('known_limitations'):
        L.append(f'알려진 한계 사례(정답 유지, 정확도에 포함): {", ".join(s["known_limitations"])} — 판정 칸이 막연히 가리키면 함께 줘도 이득이 제한됨(규칙 5 근거, 팀 결정 2026-11-13)')
    L.append('')
    if s['probes']:
        L += ['## 시험 항목 (함께 주는 칸 B·C, 칸 위치)', '', '| 사례 | 유형 | 방식 | 항목 | 정답 | AI | 칸 |', '|---|---|---|---|---|---|---|']
        for p in s['probes']:
            L.append(f'| {p["case"]} | {p["type"]} | {p["mode"]} | {p["check"]} | {p["expected"]} | {p["got"]} | {p.get("field", "")} |')
        L.append('')
    if s.get('item_mismatches'):
        L += ['## 항목 불일치 (정답과 다른 칸)', '', '| 사례 | 방식 | 항목 | 구분 | 정답 | AI | 표시·메모·AI 인용 |', '|---|---|---|---|---|---|---|']
        for m in s['item_mismatches']:
            quote = f'인용({m.get("field") or "판정 칸"}): {m["evidence"]}' if m.get('evidence') else ''
            memo = ' · '.join(x for x in (m['flag'], m['note'], quote) if x).replace('|', '/')
            ai = m['got'] + (f' (AI 답 {m["ai_status"]})' if m['ai_status'] and m['ai_status'] != m['got'] else '')
            L.append(f'| {m["case"]} | {m["mode"]} | {m["check"]} | {m["kind"]} | {m["expected"]} | {ai} | {memo[:300]} |')
        L.append('')
    if s.get('repeat'):
        r = s['repeat']
        L += [f'## 반복 일치율', '', f'- 묶음 {r["groups"]}개 · 최종 판정 같음 {pct(r["verdict_same"])} · 항목 상태 같음 {pct(r["items_same"])}', '']
    bad = [r for r in records if not r.get('skipped') and (not r.get('json_ok') or not r.get('compare', {}).get('verdict_match', True) or r.get('flags'))]
    if bad:
        L += ['## 살펴볼 호출', '', '| 사례 | 방식 | 정답 | AI | 표시 |', '|---|---|---|---|---|']
        for r in bad[:60]:
            note = r.get('json_error') or '; '.join(r.get('flags') or [])
            L.append(f'| {r["case_id"]} | {r["mode"]} | {r.get("compare", {}).get("verdict_expected", "")} | {r.get("verdict")} | {note} |')
    return '\n'.join(L) + '\n'


def load_records(run_dir, name='results.jsonl'):
    out = []
    for line in (run_dir / name).read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def write_summary(run_dir, cfg, by_id, results='results.jsonl', stem='summary', only_rep=None):
    records = load_records(run_dir, results)
    if only_rep is not None:                      # 반복 N회차만 따로 집계 (예: low ×3의 1회차를 medium·기본 ×1과 같은 조건으로 비교)
        records = [r for r in records if r.get('rep', 1) == only_rep]
        cfg = dict(cfg, label=f'{cfg.get("label", "")} ({only_rep}회차만)', repeat=1)
    ok = [r for r in records if not r.get('error')]
    s = summarize(ok, by_id)
    s['errors'] = len(records) - len(ok)
    (run_dir / f'{stem}.json').write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')
    (run_dir / f'{stem}.md').write_text(summary_md(cfg, s, ok), encoding='utf-8')
    return s


def rejudge(run_dir, only_rep=None):
    """저장된 AI 답(raw_text)을 지금의 판정 규칙(인용 확인 등)과 시험 세트(정답)로 다시 채점한다. AI를 부르지 않는다.
    원본 results.jsonl은 그대로 두고 results_rejudged.jsonl, summary_rejudged.md·json을 만든다."""
    from corsia.ai.judge import extract_json, evaluate, compare
    data = json.loads(TESTSET.read_text(encoding='utf-8'))
    by_id = {c['case_id']: c for c in data['cases']}
    cfg = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    records = load_records(run_dir)
    out, verdict_changed, item_changed, redone = [], [], 0, 0
    for r in records:
        c = by_id.get(r.get('case_id'))
        if r.get('error') or r.get('skipped') or not r.get('raw_text') or not c:
            out.append(r)
            continue
        values = {**c['inputs'], **(c.get('companions') or {})}
        old_v = r.get('verdict')
        old_items = {k: v.get('status') for k, v in (r.get('items') or {}).items()}
        obj, err = extract_json(r['raw_text'])
        r2 = dict(r)
        if obj is None:
            r2.update(json_ok=False, json_error=err, verdict=None)
            for k in ('items', 'compare', 'flags'):
                r2.pop(k, None)
        else:
            ev = evaluate(c['rule_id'], obj, values, r['mode'])
            r2.update(json_ok=True, verdict=ev['verdict'], items=ev['items'], flags=ev['flags'],
                      missing_points=ev['missing_points'], reason=ev['reason'], cited_clause=ev['cited_clause'])
            r2['compare'] = compare(ev, c['expected'][r['mode']])
            item_changed += sum(1 for k, v in ev['items'].items() if old_items.get(k) != v['status'])
        if old_v != r2.get('verdict'):
            verdict_changed.append((r['case_id'], r['mode'], old_v, r2.get('verdict')))
        r2['group'] = c.get('group')
        r2['rejudged'] = True
        redone += 1
        out.append(r2)
    with open(run_dir / 'results_rejudged.jsonl', 'w', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    cfg2 = dict(cfg, label=cfg.get('label', '') + ' (재채점)', rejudged=True)
    (run_dir / 'config_rejudged.json').write_text(json.dumps(cfg2, ensure_ascii=False, indent=1), encoding='utf-8')
    stem = 'summary_rejudged' + (f'_rep{only_rep}' if only_rep is not None else '')
    s = write_summary(run_dir, cfg2, by_id, results='results_rejudged.jsonl', stem=stem, only_rep=only_rep)
    print(f'다시 채점했습니다: {redone}개 호출 · 항목 상태가 바뀐 곳 {item_changed} · 최종 판정이 바뀐 호출 {len(verdict_changed)}')
    for cid, mode, ov, nv in verdict_changed[:40]:
        print(f'  판정 변경 {cid} {mode}: {ov} → {nv}')
    for k, v in s['accuracy'].items():
        p = lambda x: '-' if x is None else f'{x * 100:.1f}%'
        print(f'  {k}: 사례 {v["n"]} · 최종 판정 {p(v["verdict"])} · 필수·조건부 {p(v["core_items"])} · [참고] 권장 {p(v.get("optional_items"))} (정의가 갈리는 셀 제외 {p(v.get("optional_nondivergent"))})')
    if s.get('pending_excluded'):
        print(f'  교체 대기로 뺀 사례: {", ".join(s["pending_excluded"])}')
    print(f'  결과: {run_dir / (stem + ".md")}')
    return 0


def report(run_dir, only_rep=None):
    """기존 결과 폴더의 summary를 다시 만든다(AI 호출 없음). 지금 시험 세트(정답)로 다시 비교한다.
    only_rep=N이면 결과 파일은 건드리지 않고 N회차 호출만 따로 집계한 summary_repN.md·json을 만든다."""
    data = json.loads(TESTSET.read_text(encoding='utf-8'))
    by_id = {c['case_id']: c for c in data['cases']}
    cfg = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    from corsia.ai.judge import compare
    records = load_records(run_dir)
    for r in records:                        # 정답이 그 뒤에 바뀌었을 수 있어 다시 비교
        if r.get('items') and r['case_id'] in by_id:
            res = {'items': r['items'], 'verdict': r['verdict']}
            r['compare'] = compare(res, by_id[r['case_id']]['expected'][r['mode']])
    if only_rep is None:
        with open(run_dir / 'results.jsonl', 'w', encoding='utf-8') as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        s = write_summary(run_dir, cfg, by_id)
        out_name = 'summary.md'
    else:
        tmp = run_dir / '_results_recompared.jsonl'
        with open(tmp, 'w', encoding='utf-8') as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        s = write_summary(run_dir, cfg, by_id, results='_results_recompared.jsonl', stem=f'summary_rep{only_rep}', only_rep=only_rep)
        tmp.unlink()
        out_name = f'summary_rep{only_rep}.md'
    print(f'요약을 다시 만들었습니다: {run_dir / out_name}')
    for k, v in s['accuracy'].items():
        p = lambda x: '-' if x is None else f'{x * 100:.1f}%'
        print(f'  {k}: 사례 {v["n"]} · 최종 판정 {p(v["verdict"])} · 필수·조건부 {p(v["core_items"])}' + (f' (정의가 갈리는 셀 제외 {p(v.get("core_nondivergent"))})' if v.get('core_divergent_n') else '') + f' · [참고] 권장 {p(v.get("optional_items"))} (정의가 갈리는 셀 제외 {p(v.get("optional_nondivergent"))})')
    for m in s['item_mismatches']:
        print(f'  불일치 {m["case"]} {m["mode"]} {m["check"]}({m["kind"]}): 정답 {m["expected"]} / AI {m["got"]}' + (f' · {m["flag"]}' if m['flag'] else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='정성 검증 시험 세트 AI 판정')
    ap.add_argument('--set', default='pilot')
    ap.add_argument('--mode', default='single', choices=['single', 'together', 'both'])
    ap.add_argument('--model', default='sonnet', choices=list(MODELS))
    ap.add_argument('--thinking', default='default', choices=['default', 'adaptive', 'off', 'between_tools', 'disabled'])
    ap.add_argument('--effort', default='default', choices=['default', 'low', 'medium', 'high'])
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--max-usd', type=float, default=1.0)
    ap.add_argument('--max-tokens', type=int, default=4000)
    ap.add_argument('--label', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mock', choices=['perfect', 'noisy'])
    ap.add_argument('--out', default=str(RUNS))
    ap.add_argument('--report', help='기존 결과 폴더: summary만 다시 만듦(AI 호출 없음)')
    ap.add_argument('--resume', help='끊긴 실행의 결과 폴더: 남은 호출만 이어서')
    ap.add_argument('--rejudge', help='기존 결과 폴더: 저장된 AI 답(raw_text)을 지금의 판정 규칙·시험 세트로 다시 채점(AI 호출 없음, 비용 0). '
                                      'results_rejudged.jsonl·summary_rejudged.md를 새로 만들고 원본은 그대로 둔다')
    ap.add_argument('--append-to', help='기존 결과 폴더에 다른 사례(--set)의 호출을 같은 설정으로 덧붙임(예: 교체한 사례만 다시 실행). '
                                        '모델·생각·노력·방식·반복이 폴더의 설정과 같아야 함. 끝나면 그 폴더의 summary를 전체로 다시 만듦')
    ap.add_argument('--include-pending', action='store_true', help='교체 대기 사례도 포함(기본은 all·claude·team 등에서 뺌)')
    ap.add_argument('--only-rep', type=int, help='--report·--rejudge와 함께: N회차 호출만 따로 집계(summary_repN.md). 반복 ×3 결과의 1회차를 ×1 결과와 같은 조건으로 비교할 때 씀')
    a = ap.parse_args(argv)

    if a.only_rep is not None and not (a.report or a.rejudge):
        raise SystemExit('--only-rep은 --report 또는 --rejudge와 함께 씁니다.')
    if a.report:
        return report(Path(a.report), only_rep=a.only_rep)
    if a.rejudge:
        return rejudge(Path(a.rejudge), only_rep=a.only_rep)

    data = json.loads(TESTSET.read_text(encoding='utf-8'))
    cases = pick_cases(data['cases'], a.set)
    by_id = {c['case_id']: c for c in data['cases']}
    pending_ids = [c['case_id'] for c in cases if c.get('replacement_pending')]
    explicit_ids = a.set not in ('pilot', 'all', 'claude', 'team', 'seed', 'new', 'borderline')
    pending_skipped = []
    if pending_ids and not (a.include_pending or explicit_ids):
        pending_skipped = pending_ids
        cases = [c for c in cases if not c.get('replacement_pending')]
    modes = ['single', 'together'] if a.mode == 'both' else [a.mode]
    rubric = load_rubric()
    thinking = None if a.thinking == 'default' else a.thinking
    effort = None if a.effort == 'default' else a.effort
    try:                                   # 모델이 받지 않는 설정은 호출 전에(dry-run도) 막는다
        thinking_sent = resolve_thinking(a.model, thinking, effort)
    except AIError as e:
        print(f'[멈춤] 설정 오류: {getattr(e, "advice", "") or e}')
        return 2
    mi = MODELS[a.model]
    thinking_txt = (f'{a.thinking}→{thinking_sent}' if a.thinking == 'off' else (thinking_sent or 'adaptive(기본)'))
    effort_txt = effort or (f'{mi.default_effort}(기본)' if mi.default_effort else '기본(확인 안 됨)')

    # 실행 전 추정
    done_before = Counter()
    if a.resume and a.append_to:
        raise SystemExit('[멈춤] --resume과 --append-to는 같이 쓸 수 없습니다.')
    if a.resume or a.append_to:
        run_dir = Path(a.resume or a.append_to)
        old = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
        want = {'modes': ','.join(modes), 'repeat': a.repeat, 'model': a.model,
                'thinking': a.thinking, 'effort': a.effort}
        if a.resume:
            want['set'] = a.set
        diff = {k: (old.get(k), v) for k, v in want.items() if old.get(k) != v}
        if diff:
            raise SystemExit(f'[멈춤] 이어서 할 실행과 설정이 다릅니다: {diff}')
        for line in (run_dir / 'results.jsonl').read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                if not r.get('error'):
                    done_before[(r['case_id'], r['mode'])] += 1

    est_out = est_output_tokens(a.model, thinking, effort)
    jobs, est_usd, est_in = [], 0.0, 0
    for c in cases:
        values = {**c['inputs'], **(c.get('companions') or {})}
        for m in modes:
            if skip_reason(c, values):
                continue
            system, user = build_messages(c['rule_id'], values, m, rubric)
            t_in = int((len(system) + len(user)) * EST_TOKENS_PER_CHAR)
            for rep in range(a.repeat):
                if rep < done_before[(c['case_id'], m)]:
                    continue
                jobs.append((c, m, rep + 1))
                est_in += t_in
                est_usd += estimate_cost(a.model, {'input_tokens': t_in, 'output_tokens': est_out})['usd']
    print(f'정성 검증 AI 판정 · 사례 {len(cases)}개 × 방식 {len(modes)} × 반복 {a.repeat} = 호출 {len(jobs)}회'
          + (f' (이어서: 이미 한 호출 {sum(done_before.values())}회는 건너뜀)' if a.resume else '')
          + (f' (덧붙이기: {run_dir.name}에 추가)' if a.append_to else '') + f' · 모델 {MODELS[a.model].model_id}')
    print(f'  설정: 생각 {thinking_txt} · 노력 {effort_txt}')
    if pending_skipped:
        print(f'  교체 대기 제외: {", ".join(pending_skipped)} (새 글로 교체되면 포함. 넣으려면 --include-pending)')
    print(f'  기준표 {rubric["version"]} · 실행 전 추정: 입력 약 {est_in:,} 토큰, 출력 약 {est_out * len(jobs):,} 토큰, 비용 약 ${est_usd:.3f} (상한 ${a.max_usd:.2f})')
    if a.dry_run:
        print('  (dry-run: AI를 부르지 않음)')
        return 0
    if not a.mock and est_usd > a.max_usd:
        print(f'  [멈춤] 추정 비용이 상한을 넘습니다. 범위를 줄이거나 --max-usd를 올리세요(크레딧 안에서, 팀장 판단).')
        return 2

    if a.mock:
        lookup = {'cases': by_id, 'current': None}
        call_fn = make_mock(a.mock, lookup)
    else:
        from corsia.ai.client import call as call_fn
        lookup = None

    started = datetime.now()
    label = a.label or (f'mock-{a.mock}' if a.mock else f'{a.model}-{a.set}')
    if not (a.resume or a.append_to):
        run_dir = Path(a.out) / f'{started:%Y%m%d-%H%M%S}_{label}'
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        label = old.get('label', label)
    cfg = {'label': label, 'started': started.isoformat(timespec='seconds'), 'set': a.set, 'cases': [c['case_id'] for c in cases],
           'modes': ','.join(modes), 'repeat': a.repeat, 'model': a.model, 'model_id': MODELS[a.model].model_id,
           'thinking': a.thinking, 'effort': a.effort, 'thinking_sent': thinking_txt, 'effort_effective': effort_txt,
           'pending_skipped': pending_skipped, 'max_tokens': a.max_tokens, 'max_usd': a.max_usd,
           'mock': a.mock, 'rubric_version': rubric['version'], 'testset_updated': data['meta'].get('updated', '')}
    if a.resume:
        cfg['started'] = old.get('started', cfg['started'])
        cfg['resumed'] = old.get('resumed', []) + [started.isoformat(timespec='seconds')]
    if a.append_to:
        cfg = dict(old)                                   # 원래 실행의 설정을 그대로 두고 덧붙인 사례만 기록
        cfg['appended'] = old.get('appended', []) + [{'at': started.isoformat(timespec='seconds'), 'set': a.set,
                                                      'cases': [c['case_id'] for c in cases]}]
        cfg['cases'] = old.get('cases', []) + [c['case_id'] for c in cases if c['case_id'] not in old.get('cases', [])]
    (run_dir / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')

    records, total, err_run = [], 0.0, 0
    with open(run_dir / 'results.jsonl', 'a' if (a.resume or a.append_to) else 'w', encoding='utf-8') as fout:
        for i, (c, m, rep) in enumerate(jobs, 1):
            if lookup is not None:
                lookup['current'] = (c['case_id'], m)
            t0 = time.time()
            try:
                rec = judge_case(c, m, call_fn, rubric, model_key=a.model, thinking=thinking, effort=effort,
                                 max_tokens=a.max_tokens)
                rec['rep'] = rep
                rec['elapsed_s'] = round(time.time() - t0, 1)
                err_run = 0
            except Exception as e:                    # API 오류: 키·크레딧·한도·네트워크 등 (client가 안내 문구를 만듦)
                kind = type(e).__name__
                advice = getattr(e, 'advice', '')
                print(f'  [{i}/{len(jobs)}] {c["case_id"]} {m} · 오류: {e} {advice}')
                rec = {'case_id': c['case_id'], 'rule_id': c['rule_id'], 'group': c.get('group'), 'mode': m,
                       'error': f'{kind}: {e}'}
                fout.write(json.dumps(rec, ensure_ascii=False) + '\n')
                fout.flush()
                records.append(rec)
                err_run += 1
                if kind == 'AIError' and any(w in str(e) for w in ('크레딧', '한도', '인증', '키')):
                    print('  [멈춤] 계속해도 같은 오류가 날 오류입니다.')
                    break
                if err_run >= MAX_CONSECUTIVE_ERRORS:
                    print(f'  [멈춤] 오류가 {err_run}회 연달아 났습니다(설정이 이 모델에서 지원되지 않을 수 있음). --resume으로 이어서 할 수 있습니다.')
                    break
                continue
            usd = (rec.get('cost') or {}).get('usd', 0)
            total += usd
            fout.write(json.dumps(rec, ensure_ascii=False) + '\n')
            fout.flush()
            records.append(rec)
            cmp_ = rec.get('compare') or {}
            mark = '' if rec.get('skipped') else ('✓' if cmp_.get('verdict_match') else '✗')
            t = (rec.get('cost') or {}).get('tokens') or {}
            got = rec.get('verdict') if rec.get('json_ok') else ('건너뜀' if rec.get('skipped') else 'JSON 실패')
            print(f'  [{i}/{len(jobs)}] {c["case_id"]} {m}' + (f' #{rep}' if a.repeat > 1 else '') + f' · 정답 {cmp_.get("verdict_expected", "-")} / AI {got} {mark}'
                  f' · 입력 {t.get("input", 0):,} 출력 {t.get("output", 0):,} · ${usd:.4f} (누계 ${total:.4f}) · {rec.get("elapsed_s", 0):.0f}초'
                  + (f' · {len(rec["flags"])}개 항목 표시' if rec.get('flags') else ''))
            if not a.mock and total >= a.max_usd:
                print(f'  [멈춤] 누계 ${total:.4f}가 상한 ${a.max_usd:.2f}에 닿았습니다.')
                break
            if not a.mock:
                time.sleep(0.2)

    if a.append_to:
        # 덧붙인 뒤에는 폴더 안의 모든 호출을 현재 정답으로 다시 비교해 요약을 만든다(그 사이 정답표가 바뀌었어도 반영)
        with contextlib.redirect_stdout(io.StringIO()):
            report(run_dir)
        s = json.loads((run_dir / 'summary.json').read_text(encoding='utf-8'))
    else:
        s = write_summary(run_dir, cfg, by_id)
    print('-' * 64)
    for k, v in s['accuracy'].items():
        p = lambda x: '-' if x is None else f'{x * 100:.0f}%'
        print(f'  {k}: 사례 {v["n"]} · 최종 판정 {p(v["verdict"])} · 필수·조건부 항목 {p(v["core_items"])} · [참고] 권장 항목 {p(v.get("optional_items"))} (정의가 갈리는 셀 제외 {p(v.get("optional_nondivergent"))})')
    print(f'  호출 {s["calls"]} · 오류 {s["errors"]} · JSON 실패 {s["json_fail"]} · 인용·칸 문제 항목 {s["quote_fail_items"]} · 잘림 {s["truncated"]} · 거절 {s.get("refusals", 0)}')
    print(f'  토큰 입력 {s["tokens"].get("input", 0):,} · 출력 {s["tokens"].get("output", 0):,} · 1회 평균 입력 {s["avg_input_tokens"]:.0f} 출력 {s["avg_output_tokens"]:.0f}')
    try:
        where = run_dir.resolve().relative_to(ROOT)
    except ValueError:
        where = run_dir
    print(f'  예상 비용 합계 ${s["usd"]:.4f} (1회 ${s["usd_per_call"]:.4f})'
          + (f' · 1회 평균 {s["avg_seconds"]:.0f}초' if s.get('avg_seconds') else '') + f' · 결과: {where}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
