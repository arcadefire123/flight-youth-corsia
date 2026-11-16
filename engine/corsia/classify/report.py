"""분류 결과 요약, 골든셋 채점 보고서, 오답 원인 분석표, 표본 검수 양식, 채운 카탈로그 사본 (8단계)."""
import json
import warnings
from collections import Counter
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from . import catalog as C
from . import golden as G
from . import rules as R

VARIANT_NAMES = {'rule': '규칙만', 'ai': 'AI만', 'hybrid': '혼합'}
CAUSES = ['원문(항목 글)이 애매함', '판정 기준이 모호함', '조항·근거를 잘못 찾음', '정답이 틀림(재검토)',
          '코드 규칙의 한계', 'AI가 기준을 따르지 않음', '기준 없음(팀 결정 대기)', '기타']
YELLOW = PatternFill('solid', fgColor='FFF2CC')
HEAD = PatternFill('solid', fgColor='D9E1F2')


def load_predictions(run_dir):
    out = {}
    for p in sorted(Path(run_dir).glob('predictions_*.jsonl')):
        v = p.stem.replace('predictions_', '')
        out[v] = {}
        for line in p.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                out[v][r['id']] = r
    return out


def _pct(x):
    return '-' if x is None else f'{x * 100:.1f}%'


def _calls(run_dir):
    p = Path(run_dir) / 'calls.jsonl'
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]


def write_run_summary(run_dir):
    run_dir = Path(run_dir)
    cfg = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    preds = load_predictions(run_dir)
    calls = _calls(run_dir)
    L = [f"# 항목 자동 분류 결과 — {cfg['label']}", '',
         f"- 실행: {cfg['started']} · 세트 {cfg['set']} ({cfg['n_items']}개) · 모델 {cfg['model_id']} · 노력 {cfg['effort']} · 묶음 {cfg['batch']}개 · 템플릿 규칙 {cfg.get('template_rules')}",
         f"- 호출 {sum(1 for c in calls if 'error' not in c)}회 · 오류 {sum(1 for c in calls if 'error' in c)}회 · 예상 비용 ${sum(c.get('usd', 0) for c in calls):.4f} (실제 차감액은 Console 비용 페이지가 기준)",
         f"- JSON 해석 실패 {sum(1 for c in calls if c.get('json_error'))}회 · max_tokens 잘림 {sum(1 for c in calls if c.get('truncated'))}회 · 답이 빠져 다시 물은 호출 {sum(1 for c in calls if c.get('attempt') == 2)}회", '']
    for v, recs in preds.items():
        L += [f'## {VARIANT_NAMES.get(v, v)} ({v}) — 항목 {len(recs)}개', '']
        src = Counter(); flags = Counter(); conf = Counter(); obj = Counter()
        for r in recs.values():
            for a in C.ATTRS:
                src[(a, r['source'].get(a, ''))] += 1
                if r['flags'].get(a):
                    flags[a] += 1
            if r['flags'].get('_줄'):
                flags['_줄'] += 1
                if str(r['flags']['_줄'][0]).startswith('소제목 줄'):
                    flags['_소제목'] += 1
            if r.get('confidence'):
                conf[r['confidence']] += 1
            for a in r.get('objection', {}):
                obj[a] += 1
        L += ['| 속성 | 코드(확정) | 코드(잠정) | AI | 형식 점검에 걸린 항목 |', '|---|---|---|---|---|']
        for a in C.ATTRS:
            fx = src[(a, '코드(확정)')]
            tm = sum(n for (aa, s), n in src.items() if aa == a and s.startswith('코드(잠정'))
            ai = src[(a, 'AI')]
            L.append(f'| {a} | {fx} | {tm} | {ai} | {flags[a]} |')
        if flags['_줄']:
            other = flags['_줄'] - flags['_소제목']
            L += ['', f"**제외 표시** — 소제목 줄 {flags['_소제목']}개(속성을 비움, AI 호출 없음)" + (f" · 템플릿 정보·목차 줄 {other}개(속성이 의미 없는 줄)" if other else '')
                  + f". 속성이 채워진 입력 항목은 {len(recs) - flags['_줄']}개."]
        if conf:
            L += ['', '확신(AI 자기평가): ' + ' · '.join(f'{k} {n}' for k, n in conf.most_common())]
        if obj:
            L += ['', '코드가 정한 값에 AI가 이의를 낸 속성: ' + ' · '.join(f'{k} {n}건' for k, n in obj.most_common()) + ' (규칙이 맞지 않는 곳일 수 있음, 이의 내용은 predictions 파일의 objection)']
        L.append('')
    (run_dir / 'summary.md').write_text('\n'.join(L), encoding='utf-8')
    if str(cfg['set']).startswith('sample:'):
        for v in ('hybrid', 'ai', 'rule'):
            if v in preds:
                make_sample_review_xlsx(run_dir, v)
                break
    return run_dir / 'summary.md'


def score_run(run_dir, golden_paths, exclude=(), label='팀 골든셋(전문가 검토 전)'):
    run_dir = Path(run_dir)
    cfg = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    gold, conflicts = G.read_golden(golden_paths)
    preds = load_predictions(run_dir)
    if not gold:
        print('  골든셋 양식에 채워진 정답이 아직 없어 채점하지 않았습니다.')
        return None
    results, wrongs = {}, {}
    card_cells = C.card_example_cells(cfg.get('card_example_ids') or [])    # 그 실행에서 카드 예시로 쓴 칸만 제외(없으면 제외 없음)
    for v, recs in preds.items():
        pv = {iid: r['values'] for iid, r in recs.items()}
        results[v], wrongs[v] = G.score(pv, gold, exclude=exclude, exclude_cells=card_cells)
    used = [i for i in gold if i in preds.get('rule', next(iter(preds.values()))) and i not in exclude]
    L = [f"# 분류 정확도 — {cfg['label']}", '',
         f'- 채점 기준: **{label}** (골든셋 {len(gold)}건 중 이번 실행에 포함되어 채점된 것 {len(used)}건, 제외 {len(set(exclude) & set(gold))}건)',
         f"- 변형: 규칙만(rule, AI 없음) / AI만(ai) / 혼합(hybrid) · 모델 {cfg['model_id']} · 노력 {cfg['effort']}",
         '- 속성마다 따로 본다(평균 하나로 말하지 않음). 정답이 「기준 없음」인 칸은 아래에서 따로 센다.',
         '- **규칙만**은 판정 기준에서 정해지는 속성이 있어 AI 능력의 증거가 아니다. **혼합**의 코드 몫(주체·단계·시점·입력유형 일부·근거 일부)은 규칙으로 정해진다. AI의 몫은 의무성·연결필드·VR 근거·큰 칸의 입력유형이다.',
         '- 규칙은 견본 5건을 보고 다듬었다(견본은 채점에서 빠져 있음). 골든셋 채점이 규칙의 첫 독립 시험이다.']
    bad_heading = sorted(i for i in gold if R.is_excluded(i) and any(gold[i]['gold'].values()))
    if bad_heading:
        L.append(f'- [주의] 골든셋에 값이 적혀 있는데 소제목 줄로 제외된 항목: ' + ', '.join(bad_heading) + ' — 정답을 비우거나 소제목 목록에서 빼야 채점이 맞음')
    used_cells = sorted(c for c in card_cells if c[0] in gold and gold[c[0]]['gold'].get(c[1]))
    if used_cells:
        L.append(f'- **카드 예시로 쓴 칸 {len(used_cells)}개는 모든 변형의 채점에서 제외**(참고 카드의 OoM 작성 예와 같은 값이라 시험이 되지 않음): '
                 + ', '.join(f'{i}·{a}' for i, a in used_cells))
    if conflicts:
        L.append(f'- [주의] 같은 ID를 파일마다 다르게 쓴 것 {len(conflicts)}건: ' + ', '.join(i for i, _, _ in conflicts[:10]))
    variants = [v for v in ('rule', 'ai', 'hybrid') if v in results]
    L += ['', '## 속성별 정확도 (정답이 있는 칸 기준)', '', '| 속성 | 정답 건수 | ' + ' | '.join(VARIANT_NAMES[v] for v in variants) + ' |', '|---|---|' + '---|' * len(variants)]
    for a in C.ATTRS:
        n = results[variants[0]][a]['n']
        cells = []
        for v in variants:
            r = results[v][a]
            cells.append('-' if not n else f"{r['correct']}/{n} ({_pct(r['accuracy'])})")
        L.append(f'| {a} | {n} | ' + ' | '.join(cells) + ' |')
    if '근거' in results[variants[0]] and results[variants[0]]['근거']['n']:
        L += ['', '근거 부분 일치(조항 하나하나의 겹침 평균, Jaccard): ' + ' · '.join(f"{VARIANT_NAMES[v]} {_pct(results[v]['근거']['jaccard'])}" for v in variants)]
        n_b = results[variants[0]]['근거']['n']
        ex_items = results[variants[0]]['근거']['extra_items']
        ex_gold = results[variants[0]]['근거']['extra_gold']
        L += ['', '근거를 **기본 인용**(규칙이 조립: 국내 서식·별표·조문·법령, OoM은 §2.4.1.6 + 부록 1 + ETM 부록1 번호)과 **문항별 추가 인용**(작성자가 원문을 보고 덧붙인 것, 예: ETM §3.3.4.4)으로 나눠 본다. 분류기는 문서 원문을 보지 못해 추가 인용을 찾을 수 없다.',
              f'- 기본 인용 일치(정답의 기본 인용을 모두 포함하고 정답 밖의 인용이 없음): ' + ' · '.join(f"{VARIANT_NAMES[v]} {results[v]['근거']['base_ok']}/{n_b}" for v in variants),
              f'- 추가 인용이 있는 정답 {ex_items}건(추가 인용 {ex_gold}개) 중 찾은 것: ' + ' · '.join(f"{VARIANT_NAMES[v]} {results[v]['근거']['extra_hit_items']}건, {results[v]['근거']['extra_found']}개" for v in variants)]
    L += ['', '## 정답이 「기준 없음」인 칸 (headline에 섞지 않음)', '', '| 속성 | 건수 | ' + ' | '.join(VARIANT_NAMES[v] + ' 일치' for v in variants) + ' |', '|---|---|' + '---|' * len(variants)]
    any_nr = False
    for a in C.ATTRS:
        n = results[variants[0]][a]['norule_n']
        if n:
            any_nr = True
            L.append(f'| {a} | {n} | ' + ' | '.join(f"{results[v][a]['norule_agree']}/{n}" for v in variants) + ' |')
    if not any_nr:
        L.append('| (없음) | 0 | ' + ' | '.join('-' for _ in variants) + ' |')
    for v in variants:
        L += ['', f'## 틀린 칸 — {VARIANT_NAMES[v]} ({len(wrongs[v])}칸)', '']
        if wrongs[v]:
            L += ['| ID | 속성 | 정답 | 예측 |', '|---|---|---|---|']
            for w in wrongs[v][:60]:
                L.append(f"| {w['id']} | {w['attr']} | {w['gold'][:70]} | {w['pred'][:70]} |")
            if len(wrongs[v]) > 60:
                L.append(f'| … | | | (나머지 {len(wrongs[v]) - 60}칸은 오답원인분석표.xlsx) |')
    (run_dir / 'accuracy_report.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    (run_dir / 'accuracy.json').write_text(json.dumps({'label': label, 'results': results, 'n_gold': len(gold)}, ensure_ascii=False, indent=1), encoding='utf-8')
    make_error_xlsx(run_dir, wrongs, preds)
    print(f"  채점 보고서: {run_dir / 'accuracy_report.md'}")
    for a in C.ATTRS:
        n = results[variants[0]][a]['n']
        if n:
            print(f'    {a}: ' + ' · '.join(f"{VARIANT_NAMES[v]} {results[v][a]['correct']}/{n}" for v in variants))
    return results


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical='top')


def make_error_xlsx(run_dir, wrongs, preds):
    """틀린 칸마다 원인을 고르는 분석표(8단계 순서 6, 팀원이 채움). 같은 틀림은 변형을 묶어 한 줄로."""
    ctx = C.context()
    merged = {}
    for v, ws_ in wrongs.items():
        for w in ws_:
            key = (w['id'], w['attr'], w['gold'], w['pred'])
            merged.setdefault(key, []).append(VARIANT_NAMES[v])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '오답원인분석'
    heads = ['ID', '템플릿', '항목명', '속성', '정답(골든셋)', '예측', '틀린 변형', '원인(목록에서 고름)', '메모']
    ws.append(heads)
    _style_header(ws, len(heads))
    for (iid, attr, gv, pv), vs in sorted(merged.items()):
        it = ctx['by_id'][iid]
        ws.append([iid, it['템플릿'], str(it.get('항목명') or '')[:80], attr, gv, pv, ', '.join(vs), '', ''])
    for col, w in zip('ABCDEFGHI', (9, 10, 40, 10, 38, 38, 18, 26, 30)):
        ws.column_dimensions[col].width = w
    dv = DataValidation(type='list', formula1='"' + ','.join(CAUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'H2:H{max(ws.max_row, 2)}')
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=8).fill = YELLOW
    ws2 = wb.create_sheet('원인 목록')
    ws2.append(['원인 목록(분석표 H열에서 고름)'])
    for c in CAUSES:
        ws2.append([c])
    wb.save(Path(run_dir) / '오답원인분석표.xlsx')


def make_sample_review_xlsx(run_dir, variant, n=None):
    """골든셋 밖의 무작위 표본을 사람이 훑어보는 양식(8단계 순서 8)."""
    ctx = C.context()
    preds = load_predictions(run_dir).get(variant, {})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '표본검수'
    heads = ['ID', '템플릿', '시트', '항목번호', '항목명', '세부라벨', '원문 설명', '국내 대응'] + [f'예측 {a}' for a in C.ATTRS] + ['검수 결과', '틀린 속성', '메모']
    ws.append(heads)
    _style_header(ws, len(heads))
    for iid, r in preds.items():
        it = ctx['by_id'][iid]
        ws.append([iid, it['템플릿'], it['시트'], it.get('항목번호') or '', str(it.get('항목명') or '')[:120], str(it.get('세부라벨') or '')[:120],
                   str(it.get('원문 설명') or '')[:200], ' '.join(str(it.get(k) or '') for k in ('국내 서식', '국내 항목번호', '국내 항목명'))[:100]]
                  + [r['values'].get(a, '') for a in C.ATTRS] + ['', '', ''])
    dv = DataValidation(type='list', formula1='"모두 맞음,일부 틀림,판단 불가"', allow_blank=True)
    ws.add_data_validation(dv)
    col = len(heads) - 2
    dv.add(f'{openpyxl.utils.get_column_letter(col)}2:{openpyxl.utils.get_column_letter(col)}{max(ws.max_row, 2)}')
    for r in range(2, ws.max_row + 1):
        for c in (col, col + 1, col + 2):
            ws.cell(row=r, column=c).fill = YELLOW
    wb.save(Path(run_dir) / '표본검수양식.xlsx')


def export_filled_master(run_dir, variant):
    """field_master.xlsx의 사본에 한 변형의 결과를 채워 저장한다(원본은 건드리지 않음, 견본 5건의 확정 정답은 그대로)."""
    warnings.filterwarnings('ignore')
    preds = load_predictions(run_dir).get(variant)
    if preds is None:
        raise SystemExit(f'이 실행에 {variant} 결과가 없습니다.')
    wb = openpyxl.load_workbook(C.FIELD_MASTER)
    ws = wb['1_field_master']
    head = {c.value: c.column for c in ws[1] if c.value}
    extra = ws.max_column
    ws.cell(row=1, column=extra + 1, value='분류 변형·확신')
    ws.cell(row=1, column=extra + 2, value='검토 표시')
    for row in ws.iter_rows(min_row=2):
        iid = row[0].value
        if iid not in preds or iid in C.EXAMPLE_IDS:
            continue
        r = preds[iid]
        for a in C.ATTRS:
            ws.cell(row=row[0].row, column=head[a], value=r['values'].get(a, ''))
        ws.cell(row=row[0].row, column=extra + 1, value=f"{VARIANT_NAMES.get(variant, variant)} · {r.get('confidence') or '코드'}")
        fl = [(f'{a}: ' if a != '_줄' else '') + '; '.join(f) for a, f in r['flags'].items() if f]
        if str((r['flags'].get('_줄') or [''])[0]).startswith('소제목 줄'):
            ws.cell(row=row[0].row, column=extra + 1, value='제외(소제목 줄)')
            ws.cell(row=row[0].row, column=extra + 2, value=' | '.join(fl)[:250])
            continue
        ws.cell(row=row[0].row, column=extra + 2, value=' | '.join(fl)[:250])
    p = Path(run_dir) / f'field_master_{variant}.xlsx'
    wb.save(p)
    return p
