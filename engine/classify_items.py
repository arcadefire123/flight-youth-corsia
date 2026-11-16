#!/usr/bin/env python
"""8단계 항목 자동 분류 실행기 (규칙 + AI 혼합)

카탈로그(field_master.xlsx)의 항목 속성 7가지(주체·단계·의무성·시점·기한·입력유형·근거·연결필드)를 채운다.
변형 3가지를 같은 항목으로 돌려 따로 채점한다.
  rule   : 코드 규칙만 (AI 호출 없음, 비용 0)
  ai     : AI만 (코드가 정한 값을 주지 않음)
  hybrid : 코드가 정할 수 있는 속성은 코드가, 글을 읽어야 하는 속성만 AI가

쓰는 법 (engine 폴더에서)
  python classify_items.py --set examples --variant rule,hybrid --dry-run         # 견본 5건, 비용 추정만
  python classify_items.py --set golden1 --variant rule,ai,hybrid --label g30       # 골든셋 1차 30건
  python classify_items.py --set all --variant rule,hybrid --label full-hybrid --max-usd 6   # 전체 667개
  python classify_items.py --set sample:30 --variant hybrid --seed 1 --label sample          # 무작위 표본

  --set        examples(견본 5) | golden1(양식의 1차 30) | golden2(2차 20) | golden(50) | all(667) | sample:N | ID,ID,…
  --golden     골든셋을 채운 양식 파일·폴더(여러 개 가능). 주면 실행 뒤 바로 채점하고 보고서를 만든다
  --export-xlsx 변형   field_master의 사본에 그 변형의 결과를 채워 저장(원본은 건드리지 않음, 견본 5건 정답은 유지)
  --batch      한 번 호출에 보낼 항목 수 (기본 10)
  --mock       rule | noisy : 가짜 AI로 흐름만 시험(비용 0)
  --card-examples 참고 카드에 OoM 작성 예(8_classifier/card_examples_v1.json, 골든셋 항목)를 넣음. 기본은 꺼짐(2026-11-16 팀 결정).
               켜면 그 예시 칸은 채점에서 자동 제외되고 실행 설정에 기록됨
  --print-card 판정 기준 카드(AI에게 주는 참고 자료)를 화면에 출력하고 끝냄
  --refresh-rules <결과 폴더>  규칙표(template_rules_v1.json, 예: OoM 기준 확정)가 바뀐 뒤 AI를 다시 부르지 않고 그 폴더의
               rule·hybrid 결과 중 코드 몫(확정값)만 새 규칙으로 고친다(AI가 채운 값은 그대로, 원본은 .before_refresh로 보존). 비용 0
API 키는 환경 변수에서만 읽고 이 코드는 값을 출력하지 않는다. 추가 결제 화면이 보이면 멈추고 팀장에게 알린다.
"""
import argparse
import json
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corsia.ai.models import MODELS, estimate_cost  # noqa: E402
from corsia.classify import catalog as C  # noqa: E402
from corsia.classify import golden as G  # noqa: E402
from corsia.classify import prompt as P  # noqa: E402
from corsia.classify import report as RP  # noqa: E402
from corsia.classify import rules as R  # noqa: E402
from corsia.classify import validate as V  # noqa: E402

OUT_ROOT = C.CLASSIFIER_DIR / 'runs'
FORM = C.ROOT / '6_goldenset' / 'expert_request' / '골든셋_작성양식_v1.xlsx'
EST_OUT_PER_ITEM = 130      # 첫 실제 실행(견본 5건) 실측: 항목당 약 155토큰
EST_OUT_BASE = 300
MAX_CONSECUTIVE_ERRORS = 3


def pick_ids(which, seed=1):
    ctx = C.context()
    allids = [it['ID'] for it in ctx['items']]
    if which == 'examples':
        return list(C.EXAMPLE_IDS)
    if which == 'all':
        return allids
    if which in ('golden1', 'golden2', 'golden'):
        order = {'golden1': '1차', 'golden2': '2차', 'golden': None}[which]
        ids = G.form_ids(FORM, order)
    elif which.startswith('sample:'):
        n = int(which.split(':')[1])
        gold_ids = set(G.form_ids(FORM)) | set(C.EXAMPLE_IDS)
        pool = [i for i in allids if i not in gold_ids and ctx['by_id'][i].get('시트') != 'Template Information']
        rnd = random.Random(seed)
        ids = sorted(rnd.sample(pool, min(n, len(pool))), key=lambda x: ctx['pos'][x])
    else:
        ids = [x.strip() for x in which.split(',') if x.strip()]
    missing = [i for i in ids if i not in ctx['by_id']]
    if missing:
        raise SystemExit(f'카탈로그에 없는 ID: {missing}')
    return ids


def make_batches(ids, size):
    ctx = C.context()
    ordered = sorted(ids, key=lambda i: ctx['pos'][i])      # 같은 템플릿끼리 묶이게
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


# ---- 가짜 AI (비용 없이 흐름 시험) ----
class _Fake:
    def __init__(self, text, usage, model_key):
        self.text, self.stop_reason, self.truncated = text, 'end_turn', False
        self.cost = estimate_cost(model_key, usage)


def make_mock(kind):
    state = {'n': 0}

    def fn(user, model_key='sonnet', system='', max_tokens=0, thinking=None, effort=None):
        state['n'] += 1
        variant_hybrid = '코드가 정한 값(확정' in user
        body = user.split('## 분류할 항목')[1]
        ids = re.findall(r'^\[(F\d{4})\]', body, re.M)
        items = []
        for iid in ids:
            rp = R.rule_predict(iid)
            attrs = [a for a in C.ATTRS if (not rp[a]['fixed'] or not variant_hybrid)]
            vals = {a: rp[a]['value'] for a in attrs}
            items.append({'id': iid, '값': vals, '확신': '보통', '이유': '모의 응답', '이의': {}})
        if kind == 'noisy':
            if state['n'] % 2 == 1 and items:
                first = items[0]['값']
                key = '의무성' if '의무성' in first else next(iter(first))
                first[key] = '모름'                       # 코드값 오류
            if len(items) > 1 and state['n'] % 3 == 1:
                items.pop()                                # 항목 하나 빠뜨림 → 다시 묻기 시험
        text = '결과입니다.\n```json\n' + json.dumps({'items': items}, ensure_ascii=False) + '\n```' if kind == 'noisy' else json.dumps({'items': items}, ensure_ascii=False)
        return _Fake(text, {'input_tokens': int(len(system) + len(user)), 'output_tokens': 300 * max(len(ids), 1)}, model_key)
    return fn


def est_cost(batches, variant, args):
    tin = tout = 0
    for b in batches:
        s, u = P.build_prompt(b, variant)
        tin += int((len(s) + len(u)) * 0.95)
        tout += len(b) * EST_OUT_PER_ITEM + EST_OUT_BASE
    usd = estimate_cost(args.model, {'input_tokens': tin, 'output_tokens': tout})['usd']
    return tin, tout, usd


def parse_batch(obj, ids):
    got = {}
    for x in obj.get('items', []):
        if isinstance(x, dict) and x.get('id') in ids:
            got[x['id']] = x
    return got


def build_record(iid, variant, ai):
    """AI 답(ai, 없으면 None)과 코드 규칙으로 한 항목의 예측 기록을 만든다."""
    rp = R.rule_predict(iid)
    rec = {'id': iid, 'variant': variant, 'values': {}, 'source': {}, 'flags': {}, 'confidence': '', 'reason': '', 'objection': {},
           'example': iid in C.EXAMPLE_IDS, 'template': C.context()['by_id'][iid]['템플릿']}
    if R.is_excluded(iid):                                  # 소제목 줄(합의 안건 12): 속성을 채우지 않고 AI도 부르지 않음
        for a in C.ATTRS:
            rec['values'][a], rec['source'][a] = '', '코드(확정)'
        rec['flags']['_줄'] = ['소제목 줄(제외 권장, 합의 안건 12): 속성을 채우지 않음']
        return rec
    if variant == 'rule':
        for a in C.ATTRS:
            rec['values'][a] = V.norm_value(a, rp[a]['value'])
            rec['source'][a] = '코드(확정)' if rp[a]['fixed'] else '코드(잠정)'
        it = C.context()['by_id'][iid]
        if it.get('시트') == 'Template Information':
            rec['flags']['_줄'] = ['템플릿 정보 줄(제공자·버전 등): 속성이 의미 없는 줄, 채점·집계에서 제외 권장']
        elif str(it.get('매핑') or '').startswith('회의 결정 9'):
            rec['flags']['_줄'] = ['목차(섹션 제목) 줄(판정 기준 9): 항목이 아니라 섹션 제목 대응, 속성 판정 대상인지 팀 확인']
        return rec
    it = C.context()['by_id'][iid]
    if it.get('시트') == 'Template Information':
        rec['flags']['_줄'] = ['템플릿 정보 줄(제공자·버전 등): 속성이 의미 없는 줄, 채점·집계에서 제외 권장']
    elif str(it.get('매핑') or '').startswith('회의 결정 9'):
        rec['flags']['_줄'] = ['목차(섹션 제목) 줄(판정 기준 9): 항목이 아니라 섹션 제목 대응, 속성 판정 대상인지 팀 확인']
    vals = (ai or {}).get('값', {}) if isinstance((ai or {}).get('값', {}), dict) else {}
    for a in C.ATTRS:
        if variant == 'hybrid' and rp[a]['fixed']:
            rec['values'][a] = V.norm_value(a, rp[a]['value'])
            rec['source'][a] = '코드(확정)'
            continue
        if a in vals and str(vals[a]).strip():
            rec['values'][a] = V.norm_value(a, vals[a])
            rec['source'][a] = 'AI'
            fl = V.check_value(iid, a, vals[a])
            if a == '입력유형':                                  # 칸 크기와 어긋나는 AI 답을 표시(형식 오류는 아님, 사람이 확인)
                cell = C.template_cells().get(iid, {})
                w, h = cell.get('merged_w', 1), cell.get('merged_h', 1)
                if rec['values'][a] == '서술' and w * h < 12 and not cell.get('dropdown'):
                    fl = fl + [f'입력유형: 서술인데 칸이 작음({w}×{h}), 짧은 식별 칸을 서술로 채운 것인지 확인']
                elif rec['values'][a] == '텍스트' and w >= 3 and w * h >= 12 and not cell.get('dropdown'):
                    fl = fl + [f'입력유형: 텍스트인데 칸이 큼({w}×{h}), 설명을 쓰는 칸인지 확인']
            if fl:
                rec['flags'][a] = fl
        elif variant == 'hybrid':
            rec['values'][a] = V.norm_value(a, rp[a]['value'])
            rec['source'][a] = '코드(잠정, AI 답 없음)'
            rec['flags'][a] = ['AI 답 없음 → 코드 잠정값']
        else:
            rec['values'][a] = ''
            rec['source'][a] = 'AI 답 없음'
            rec['flags'][a] = ['AI 답 없음']
    if ai:
        rec['confidence'] = str(ai.get('확신', ''))
        rec['reason'] = str(ai.get('이유', ''))[:300]
        if isinstance(ai.get('이의'), dict) and ai['이의']:
            rec['objection'] = {k: str(v)[:200] for k, v in ai['이의'].items()}
    return rec


def run_variant(variant, ids, args, run_dir, call_fn):
    """한 변형을 돌려 predictions_<variant>.jsonl 에 저장하고 (기록 목록, 비용 합계, 호출 수) 를 돌려준다."""
    out_path = run_dir / f'predictions_{variant}.jsonl'
    calls_path = run_dir / 'calls.jsonl'
    recs = {}
    if variant == 'rule':
        for iid in ids:
            recs[iid] = build_record(iid, 'rule', None)
        out_path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in recs.values()) + '\n', encoding='utf-8')
        return list(recs.values()), 0.0, 0
    done = {}
    if args.resume and out_path.exists():
        for line in out_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                done[r['id']] = r
    todo = [i for i in ids if i not in done]
    skipped = [i for i in todo if R.is_excluded(i)]          # 소제목 줄은 AI를 부르지 않고 바로 기록
    todo = [i for i in todo if i not in skipped]
    batches = make_batches(todo, args.batch)
    total, ncalls, errors = 0.0, 0, 0
    with open(out_path, 'a' if done else 'w', encoding='utf-8') as fout, open(calls_path, 'a', encoding='utf-8') as fcalls:
        for iid in skipped:
            r = build_record(iid, variant, None)
            recs[iid] = r
            fout.write(json.dumps(r, ensure_ascii=False) + '\n')
        for bi, b in enumerate(batches, 1):
            pending, got, attempt = list(b), {}, 0
            while pending and attempt < 2:          # 빠진 항목은 한 번만 다시 묻는다
                attempt += 1
                system, user = P.build_prompt(pending, variant)
                t0 = time.time()
                try:
                    res = call_fn(user, model_key=args.model, system=system, max_tokens=args.max_tokens,
                                  thinking=args.thinking, effort=args.effort)
                except Exception as exc:                # AIError 등
                    errors += 1
                    fcalls.write(json.dumps({'variant': variant, 'batch': bi, 'ids': pending, 'error': str(exc)[:200]}, ensure_ascii=False) + '\n')
                    print(f'  [{variant} {bi}/{len(batches)}] 오류: {exc}')
                    if errors >= MAX_CONSECUTIVE_ERRORS:
                        print('  [멈춤] 같은 오류가 연달아 났습니다. 설정을 확인하세요.')
                        return list(done.values()) + list(recs.values()), total, ncalls
                    break
                errors = 0
                ncalls += 1
                usd = res.cost.get('usd', 0.0) if isinstance(res.cost, dict) else 0.0
                total += usd
                obj, err = P.extract_items_json(res.text)
                parsed = parse_batch(obj, set(pending)) if obj else {}
                fcalls.write(json.dumps({'variant': variant, 'batch': bi, 'attempt': attempt, 'ids': pending, 'usd': usd,
                                         'tokens': res.cost.get('tokens') if isinstance(res.cost, dict) else None,
                                         'truncated': bool(getattr(res, 'truncated', False)), 'json_error': err,
                                         'answered': len(parsed), 'seconds': round(time.time() - t0, 1)}, ensure_ascii=False) + '\n')
                got.update(parsed)
                pending = [i for i in pending if i not in parsed]
                if pending and attempt < 2:
                    print(f'  [{variant} {bi}/{len(batches)}] 답이 빠진 항목 {len(pending)}개 → 한 번 다시 묻습니다')
            for iid in b:
                r = build_record(iid, variant, got.get(iid))
                recs[iid] = r
                fout.write(json.dumps(r, ensure_ascii=False) + '\n')
            fout.flush()
            print(f'  [{variant} {bi}/{len(batches)}] 항목 {len(b)}개 · 누계 ${total:.4f}')
    return list(done.values()) + list(recs.values()), total, ncalls


def refresh_rules(run_dir):
    """규칙표가 바뀐 뒤 코드 몫을 다시 채운다(AI 호출 없음). rule은 전체, hybrid는 코드가 확정하는 속성만 새 규칙값으로 바꾼다.
    AI가 채운 값은 건드리지 않는다. 예전에 코드가 확정했는데 이제 확정이 아니게 된 속성은 값을 두고 「AI 판단 필요」 표시를 붙인다."""
    run_dir = Path(run_dir)
    cfg_path = run_dir / 'config.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    report = {}
    for p in sorted(run_dir.glob('predictions_*.jsonl')):
        v = p.stem.replace('predictions_', '')
        if v not in ('rule', 'hybrid'):
            continue
        bak = p.with_name(p.name + '.before_refresh')
        if not bak.exists():
            shutil.copy(p, bak)
        recs = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
        changed, need_ai = {}, 0
        for r in recs:
            if R.is_excluded(r['id']):                      # 소제목 줄로 확정된 항목: 속성을 비우고 AI 결과(확신·이유·이의)도 지움
                for a in C.ATTRS:
                    if r['values'].get(a):
                        changed[a] = changed.get(a, 0) + 1
                    r['values'][a], r['source'][a] = '', '코드(확정)'
                r['flags'] = {'_줄': ['소제목 줄(제외 권장, 합의 안건 12): 속성을 채우지 않음']}
                r['confidence'], r['reason'], r['objection'] = '', '', {}
                continue
            rp = R.rule_predict(r['id'])
            for a in C.ATTRS:
                use = (v == 'rule') or rp[a]['fixed']
                if use:
                    new = V.norm_value(a, rp[a]['value'])
                    src = '코드(확정)' if rp[a]['fixed'] else '코드(잠정)'
                    if r['values'].get(a) != new:
                        changed[a] = changed.get(a, 0) + 1
                    r['values'][a], r['source'][a] = new, src
                    if r['flags'].get(a) and src.startswith('코드'):
                        r['flags'].pop(a)
                elif str(r['source'].get(a, '')).startswith('코드(확정)'):
                    r['flags'][a] = ['규칙이 바뀌어 이제 AI 판단이 필요(이 항목만 다시 실행)']
                    need_ai += 1
        p.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in recs) + '\n', encoding='utf-8')
        report[v] = (changed, need_ai)
    cfg.setdefault('refreshed', []).append({'at': datetime.now().isoformat(timespec='seconds'), 'template_rules': C.template_rules().get('_버전'),
                                           'changed': {v: sum(c.values()) for v, (c, _n) in report.items()}})
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    RP.write_run_summary(run_dir)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description='8단계 항목 자동 분류 실행기 (규칙 + AI 혼합)')
    ap.add_argument('--set', default='examples')
    ap.add_argument('--variant', default='rule,hybrid')
    ap.add_argument('--model', default='sonnet', choices=list(MODELS))
    ap.add_argument('--thinking', default='adaptive')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--batch', type=int, default=10)
    ap.add_argument('--max-tokens', type=int, default=8000)
    ap.add_argument('--max-usd', type=float, default=6.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--label', default='')
    ap.add_argument('--out', default=str(OUT_ROOT))
    ap.add_argument('--golden', nargs='*', help='골든셋을 채운 양식 파일·폴더')
    ap.add_argument('--exclude', default='', help='채점에서 뺄 ID(예시로 쓴 항목 등), 쉼표로 구분')
    ap.add_argument('--golden-label', default='팀 골든셋(전문가 검토 전)')
    ap.add_argument('--export-xlsx', default='')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mock', choices=['rule', 'noisy'])
    ap.add_argument('--print-card', action='store_true')
    ap.add_argument('--refresh-rules', metavar='RUN_DIR')
    ap.add_argument('--card-examples', action='store_true')
    a = ap.parse_args(argv)
    C.set_card_examples(a.card_examples)                  # 기본 꺼짐: 카드에 문구만 들어가고 골든셋 항목 예시는 들어가지 않음

    if a.refresh_rules:
        rep = refresh_rules(a.refresh_rules)
        for v, (changed, need_ai) in rep.items():
            tot = sum(changed.values())
            print(f'  {v}: 코드 몫 {tot}칸 갱신' + (' (' + ' · '.join(f'{k} {n}' for k, n in changed.items()) + ')' if tot else '')
                  + (f' · AI 판단이 새로 필요한 칸 {need_ai}' if need_ai else ''))
        print(f'  AI 호출 없음(비용 0). 원본은 predictions_*.jsonl.before_refresh 로 보존, summary.md 다시 만듦')
        return 0

    if a.print_card:
        card = P.reference_card()
        print(card)
        print(f'\n(글자 {len(card):,}, 약 {int(len(card) * 0.95):,} 토큰)')
        return 0
    variants = [v.strip() for v in a.variant.split(',') if v.strip()]
    bad = [v for v in variants if v not in ('rule', 'ai', 'hybrid')]
    if bad:
        raise SystemExit(f'모르는 변형: {bad} (rule, ai, hybrid)')
    ids = pick_ids(a.set, a.seed)
    n_head = sum(1 for i in ids if R.is_excluded(i))
    batches = make_batches([i for i in ids if not R.is_excluded(i)], a.batch)       # 소제목 줄은 AI를 부르지 않음
    print(f'항목 분류 · 세트 {a.set} ({len(ids)}개' + (f', 그중 소제목 줄 {n_head}개는 AI 제외' if n_head else '') + f') · 변형 {",".join(variants)} · 모델 {MODELS[a.model].model_id} · 노력 {a.effort} · 묶음 {a.batch}개씩')
    est_total = 0.0
    for v in variants:
        if v == 'rule':
            print('  rule: AI를 부르지 않음(비용 0)')
            continue
        tin, tout, usd = est_cost(batches, v, a)
        est_total += usd
        print(f'  {v}: 호출 {len(batches)}회 · 입력 약 {tin:,} · 출력 약 {tout:,} 토큰 · 비용 약 ${usd:.3f}')
    print(f'  합계 추정 ${est_total:.3f} (상한 ${a.max_usd:.2f})')
    if a.dry_run:
        print('  (dry-run: AI를 부르지 않음)')
        return 0
    if not a.mock and est_total > a.max_usd:
        print('  [멈춤] 추정 비용이 상한을 넘습니다. 범위를 줄이거나 --max-usd를 올리세요(크레딧 안에서, 팀장 판단).')
        return 2
    if a.mock:
        call_fn = make_mock(a.mock)
    elif any(v != 'rule' for v in variants):
        from corsia.ai.client import call as call_fn
    else:
        call_fn = None

    started = datetime.now()
    label = a.label or (f'mock-{a.mock}' if a.mock else f'{a.set}')
    run_dir = Path(a.out) / f'{started:%Y%m%d-%H%M%S}_{label}'
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = {'label': label, 'started': started.isoformat(timespec='seconds'), 'set': a.set, 'n_items': len(ids), 'ids': ids,
           'variants': variants, 'model': a.model, 'model_id': MODELS[a.model].model_id, 'thinking': a.thinking, 'effort': a.effort,
           'batch': a.batch, 'max_usd': a.max_usd, 'mock': a.mock, 'seed': a.seed,
           'template_rules': C.template_rules().get('_버전'), 'golden_label': a.golden_label,
           'card_example_ids': [e['id'] for e in C.card_examples()]}
    (run_dir / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    grand, calls = 0.0, 0
    for v in variants:
        print(f'\n[{v}] 시작')
        recs, usd, n = run_variant(v, ids, a, run_dir, call_fn)
        grand += usd
        calls += n
    cfg.update({'cost_usd': round(grand, 4), 'calls': calls})
    (run_dir / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n완료 · 호출 {calls}회 · 예상 비용 ${grand:.4f} · 결과: {run_dir}')
    RP.write_run_summary(run_dir)
    if a.golden:
        exclude = [x.strip() for x in a.exclude.split(',') if x.strip()]
        RP.score_run(run_dir, a.golden, exclude=exclude, label=a.golden_label)
    if a.export_xlsx:
        p = RP.export_filled_master(run_dir, a.export_xlsx)
        print(f'  채운 사본: {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
