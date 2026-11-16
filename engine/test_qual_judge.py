"""
7단계 E1 - 정성 검증 AI 판정 코드 단위시험 (AI 호출 없음, 비용 0)

    engine 폴더에서:  python test_qual_judge.py
"""
import json
from collections import Counter
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corsia.ai.rubric import RULES                                    # noqa: E402
from corsia.ai.rubric_text import load_rubric                         # noqa: E402
from corsia.ai.judge import (judge_case, extract_json, evaluate, quote_found,   # noqa: E402
                             build_messages, skip_reason)
from corsia.ai.models import estimate_cost                            # noqa: E402
import run_qual_eval as RQ                                            # noqa: E402

TESTSET = HERE.parent / '7_qualitative' / 'qual_testset_v1.json'
CASES = {c['case_id']: c for c in json.loads(TESTSET.read_text(encoding='utf-8'))['cases']}
RUBRIC = load_rubric()


class Fake:
    def __init__(self, text):
        self.text, self.stop_reason, self.truncated = text, 'end_turn', False
        self.cost = estimate_cost('sonnet', {'input_tokens': 100, 'output_tokens': 50})


def answer_of(case, mode, drop_field=False):
    checks = []
    for k, x in case['expected'][mode]['checks'].items():
        d = {'id': k, 'status': x['status']}
        if x.get('evidence'):
            d['evidence'] = x['evidence']
            if x.get('field') and not drop_field:
                d['field'] = x['field']
        checks.append(d)
    return {'rule_id': case['rule_id'], 'checks': checks}


class TestJudge(unittest.TestCase):
    def test_rubric_text_matches_rubric_py(self):
        for rid, spec in RULES.items():
            sec = RUBRIC['rules'][rid]
            self.assertEqual({k: v['kind'] for k, v in sec['checks'].items()}, spec['checks'], rid)

    def test_perfect_answers_reproduce_answer_key(self):
        # 정답을 그대로 답하면 모든 사례·두 방식에서 정답표와 같아야 함(인용 확인 포함)
        for c in CASES.values():
            for m in ('single', 'together'):
                txt = json.dumps(answer_of(c, m), ensure_ascii=False)
                rec = judge_case(c, m, lambda *a, **k: Fake(txt), RUBRIC)
                self.assertTrue(rec['json_ok'])
                self.assertEqual(rec['flags'], [], (c['case_id'], m, rec['flags']))
                self.assertTrue(rec['compare']['verdict_match'], (c['case_id'], m))
                self.assertTrue(all(r['match'] for r in rec['compare']['items'].values()), (c['case_id'], m))

    def test_extract_json_with_prose_and_fence(self):
        obj = {'rule_id': 'Q-01', 'checks': [{'id': 'C1', 'status': '있음'}]}
        for t in [json.dumps(obj, ensure_ascii=False),
                  '설명입니다 {중괄호} 그리고\n```json\n' + json.dumps(obj, ensure_ascii=False) + '\n```\n끝',
                  '생각: 먼저 {"x": 1} 를 보면 ... ' + json.dumps(obj, ensure_ascii=False)]:
            got, err = extract_json(t)
            self.assertIsNone(err, t)
            self.assertEqual(got['rule_id'], 'Q-01')
        self.assertIsNotNone(extract_json('JSON 없음')[1])
        self.assertIsNotNone(extract_json('')[1])

    def test_missing_field_name_becomes_unknown_and_says_where(self):
        c = CASES['Q07-F1']   # C3·C4 근거가 발견사항 칸
        ev = evaluate('Q-07', answer_of(c, 'single', drop_field=True), {**c['inputs']}, 'single')
        self.assertEqual(ev['items']['C4']['status'], '판단 불가')
        self.assertIn('VR.findings_and_actions에 있음', ev['items']['C4']['flag'])
        self.assertEqual(ev['verdict'], '판단 불가')

    def test_field_names_written_loosely_are_accepted(self):
        c = CASES['Q07-F1']
        ans = answer_of(c, 'single')
        for d in ans['checks']:
            if d.get('field'):
                d['field'] = f"[{d['field']}]"
        ev = evaluate('Q-07', ans, {**c['inputs']}, 'single')
        self.assertEqual(ev['flags'], [])

    def test_companion_field_not_given_in_single_mode(self):
        c = CASES['Q04-B']    # 함께 줌에서만 저장 위치(5a)를 인정
        ans = answer_of(c, 'together')
        ev = evaluate('Q-04', ans, {**c['inputs'], **c['companions']}, 'single')
        self.assertEqual(ev['items']['C4']['status'], '판단 불가')
        self.assertIn('주지 않은 칸', ev['items']['C4']['flag'])

    def test_invented_quote_and_required_na_and_missing_item(self):
        c = CASES['Q03-S1']
        ans = answer_of(c, 'single')
        ans['checks'][0]['evidence'] = '서술에 없는 문장을 지어냄'
        ans['checks'][2]['status'] = '해당 없음'      # C3 필수
        ans['checks'] = [d for d in ans['checks'] if d['id'] != 'C5']
        ev = evaluate('Q-03', ans, {**c['inputs']}, 'single')
        self.assertEqual(ev['items']['C1']['status'], '판단 불가')
        self.assertEqual(ev['items']['C3']['status'], '판단 불가')
        self.assertEqual(ev['items']['C5']['flag'], '항목 누락')

    def test_quote_matching(self):
        self.assertTrue(quote_found('가나다  라마', '앞 가나다 라마 뒤'))
        self.assertTrue(quote_found('「가나다 라마.」', '앞 가나다 라마 뒤'))
        self.assertTrue(quote_found('가나다 … 바사', '가나다 라마 바사'))
        self.assertFalse(quote_found('가나다 아자', '가나다 라마 바사'))
        self.assertTrue(quote_found('기록 목록', ['모니터링 계획서', '연료 기록 목록']))

    def test_q06_condition_skips_ai(self):
        self.assertIsNotNone(skip_reason('Q-06', {'ER.data_gap_pct': 4.9}))
        self.assertIsNone(skip_reason('Q-06', {'ER.data_gap_pct': 6.3}))

    def test_prompt_has_rubric_and_no_key(self):
        c = CASES['Q04-T']
        s, u = build_messages('Q-04', {**c['inputs'], **c['companions']}, 'single', RUBRIC)
        self.assertIn('방법 B (4.2)', u)
        self.assertIn('변수마다', u)
        self.assertNotIn('EMP.data_management', u)      # 한 칸만 방식에서는 함께 주는 칸이 안 나옴
        self.assertNotIn('sk-ant', s + u)


class TestV15(unittest.TestCase):
    """기준표 v1.5: Q-02 방법 값·C2 구분, 인용 허용 범위, 경계 사례·교체 대기 집계, 재채점."""

    def test_q02_gets_method_in_both_modes_and_prompt_has_rules(self):
        c = CASES['Q02-N3']
        for mode in ('single', 'together'):
            _, u = build_messages('Q-02', {**c['inputs'], **c['companions']}, mode, RUBRIC)
            self.assertIn('EMP.monitoring_method', u)
            self.assertIn('FUEL_UPLIFT', u)                      # 입력 값
            self.assertIn('연료사용량 모니터링 방법', u)             # 방법 구분 설명
            self.assertIn('다시 받는 것', u)                      # C2 한 줄
            self.assertIn('2차 자료출처가 아니다', u)
        _, u1 = build_messages('Q-02', {**c['inputs'], **c['companions']}, 'single', RUBRIC)
        self.assertNotIn('EMP.measurement_timing', u1)           # 한 칸만에서는 측정 시점 칸이 안 나옴

    def test_quote_joiners_and_lists(self):
        self.assertTrue(quote_found('가나다라 / 마바사아', '가나다라 중간 마바사아'))
        self.assertTrue(quote_found('모니터링 계획서, 배출량보고서 및 검증보고서', ['모니터링 계획서', '배출량보고서 및 검증보고서']))
        self.assertFalse(quote_found('가나다 / 없는문장입니다', '가나다 라마 바사'))
        from corsia.ai.judge import quote_match
        self.assertEqual(quote_match('가나다 라마', '앞 가나다 라마 뒤'), 'exact')
        self.assertEqual(quote_match('모니터링 계획서, 배출량', ['모니터링 계획서', '배출량보고서']), 'loose')

    def test_insufficient_without_quote_becomes_missing_but_present_without_quote_is_unknown(self):
        c = CASES['Q04-C2']
        ans = answer_of(c, 'single')
        for d in ans['checks']:
            if d['id'] == 'C3':
                d['status'] = '부족'; d.pop('evidence', None)
            if d['id'] == 'C6':
                d['status'] = '있음'; d.pop('evidence', None)
        ev = evaluate('Q-04', ans, {**c['inputs']}, 'single')
        self.assertEqual(ev['items']['C3']['status'], '없음')
        self.assertEqual(ev['items']['C6']['status'], '판단 불가')
        self.assertEqual(ev['verdict'], '보완 필요')                # C3 없음이라 판단 불가로 바뀌지 않음

    def _records(self, ids, modes=('single', 'together')):
        out = []
        for cid in ids:
            c = CASES[cid]
            for m in modes:
                txt = json.dumps(answer_of(c, m), ensure_ascii=False)
                out.append(judge_case(c, m, lambda *a, **k: Fake(txt), RUBRIC))
        return out

    def test_summary_separates_borderline_and_excludes_pending(self):
        import copy
        cases = copy.deepcopy(CASES)
        cases['Q08-N1']['replacement_pending'] = {'since': 'x', 'decision': 'x', 'previous_version': {}}   # 교체 대기 표시를 흉내냄
        ids = ['Q03-S1', 'Q03-T', 'Q05-D2', 'Q08-N1', 'Q02-N1']
        s = RQ.summarize(self._records(ids), cases)
        keys = set(s['accuracy'])
        self.assertIn('borderline/single', keys)
        self.assertEqual(s['accuracy']['borderline/single']['n'], 2)      # Q03-T, Q05-D2
        self.assertIn('new/single', keys)
        self.assertNotIn('team/single', keys)                              # Q03-T는 경계 사례로 빠짐
        self.assertEqual(s['pending_excluded'], ['Q08-N1'])                # 교체 대기 사례는 집계에서 제외

    def test_flags_on_test_set(self):
        self.assertEqual([c['case_id'] for c in CASES.values() if c.get('borderline')], ['Q05-D2', 'Q03-T', 'Q07-T'])
        self.assertEqual([c['case_id'] for c in CASES.values() if c.get('replacement_pending')], [])   # 교체 대기 사례 없음
        retired = json.loads(TESTSET.read_text(encoding='utf-8'))['meta']['retired']
        self.assertEqual(sorted(retired), ['Q04-P', 'Q08-T'])
        self.assertEqual(retired['Q08-T']['replaced_by'], 'Q08-T2')
        self.assertIn('바로잡았고', retired['Q08-T']['old_text'])          # 옛 글과 옛 결과는 보존
        self.assertIn('6/6', retired['Q08-T']['old_results'])

    def test_pick_cases_skips_pending_unless_explicit(self):
        allc = list(CASES.values())
        self.assertEqual(len(RQ.pick_cases(allc, 'new')), 3)
        self.assertEqual(len(RQ.pick_cases(allc, 'borderline')), 3)
        self.assertEqual([c['case_id'] for c in RQ.pick_cases(allc, 'Q08-T2')], ['Q08-T2'])

    def test_rejudge_reproduces_and_applies_new_rules(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            rc = RQ.main(['--set', 'Q02-N1,Q07-F1,Q03-T', '--mode', 'both', '--mock', 'perfect', '--out', td, '--label', 't'])
            self.assertEqual(rc, 0)
            run = next(out.iterdir())
            rc = RQ.main(['--rejudge', str(run)])
            self.assertEqual(rc, 0)
            self.assertTrue((run / 'summary_rejudged.md').exists())
            self.assertTrue((run / 'results.jsonl').exists())              # 원본은 그대로
            a = (run / 'results.jsonl').read_text(encoding='utf-8')
            b = (run / 'results_rejudged.jsonl').read_text(encoding='utf-8')
            ra = [json.loads(x) for x in a.splitlines()]
            rb = [json.loads(x) for x in b.splitlines()]
            self.assertEqual([r['verdict'] for r in ra], [r['verdict'] for r in rb])
            self.assertTrue(all(r.get('rejudged') for r in rb))


class TestV16(unittest.TestCase):
    """기준표 v1.6: Q-02 C3 한 줄, 조각 문장 인용, 규칙 선택, 알려진 한계."""

    def test_q02_prompt_has_c3_line(self):
        c = CASES['Q02-D1']
        for mode in ('single', 'together'):
            _, u = build_messages('Q-02', {**c['inputs'], **c['companions']}, mode, RUBRIC)
            self.assertIn('CERT를 어떻게 적용하는지', u)               # 팀 결정 ⑬ 한 줄
            self.assertIn('2차 자료출처가 서술되었는지(C2)와 따로 판정한다', u)
            self.assertIn('CERT를 특정하지 않은 서술은', u)

    def test_pieced_sentence_quotes(self):
        from corsia.ai.judge import quote_match
        text = '확인 사항은 2건이다. 첫째, 가나다라마바사가 잘못되었다(비중대). 운영사는 고쳤고, 검증팀이 확인하였다. 둘째, 아자차카타파하가 늦었다(비중대).'
        # 가운데 문장을 건너뛰고 이어 붙인 인용은 문장마다 확인해 인정
        self.assertEqual(quote_match('확인 사항은 2건이다. 첫째, 가나다라마바사가 잘못되었다(비중대). 둘째, 아자차카타파하가 늦었다(비중대).', text), 'pieced')
        # 한 문장이라도 그 칸에 없으면 인정하지 않음
        self.assertIsNone(quote_match('확인 사항은 2건이다. 첫째, 없는내용이지어낸문장이다. 둘째, 아자차카타파하가 늦었다(비중대).', text))
        # 이어진 인용은 그대로 exact
        self.assertEqual(quote_match('첫째, 가나다라마바사가 잘못되었다(비중대). 운영사는 고쳤고, 검증팀이 확인하였다', text), 'exact')
        # 한 문장짜리 틀린 인용은 조각 확인으로 살아나지 않음
        self.assertIsNone(quote_match('둘째, 아자차카타파하가 일찍 끝났다(비중대).', text))

    def test_pieced_quote_is_accepted_in_evaluate_and_marked(self):
        c = CASES['Q08-S2']
        ans = answer_of(c, 'single')
        ev_text = c['inputs']['VR.findings_and_actions']
        sents = [x.strip() for x in ev_text.replace('다. ', '다.\n').split('\n') if x.strip()]
        for d in ans['checks']:
            if d['id'] == 'C1':
                d['evidence'] = ' '.join([sents[0], sents[-1]])        # 중간 문장 건너뛰기
                d['field'] = 'VR.findings_and_actions'
        ev = evaluate('Q-08', ans, {**c['inputs']}, 'single')
        self.assertEqual(ev['items']['C1']['status'], '있음')
        self.assertTrue(ev['items']['C1'].get('quote_pieced'))
        self.assertEqual(ev['flags'], [])

    def test_two_fields_in_one_quote_still_rejected(self):
        c = CASES['Q07-F1']
        ans = answer_of(c, 'single')
        for d in ans['checks']:
            if d['id'] == 'C4':
                d['evidence'] = c['inputs']['VR.findings_and_actions'][:40] + ' / ' + c['inputs']['VR.verification_opinion'][:40]
                d['field'] = 'VR.findings_and_actions'
        ev = evaluate('Q-07', ans, {**c['inputs']}, 'single')
        self.assertEqual(ev['items']['C4']['status'], '판단 불가')           # 두 칸 인용을 한 칸 이름으로 묶으면 인정 안 함

    def test_rule_selector_and_known_limitation(self):
        allc = list(CASES.values())
        q02 = RQ.pick_cases(allc, 'Q-02')
        self.assertEqual(len(q02), 12)
        self.assertTrue(all(c['rule_id'] == 'Q-02' for c in q02))
        self.assertEqual(len(RQ.pick_cases(allc, 'Q-08')), 8)
        self.assertEqual([c['case_id'] for c in allc if c.get('known_limitation')], ['Q02-B2'])
        s = RQ.summarize(TestV15()._records(['Q02-B2', 'Q02-C']), CASES)
        self.assertEqual(s['known_limitations'], ['Q02-B2'])
        self.assertIn('claude/single', s['accuracy'])                      # 알려진 한계 사례도 정확도에 포함


class TestV17(unittest.TestCase):
    """기준표 v1.7: Q-02 C2 기술일지·연료 송장, Q-08 C4·C5 조건, 정의가 갈리는 셀, --only-rep."""

    def test_q02_c2_text_in_prompt_keeps_both_rules(self):
        c = CASES['Q02-N1']
        for mode in ('single', 'together'):
            _, u = build_messages('Q-02', {**c['inputs'], **c['companions']}, mode, RUBRIC)
            self.assertIn('기술일지·연료 송장처럼 1차 전송 경로와 다른 기록·문서는 같은 측정값을 담아도 2차 자료출처로 본다', u)
            self.assertIn('ACARS 원본 재조회, 수신 서버 원시 로그 재수신 등', u)      # 1차 재조회는 2차가 아님(N1)
            self.assertIn('원본·로그·수신 서버 기록', u)                            # 1차 경로의 범위
            self.assertIn('ETM 3.1.6', u)

    def test_q08_c4_c5_condition_in_prompt_and_answer_key(self):
        c = CASES['Q08-N2']
        _, u = build_messages('Q-08', {**c['inputs']}, 'single', RUBRIC)
        self.assertIn('C4·C5(권장)도 C2와 같은 조건을 쓴다', u)
        ex = c['expected']['single']
        self.assertEqual((ex['checks']['C4']['status'], ex['checks']['C5']['status']), ('판단 불가', '판단 불가'))
        self.assertEqual(ex['verdict'], '보완 필요')                              # 최종 판정은 그대로
        n1 = CASES['Q08-N1']['expected']['single']['checks']
        self.assertEqual((n1['C4']['status'], n1['C5']['status']), ('해당 없음', '해당 없음'))

    def test_divergent_cells_keep_answers(self):
        cells = [(c['case_id'], d['item']) for c in CASES.values() for d in (c.get('definition_divergent') or [])]
        kinds = Counter(RULES[CASES[cid]['rule_id']]['checks'][it] for cid, it in cells)
        self.assertEqual(kinds['권장'], 12)                                   # 권장 항목 12셀(⑯)
        self.assertEqual(kinds['필수'], 1)                                    # 필수 항목 1셀(⑱: Q02-C 함께 줌 C2)
        self.assertEqual(len(cells), 13)
        self.assertIn(('Q02-B', 'C6'), cells)
        self.assertIn(('Q07-F1', 'C6'), cells)
        self.assertEqual(CASES['Q02-B']['expected']['single']['checks']['C6']['status'], '없음')   # 정답은 바뀌지 않음

    def test_optional_accuracy_is_reference_and_split(self):
        recs = TestV15()._records(['Q02-B', 'Q07-F1', 'Q03-S1'], modes=('single',))
        for r in recs:                                         # 정의가 갈리는 셀(C6)을 AI가 다르게 답한 것처럼 만듦
            if r['case_id'] in ('Q02-B', 'Q07-F1'):
                row = r['compare']['items']['C6']
                row['got'], row['match'] = '부족', False
        s = RQ.summarize(recs, CASES)
        a = s['accuracy']['claude/single']
        self.assertEqual(a['divergent_n'], 2)
        self.assertEqual(a['optional_divergent'], 0.0)
        self.assertEqual(a['optional_nondivergent'], 1.0)      # 정의가 갈리는 셀을 빼면 권장 항목은 모두 일치
        self.assertEqual(a['verdict'], 1.0)                    # 최종 판정에는 영향 없음
        md = RQ.summary_md({'label': 't', 'started': 's', 'model_id': 'm', 'thinking': 'a', 'effort': 'b', 'modes': 'single',
                            'repeat': 1, 'rubric_version': 'v', 'testset_updated': 'u'}, s, recs)
        head, ref = md.split('## 권장 항목 (참고 값')
        self.assertIn('필수·조건부 항목 일치', head)
        self.assertNotIn('권장 항목 일치', head.split('## 정확도')[1])   # headline 표에는 권장 항목 열이 없음
        self.assertIn('정의가 갈리는 셀 제외', ref)
        self.assertIn('AI에 맞춘 것이 아니다', ref)

    def test_only_rep_summary_and_results_untouched(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            rc = RQ.main(['--set', 'Q03-S1,Q05-D1', '--mode', 'both', '--repeat', '2', '--mock', 'perfect', '--out', td, '--label', 'r'])
            self.assertEqual(rc, 0)
            run = next(Path(td).iterdir())
            before = (run / 'results.jsonl').read_text(encoding='utf-8')
            self.assertEqual(RQ.main(['--report', str(run), '--only-rep', '1']), 0)
            self.assertEqual((run / 'results.jsonl').read_text(encoding='utf-8'), before)    # 결과 파일은 그대로
            full = json.loads((run / 'summary.json').read_text(encoding='utf-8')) if (run / 'summary.json').exists() else None
            one = json.loads((run / 'summary_rep1.json').read_text(encoding='utf-8'))
            self.assertEqual(one['calls'], 4)                                                 # 2사례 × 2방식 × 1회차
            if full:
                self.assertEqual(full['calls'], 8)
            self.assertEqual(RQ.main(['--rejudge', str(run), '--only-rep', '2']), 0)
            self.assertTrue((run / 'summary_rejudged_rep2.md').exists())
            with self.assertRaises(SystemExit):
                RQ.main(['--only-rep', '1'])

    def test_opus_estimate_calibrated(self):
        self.assertEqual(RQ.est_output_tokens('opus', None, None), 1400)
        self.assertEqual(RQ.est_output_tokens('sonnet', 'adaptive', 'low'), 850)


class TestV18(unittest.TestCase):
    """기준표 v1.8: Q07-D1 정정, Q04-P2 재작성, 필수 항목의 정의가 갈리는 셀, 교체된 사례, 규칙 5 확정."""

    def test_q07_d1_c2_matches_rubric_example(self):
        guide = ' '.join(RUBRIC['rules']['Q-07']['guide'])
        self.assertIn('검증 결과 만족함', guide)                           # 기준표의 「부족」 예시
        ex = CASES['Q07-D1']['expected']['single']
        self.assertEqual(ex['checks']['C2']['status'], '부족')              # 결론만 있는 경우 = 부족
        self.assertEqual(ex['verdict'], '보완 필요')                        # 최종 판정은 그대로

    def test_q04_p2_does_not_reveal_the_method(self):
        self.assertNotIn('Q04-P', CASES)
        c = CASES['Q04-P2']
        text = c['inputs']['EMP.measurement_timing']
        for w in ('견인', '주차 브레이크', 'FQIS', 'Block', '블록', '급유', '탱크'):
            self.assertNotIn(w, text)
        self.assertIsNone(c['inputs']['EMP.monitoring_method'])
        st = {k: v['status'] for k, v in c['expected']['single']['checks'].items()}
        self.assertEqual(st, {'C1': '없음', 'C2': '판단 불가', 'C3': '판단 불가', 'C4': '판단 불가', 'C5': '판단 불가', 'C6': '없음'})
        self.assertEqual(c['expected']['single']['verdict'], '보완 필요')
        meta = json.loads(TESTSET.read_text(encoding='utf-8'))['meta']
        self.assertEqual(meta['retired']['Q04-P']['replaced_by'], 'Q04-P2')

    def test_required_divergent_cell_is_split_out(self):
        dv = CASES['Q02-C']['definition_divergent']
        self.assertIn(('C2', ['together']), [(d['item'], d['modes']) for d in dv])
        recs = TestV15()._records(['Q02-C'], modes=('single', 'together'))
        for r in recs:
            if r['mode'] == 'together':
                row = r['compare']['items']['C2']
                row['got'], row['match'] = '부족', False
        s = RQ.summarize(recs, CASES)
        t, o = s['accuracy']['claude/together'], s['accuracy']['claude/single']
        self.assertEqual(t['core_divergent_n'], 1)
        self.assertLess(t['core_items'], 1.0)                              # headline에는 그대로 반영
        self.assertEqual(t['core_nondivergent'], 1.0)                      # 정의가 갈리는 셀 제외 값은 100%
        self.assertEqual(o['core_divergent_n'], 0)                         # 한 칸만 방식에는 해당 셀 없음
        md = RQ.summary_md({'label': 't', 'started': 's', 'model_id': 'm', 'thinking': 'a', 'effort': 'b', 'modes': 'x',
                            'repeat': 1, 'rubric_version': 'v', 'testset_updated': 'u'}, s, recs)
        self.assertIn('(참고) 정의가 갈리는 셀 제외', md)
        self.assertIn('(셀 1)', md)

    def test_retired_case_results_are_excluded(self):
        recs = TestV15()._records(['Q03-S1'], modes=('single',))
        old = dict(recs[0]); old['case_id'] = 'Q04-P'                      # 옛 사례의 결과가 남아 있는 경우
        s = RQ.summarize(recs + [old], CASES)
        self.assertEqual(s['retired_excluded'], ['Q04-P'])
        self.assertEqual(s['calls'], 1)
        self.assertEqual(s['accuracy']['claude/single']['n'], 1)

    def test_rule5_confirmed_in_rubric(self):
        text = Path(RUBRIC['path']).read_text(encoding='utf-8')
        self.assertIn('E5 결론(확정, 2026-11-14 팀 결정 ㉒)', text)
        self.assertNotIn('E5 결론(제안', text)


    def test_append_to_adds_cases_with_same_settings(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(RQ.main(['--set', 'Q03-S1', '--mode', 'both', '--repeat', '2', '--mock', 'perfect', '--out', td, '--label', 'a']), 0)
            run = next(Path(td).iterdir())
            n0 = len((run / 'results.jsonl').read_text(encoding='utf-8').splitlines())
            self.assertEqual(RQ.main(['--set', 'Q04-P2', '--mode', 'both', '--repeat', '2', '--mock', 'perfect', '--append-to', str(run)]), 0)
            lines = [json.loads(x) for x in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(lines), n0 + 4)                                       # 1사례 × 2방식 × 2회
            self.assertEqual({r['case_id'] for r in lines}, {'Q03-S1', 'Q04-P2'})
            cfg = json.loads((run / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(cfg['set'], 'Q03-S1')                                     # 원래 설정은 그대로
            self.assertEqual(cfg['appended'][0]['cases'], ['Q04-P2'])
            summ = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summ['calls'], n0 + 4)                                    # summary는 전체로 다시 만들어짐
            with self.assertRaises(SystemExit):                                        # 설정(반복 횟수)이 다르면 멈춤
                RQ.main(['--set', 'Q04-P2', '--mode', 'both', '--repeat', '1', '--mock', 'perfect', '--append-to', str(run)])
            with self.assertRaises(SystemExit):
                RQ.main(['--set', 'Q04-P2', '--resume', str(run), '--append-to', str(run), '--mock', 'perfect'])


    def test_append_to_recompares_old_records_with_current_keys(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(RQ.main(['--set', 'Q03-S1', '--mode', 'both', '--mock', 'perfect', '--out', td, '--label', 'a']), 0)
            run = next(Path(td).iterdir())
            rows = [json.loads(x) for x in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
            for r in rows:                                   # 옛 정답과 달랐던 것처럼 저장된 비교 결과를 틀리게 만든다
                r['compare']['verdict_match'] = False
            (run / 'results.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
            self.assertEqual(RQ.main(['--set', 'Q02-N1', '--mode', 'both', '--mock', 'perfect', '--append-to', str(run)]), 0)
            summ = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summ['accuracy']['claude/single']['verdict'], 1.0)        # 현재 정답으로 다시 비교되어 100%


class TestQ08T2(unittest.TestCase):
    """Q08-T 교체(Q08-T2)와 ㉓ (A) 더 엄격한 읽기 기록."""

    def test_q08_t2_key_and_text(self):
        c = CASES['Q08-T2']
        self.assertNotIn('Q08-T', CASES)
        text = c['inputs']['VR.findings_and_actions']
        self.assertNotIn('바로잡았', text)                                    # 옛 글의 해결 표현이 없음
        self.assertNotIn('수정 결과', text)
        self.assertIn('이 건에 대한 의견을 검증팀에 전달하였고', text)
        st = {k: v['status'] for k, v in c['expected']['single']['checks'].items()}
        self.assertEqual(st, {'C1': '있음', 'C2': '부족', 'C3': '있음', 'C4': '있음', 'C5': '있음'})
        self.assertEqual(c['expected']['single']['verdict'], '보완 필요')
        self.assertEqual(c['team']['author'], '팀원4')
        self.assertEqual(c['team']['cross_check'], '완료')
        self.assertEqual(c['source_case'], 'Q08-S2')
        for m in ('single', 'together'):                                      # 인용이 실제 글에 있음
            for k, x in c['expected'][m]['checks'].items():
                self.assertIn(x['evidence'], text, (m, k))

    def test_stricter_reading_recorded_without_changing_keys(self):
        flagged = {cid: c['stricter_reading'] for cid, c in CASES.items() if c.get('stricter_reading')}
        self.assertEqual(sorted(flagged), ['Q03-D2', 'Q07-U', 'Q08-D1'])
        self.assertEqual(CASES['Q07-U']['expected']['single']['checks']['C2']['status'], '있음')    # 정답은 그대로(㉓ (A))
        self.assertEqual(CASES['Q08-D1']['expected']['single']['checks']['C2']['status'], '있음')
        self.assertEqual(CASES['Q03-D2']['expected']['single']['checks']['C5']['status'], '있음')
        for sr in flagged.values():
            self.assertIn('(A)', sr['decision'])
            self.assertIn('(B)', sr['decision'])
        recs = TestV15()._records(['Q07-U', 'Q03-S1'], modes=('single',))
        s = RQ.summarize(recs, CASES)
        self.assertEqual(s['stricter_reading_cases'], ['Q07-U'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
