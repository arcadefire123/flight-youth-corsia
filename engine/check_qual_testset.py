"""
7단계 D - 정성 검증 시험 세트 점검

    engine 폴더에서:  python check_qual_testset.py

시험 세트(7_qualitative/qual_testset_v1.json)의 정답표가 기준표와 맞는지 기계적으로 확인한다.
AI를 부르지 않으므로 비용이 들지 않는다.

  1. 항목 목록이 기준표(corsia/ai/rubric.py)와 같은지
  2. 최종 판정이 공통 규칙 2로 계산한 값과 같은지 (두 방식 모두)
  3. 「있음」「부족」의 인용문이 실제로 그 칸에 있는지, 인용한 칸이 그 방식에서 주는 칸인지
  4. 조건부 항목(Q-04 방법별, Q-07 의견부 만족)의 「해당 없음」이 맞는지
  5. 두 방식의 정답이 다른 경우는 B 유형(함께 준 칸에만 정보)뿐인지
  6. 시험 문장에 기준표의 「부족」 예시 문장이나 정상본 서술이 그대로 들어 있지 않은지
  7. 팀원이 고칠 충분본이 8개(규칙마다 1개)이고 모두 충분인지
  8. 팀원 수정분: 원본 충분본이 있고 글이 실제로 바뀌었는지, 건드린 항목이 의도한 상태인지
  9. 잠정 표시(provisional): 현재 정답과 대안 정답이 규칙 2 계산과 맞는지, 대안이 실제로 판정을 바꾸는지
 10. 칸 위치 시험(info_probe, Q-07 C4): 판정 칸에는 없고 발견사항 칸에만 있는 사례가 정답 「있음」+근거 칸 이름,
     두 칸 모두 없는 사례가 「부족」인지. 항목별로 정한 근거 칸(rubric.EVIDENCE_FIELDS) 밖을 인용하지 않았는지
 11. 지시문 조립(prompts.py): 모든 사례를 두 방식으로 조립해 칸 머리표가 그 방식에서 주는 칸과 같은지,
     규칙별 안내 문구(RULE_NOTES: Q-04 C3·C4 측정 변수별, Q-07 C4 근거 칸)가 그 규칙에만 있는지
 12. 기준표 문서(Part 2)를 읽은 항목 목록·구분이 rubric.py와 같은지 (E 지시문은 기준표 문서에서 바로 만듦)
 13. 경계 사례(borderline)·교체 대기(replacement_pending) 표시: 팀 정답이 시험 세트 정답과 같은지, 이전 결과 기록이 있는지
 14. 함께 주는 정보로 정한 칸(rubric.RULES의 info)이 그 규칙의 모든 사례 입력에 들어 있는지
 15. 알려진 한계(known_limitation) 표시: 방식·결정일·설명이 있고, 해당 방식의 정답이 표시한 사례의 시험 의도와 맞는지
 16. 정의가 갈리는 셀(definition_divergent, 기준표 Part 4 ⑯·⑱): 항목이 그 규칙에 있고, 표시한 방식마다 정답이 팀 정답과 같은지
 17. 교체된 옛 사례(meta.retired): 새 사례가 있고 옛 사례 ID는 시험 세트에 남아 있지 않은지
 18. 더 엄격한 읽기(stricter_reading, ㉓ (A)): 표시한 항목이 그 규칙에 있고 결정 기록이 있는지
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corsia.ai.rubric import RULES, compute_verdict, conditional_applies, evidence_fields, STATUSES   # noqa: E402
from corsia.ai.rubric_text import load_rubric   # noqa: E402
from corsia.ai.prompts import build_instructions, build_input_block, fields_given, RULE_NOTES, MODES   # noqa: E402

ROOT = HERE.parent
TESTSET = ROOT / '7_qualitative' / 'qual_testset_v1.json'
RUBRIC = ROOT / '7_qualitative' / 'qualitative_rubric_v1.md'
NARRATIVE_FILES = [HERE / 'dataset' / 'documents.json', HERE / 'dataset' / 'documents_dirty.json']
NARRATIVE_KEYS = [('EMP', 'data_management'), ('EMP', 'data_gap_procedure'), ('EMP', 'route_determination'),
                  ('EMP', 'measurement_timing'), ('EMP', 'risk_assessment'), ('ER', 'data_gap_explanation'),
                  ('VR', 'verification_opinion'), ('VR', 'findings_and_actions')]


def rubric_examples():
    """기준표에서 「부족」 예시로 든 따옴표 문장을 모은다."""
    out = set()
    for line in RUBRIC.read_text(encoding='utf-8').splitlines():
        if line.startswith('- 「부족」의 예') or line.startswith('| 부족 |') or line.startswith('- C3 「없음」'):
            for q in re.findall(r'「([^」]{4,})」', line):
                out.add(q)
    return sorted(out)


def narrative_sentences():
    out = set()
    for f in NARRATIVE_FILES:
        if not f.exists():
            continue
        docs = json.loads(f.read_text(encoding='utf-8')).get('documents', {})
        for d, k in NARRATIVE_KEYS:
            v = (docs.get(d) or {}).get(k) or ''
            for s in re.split(r'(?<=[.다])\s+', v):
                s = s.strip()
                if len(s) >= 20:
                    out.add(s)
    return out


def texts_of(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def main():
    data = json.loads(TESTSET.read_text(encoding='utf-8'))
    cases = data['cases']
    errors, warns = [], []
    prov, resolved, pending_ids, known_ids, div_cells, strict_ids = [], [], [], [], [], []
    examples = rubric_examples()
    sentences = narrative_sentences()
    seen_texts = defaultdict(list)

    for c in cases:
        cid, rule = c['case_id'], c['rule_id']
        spec = RULES.get(rule)
        if not spec:
            errors.append(f'{cid}: 모르는 규칙 {rule}')
            continue
        if c['judged_field'] != spec['field'] or not (c['inputs'].get(spec['field']) or '').strip():
            errors.append(f'{cid}: 판정 칸 {spec["field"]}이 없거나 비어 있음')
        _others = {k: v for k, v in c['inputs'].items() if k != spec['field']}
        seen_texts[c['inputs'].get(spec['field'])].append(
            (cid, json.dumps({'c': c.get('companions'), 'i': _others}, ensure_ascii=False, sort_keys=True)))
        if set(c.get('companions') or {}) - set(spec['companions']):
            errors.append(f'{cid}: 규칙 5 표에 없는 함께 주는 칸 {set(c["companions"]) - set(spec["companions"])}')

        base_fields = {spec['field'], *spec.get('info', [])}
        for mode in ('single', 'together'):
            exp = c['expected'][mode]
            checks = exp['checks']
            if list(checks) != list(spec['checks']):
                errors.append(f'{cid}/{mode}: 항목 목록이 기준표와 다름 {list(checks)}')
                continue
            try:
                v = compute_verdict(rule, {k: x['status'] for k, x in checks.items()})
            except ValueError as e:
                errors.append(f'{cid}/{mode}: {e}')
                continue
            if v != exp['verdict']:
                errors.append(f'{cid}/{mode}: 최종 판정 {exp["verdict"]} ≠ 규칙 2 계산 {v}')
            allowed = base_fields | (set(c.get('companions') or {}) if mode == 'together' else set())
            pool = {**c['inputs'], **(c.get('companions') or {})}
            for k, x in checks.items():
                st = x['status']
                if st not in STATUSES:
                    errors.append(f'{cid}/{mode}/{k}: 모르는 상태 {st}')
                if st in ('있음', '부족'):
                    ev, fld = x.get('evidence'), x.get('field') or spec['field']
                    if not ev:
                        errors.append(f'{cid}/{mode}/{k}: {st}인데 인용 없음')
                    elif fld not in allowed:
                        errors.append(f'{cid}/{mode}/{k}: 이 방식에서 주지 않는 칸({fld})을 인용')
                    elif evidence_fields(rule, k) is not None and fld not in evidence_fields(rule, k):
                        errors.append(f'{cid}/{mode}/{k}: 이 항목의 근거를 찾을 수 있는 칸({evidence_fields(rule, k)})이 아님: {fld}')
                    elif not any(ev in t for t in texts_of(pool.get(fld))):
                        errors.append(f'{cid}/{mode}/{k}: 인용문이 {fld}에 없음: {ev[:30]}')
                elif x.get('evidence'):
                    errors.append(f'{cid}/{mode}/{k}: {st}인데 인용이 있음')
                for ex in x.get('evidence_also') or []:
                    if mode != 'together':
                        errors.append(f'{cid}/{mode}/{k}: 추가 인용(evidence_also)은 함께 줌 방식에서만 씀')
                    elif ex['field'] not in allowed:
                        errors.append(f'{cid}/{mode}/{k}: 추가 인용의 칸({ex["field"]})을 이 방식에서 주지 않음')
                    elif not any(ex['evidence'] in t for t in texts_of(pool.get(ex['field']))):
                        errors.append(f'{cid}/{mode}/{k}: 추가 인용문이 {ex["field"]}에 없음: {ex["evidence"][:30]}')
                # 조건부 항목
                if spec['checks'][k] == '조건부':
                    app = conditional_applies(rule, k, method=c.get('monitoring_method'),
                                              opinion=c.get('opinion_stated'),
                                              has_findings=c.get('findings_present'))
                    if app is True and st == '해당 없음':
                        errors.append(f'{cid}/{mode}/{k}: 조건이 맞는데 해당 없음')
                    if app is False and st != '해당 없음':
                        errors.append(f'{cid}/{mode}/{k}: 조건이 안 맞는데 {st}')
                    if app is None and st not in ('판단 불가', '있음', '부족', '없음'):
                        errors.append(f'{cid}/{mode}/{k}: 조건을 모르는데 {st}')
        # 두 방식 차이
        diff = [k for k in spec['checks']
                if c['expected']['single']['checks'][k] != c['expected']['together']['checks'][k]]
        if diff and c.get('companion_type') != 'B':
            errors.append(f'{cid}: 두 방식 정답이 다른데({diff}) B 유형이 아님')
        if c.get('companion_type') == 'B' and not diff:
            errors.append(f'{cid}: B 유형인데 두 방식 정답이 같음')
        probes = c.get('probe_checks') or []
        if c.get('companion_type') in ('B', 'C') and not probes:
            errors.append(f'{cid}: B·C 유형인데 probe_checks 없음')
        for k in probes:
            s1 = c['expected']['single']['checks'][k]['status']
            s2 = c['expected']['together']['checks'][k]['status']
            if s1 not in ('없음', '부족'):
                errors.append(f'{cid}/{k}: 시험 항목인데 한 칸만 정답이 {s1}')
            if c['companion_type'] == 'B' and s2 != '있음':
                errors.append(f'{cid}/{k}: B 유형 시험 항목인데 함께 줌 정답이 {s2}')
            if c['companion_type'] == 'C' and s2 != s1:
                errors.append(f'{cid}/{k}: C 유형 시험 항목인데 두 방식 정답이 다름')
        for k in diff:
            if c['expected']['together']['checks'][k].get('field') not in spec['companions']:
                errors.append(f'{cid}/{k}: 함께 줌 정답의 인용이 함께 준 칸이 아님')
        # 금지 문장
        all_text = ' '.join(t for v in {**c['inputs'], **(c.get('companions') or {})}.values() for t in texts_of(v))
        for ex in examples:
            if ex in all_text:
                errors.append(f'{cid}: 기준표 「부족」 예시 문장을 그대로 씀: {ex}')
        for s in sentences:
            if s in all_text:
                errors.append(f'{cid}: 정상본·오류본 서술 문장을 그대로 씀: {s[:30]}')

    for t, items in seen_texts.items():
        comps = [cp for _, cp in items]
        if len(items) > 1 and len(set(comps)) < len(comps):
            errors.append(f'판정 칸과 함께 준 칸이 모두 같은 사례: {[i for i, _ in items]}')
        elif len(items) > 1:
            warns.append(f'판정 칸이 같고 함께 준 칸만 다른 짝(의도된 비교): {[i for i, _ in items]}')

    by_id = {c['case_id']: c for c in cases}
    for c in cases:
        if c.get('author') != 'team':
            continue
        src = by_id.get(c.get('source_case'))
        if not src or not src.get('seed_for'):
            errors.append(f'{c["case_id"]}: 원본 충분본({c.get("source_case")})이 없음')
            continue
        f = c['judged_field']
        if c['inputs'][f] == src['inputs'][f]:
            errors.append(f'{c["case_id"]}: 원본과 글이 같음')
        if {k: v for k, v in c['inputs'].items() if k != f} != {k: v for k, v in src['inputs'].items() if k != f} \
                or c.get('companions') != src.get('companions'):
            errors.append(f'{c["case_id"]}: 판정 칸 밖(함께 주는 정보·칸)이 원본과 다름')
        t = c.get('team') or {}
        got = c['expected']['single']['checks'].get(t.get('touched_item'), {}).get('status')
        want = t.get('decided_state') or t.get('intended_state')
        if got != want:
            errors.append(f'{c["case_id"]}: 건드린 항목 {t.get("touched_item")}의 정답 {got} ≠ {"팀 결정" if t.get("decided_state") else "의도"} {want}')
        if t.get('decided_state') and not t.get('decision_record'):
            errors.append(f'{c["case_id"]}: 팀 결정으로 바뀐 정답에 기록(decision_record)이 없음')

    for c in cases:
        pv = c.get('provisional')
        if not pv:
            continue
        sts = {k: x['status'] for k, x in c['expected']['single']['checks'].items()}
        if sts.get(pv['item']) != pv['current_status']:
            errors.append(f'{c["case_id"]}: 잠정 항목 {pv["item"]}의 현재 정답이 {sts.get(pv["item"])}')
        alt = dict(sts)
        alt[pv['item']] = pv['alt_status']
        v_alt = compute_verdict(c['rule_id'], alt)
        if v_alt != pv['alt_verdict']:
            errors.append(f'{c["case_id"]}: 잠정 대안 판정 {pv["alt_verdict"]} ≠ 계산 {v_alt}')
        if v_alt == c['expected']['single']['verdict']:
            warns.append(f'{c["case_id"]}: 잠정 항목이 최종 판정에는 영향이 없음(항목 상태만 잠정)')
        if pv.get('resolved'):
            if not pv.get('resolution'):
                errors.append(f'{c["case_id"]}: 잠정 해소인데 해소 내용(resolution)이 없음')
            resolved.append(c['case_id'])
        else:
            prov.append(c['case_id'])

    probes_ip = []
    for c in cases:
        ip = c.get('info_probe')
        if not ip:
            continue
        spec = RULES[c['rule_id']]
        jf, inf = spec['field'], ip['info_field']
        if inf not in spec.get('info', []):
            errors.append(f'{c["case_id"]}: info_probe의 칸 {inf}이 이 규칙의 함께 주는 정보가 아님')
            continue
        tj, ti = c['inputs'].get(jf) or '', c['inputs'].get(inf) or ''
        for ph in ip['not_in_judged']:
            if ph in tj:
                errors.append(f'{c["case_id"]}: 판정 칸에 있어서는 안 되는 문구가 있음: {ph}')
            if ip['expect'] != '있음' and ph in ti:
                errors.append(f'{c["case_id"]}: 두 칸 모두 없어야 하는데 {inf}에 있음: {ph}')
        for m in MODES:
            x = c['expected'][m]['checks'][ip['item']]
            if x['status'] != ip['expect']:
                errors.append(f'{c["case_id"]}/{m}/{ip["item"]}: 정답 {x["status"]} ≠ 시험 의도 {ip["expect"]}')
            if ip['expect'] == '있음' and x.get('field') != inf:
                errors.append(f'{c["case_id"]}/{m}/{ip["item"]}: 근거 칸이 {inf}이어야 함 (실제 {x.get("field")})')
        probes_ip.append((c['case_id'], ip['expect']))
    if probes_ip and not ({e for _, e in probes_ip} >= {'있음', '부족'}):
        warns.append(f'칸 위치 시험이 한쪽 결과만 있음: {probes_ip}')

    # 지시문 조립 점검 (AI 호출 없음)
    heads = re.compile(r'^\[칸 이름: ([A-Za-z]+\.[a-z_]+)\]', re.M)
    for c in cases:
        values = {**c['inputs'], **(c.get('companions') or {})}
        for m in MODES:
            want = [n for n, _ in fields_given(c['rule_id'], m)]
            got = heads.findall(build_input_block(c['rule_id'], values, m))
            if got != want:
                errors.append(f'{c["case_id"]}/{m}: 입력 머리표 {got} ≠ 주는 칸 {want}')
            ins = build_instructions(c['rule_id'], m)
            for n, _ in fields_given(c['rule_id'], 'together'):
                if n not in want and n in ins:
                    errors.append(f'{c["case_id"]}/{m}: 이 방식에서 주지 않는 칸({n})이 지시문에 나옴')
            for nr, notes in RULE_NOTES.items():
                for note in notes:
                    has = note in ins
                    if has != (c['rule_id'] == nr):
                        errors.append(f'{c["case_id"]}/{m}: {nr} 안내 문구가 {"없음" if not has else "다른 규칙에 들어감"}')

    try:
        rt = load_rubric(RUBRIC)
        for rid, spec in RULES.items():
            got = {k: v['kind'] for k, v in rt['rules'].get(rid, {}).get('checks', {}).items()}
            if got != spec['checks']:
                errors.append(f'기준표 문서와 rubric.py의 {rid} 항목·구분이 다름: 문서 {got} / 코드 {spec["checks"]}')
    except Exception as e:
        errors.append(f'기준표 문서를 읽지 못함: {e}')

    for c in cases:
        b = c.get('borderline')
        if b:
            for m in MODES:
                got = c['expected'][m]['checks'].get(b['item'], {}).get('status')
                if got != b['team_answer']:
                    errors.append(f'{c["case_id"]}/{m}: 경계 사례의 팀 정답 {b["team_answer"]} ≠ 시험 세트 정답 {got}')
            if b['ai_answer'] == b['team_answer']:
                errors.append(f'{c["case_id"]}: 경계 사례인데 AI 판정이 팀 정답과 같음')
            if not b.get('decision') or not b.get('e3'):
                errors.append(f'{c["case_id"]}: 경계 사례의 결정·이전 결과 기록이 없음')
        for dv in c.get('definition_divergent') or []:
            kind = RULES[c['rule_id']]['checks'].get(dv['item'])
            if kind is None:
                errors.append(f'{c["case_id"]}: 정의가 갈리는 셀의 항목 {dv["item"]}이 규칙 {c["rule_id"]}에 없음')
            for m in dv['modes']:
                got = c['expected'][m]['checks'].get(dv['item'], {}).get('status')
                if got != dv['team_answer']:
                    errors.append(f'{c["case_id"]}/{m}: 정의가 갈리는 셀 {dv["item"]}의 팀 정답 {dv["team_answer"]} ≠ 시험 세트 정답 {got}')
            div_cells.append((c['case_id'], dv['item']))
        sr = c.get('stricter_reading')
        if sr:
            for it in sr['items']:
                if it not in RULES[c['rule_id']]['checks']:
                    errors.append(f'{c["case_id"]}: 더 엄격한 읽기 표시의 항목 {it}이 규칙 {c["rule_id"]}에 없음')
            if not sr.get('decision') or not sr.get('source'):
                errors.append(f'{c["case_id"]}: 더 엄격한 읽기 표시에 결정·원문 근거가 없음')
            strict_ids.append(c['case_id'])
        kl = c.get('known_limitation')
        if kl:
            if kl.get('mode') not in MODES or not kl.get('decided') or not kl.get('text'):
                errors.append(f'{c["case_id"]}: 알려진 한계 표시에 방식·결정일·설명이 빠짐')
            if c['expected']['single']['verdict'] == c['expected']['together']['verdict'] and c.get('companion_type') == 'B':
                errors.append(f'{c["case_id"]}: B 유형인데 두 방식 정답이 같음(알려진 한계 표시와 어긋남)')
            known_ids.append(c['case_id'])
        pend = c.get('replacement_pending')
        if pend:
            pv = pend.get('previous_version') or {}
            if not pv.get('edited_text') or not pv.get('e3') or not pend.get('decision'):
                errors.append(f'{c["case_id"]}: 교체 대기인데 이전 글·이전 결과 기록이 없음')
            pending_ids.append(c['case_id'])
        if b and pend:
            errors.append(f'{c["case_id"]}: 경계 사례와 교체 대기를 동시에 표시할 수 없음')
    for rid, spec in RULES.items():
        for f in spec.get('info', []):
            miss = [c['case_id'] for c in cases if c['rule_id'] == rid and f not in c['inputs']]
            if miss:
                errors.append(f'{rid}: 함께 주는 정보 {f}가 입력에 없는 사례 {miss}')

    retired = (data.get('meta') or {}).get('retired') or {}
    ids_now = {c['case_id'] for c in cases}
    for old, info in retired.items():
        if old in ids_now:
            errors.append(f'은퇴한 사례 {old}가 시험 세트에 남아 있음')
        if info.get('replaced_by') not in ids_now:
            errors.append(f'은퇴한 사례 {old}의 새 사례 {info.get("replaced_by")}가 시험 세트에 없음')
        if not info.get('old_text') or not info.get('reason'):
            errors.append(f'은퇴한 사례 {old}의 이유·옛 글 기록이 없음')

    seeds = [c for c in cases if c.get('seed_for')]
    if sorted(c['rule_id'] for c in seeds) != sorted(RULES):
        errors.append(f'팀원용 충분본이 규칙마다 1개가 아님: {[c["case_id"] for c in seeds]}')
    for c in seeds:
        if c['expected']['single']['verdict'] != '충분' or c['expected']['together']['verdict'] != '충분':
            errors.append(f'{c["case_id"]}: 팀원용 충분본인데 충분이 아님')

    # 요약
    print('=' * 64)
    print(f'정성 검증 시험 세트 점검 - {TESTSET.name}')
    print('=' * 64)
    g = Counter(c.get('group', 'claude') for c in cases)
    nb = sum(1 for c in cases if c.get('borderline'))
    print(f'사례 {len(cases)}개 = Claude 작성분 {g["claude"]} + 팀원용 충분본 {g["seed"]} + 팀원 수정분 {g["team"]}'
          f' + 개선 확인용 새 사례 {g["new"]}  (정확도 집계는 그룹별. 경계 사례 {nb}개는 따로, 교체 대기 {len(pending_ids)}개는 제외)')
    if pending_ids:
        print(f'교체 대기: {", ".join(pending_ids)}')
    if known_ids:
        print(f'알려진 한계(정답 유지, 집계에 포함): {", ".join(known_ids)}')
    if strict_ids:
        print(f'더 엄격한 읽기(참고, ㉓ (A)): {", ".join(strict_ids)}')
    if div_cells:
        print(f'정의가 갈리는 셀 {len(div_cells)}개(정답 유지, 권장 항목 정확도는 참고 값으로 따로 보고)')
    if prov:
        print(f'잠정(팀 합의 대기) 사례: {", ".join(prov)} — 정확도 집계에서 따로 표시')
    if resolved:
        print(f'잠정 해소(정확도 집계에 포함): {", ".join(resolved)}')
    pending = [c['case_id'] for c in cases if (c.get('team') or {}).get('cross_check') not in (None, '완료')]
    print(f'팀원 수정분 교차 확인: ' + ('모두 완료' if not pending else f'진행 중 {pending}'))
    if probes_ip:
        print('칸 위치 시험(Q-07 C4): ' + ', '.join(f'{i}→{e}' for i, e in probes_ip))
    print(f'기준표 「부족」 예시 문장 {len(examples)}개, 정상본·오류본 서술 문장 {len(sentences)}개와 겹침 검사')
    by_rule = defaultdict(Counter)
    for c in cases:
        by_rule[c['rule_id']][c['expected']['single']['verdict']] += 1
    print('\n규칙별 최종 판정 (한 칸만 기준)')
    for r in RULES:
        cnt = by_rule[r]
        print(f'  {r}: ' + ', '.join(f'{v} {cnt[v]}' for v in ('충분', '보완 필요', '판단 불가')))
    tot_s = Counter(c['expected']['single']['verdict'] for c in cases)
    tot_t = Counter(c['expected']['together']['verdict'] for c in cases)
    print('  합계 한 칸만 : ' + ', '.join(f'{v} {tot_s[v]}' for v in ('충분', '보완 필요', '판단 불가')))
    print('  합계 함께 줌 : ' + ', '.join(f'{v} {tot_t[v]}' for v in ('충분', '보완 필요', '판단 불가')))
    ct = Counter((c['rule_id'], c.get('companion_type')) for c in cases if RULES[c['rule_id']]['companions'])
    print('\n함께 주는 칸이 있는 규칙의 유형 (A 판정 칸에 있음 / B 함께 준 칸에만 / C 두 칸 모두 없음)')
    for r in [r for r in RULES if RULES[r]['companions']]:
        print(f'  {r}: A {ct[(r, "A")]} · B {ct[(r, "B")]} · C {ct[(r, "C")]}')
    b_items = c_items = 0
    for c in cases:
        if not RULES[c['rule_id']]['companions']:
            continue
        for k, x in c['expected']['single']['checks'].items():
            if x['status'] in ('없음', '부족'):
                if c['expected']['together']['checks'][k]['status'] == '있음':
                    b_items += 1
                else:
                    c_items += 1
    pb = sum(len(c.get('probe_checks') or []) for c in cases if c.get('companion_type') == 'B')
    pc = sum(len(c.get('probe_checks') or []) for c in cases if c.get('companion_type') == 'C')
    print(f'  설계한 시험 항목: B {pb}개 · C {pc}개')
    print(f'  항목 단위 전체: 한 칸만에서 없음·부족인 항목 중 함께 주면 있음 {b_items}개, 함께 줘도 없음·부족 {c_items}개')
    st = Counter(x['status'] for c in cases for x in c['expected']['single']['checks'].values())
    print('\n항목 상태 분포 (한 칸만): ' + ', '.join(f'{s} {st[s]}' for s in STATUSES))
    print('-' * 64)
    for w in warns:
        print('  [참고]', w)
    if errors:
        for e in errors:
            print('  [오류]', e)
        print(f'결과: 오류 {len(errors)}건')
        return 1
    print('결과: 오류 없음')
    return 0


if __name__ == '__main__':
    sys.exit(main())
