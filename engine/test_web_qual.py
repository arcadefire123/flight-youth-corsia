"""웹 연결용 정성 검증(web_qual) 단위시험 — AI를 부르지 않는다(모의 응답). engine 폴더에서:  python test_web_qual.py"""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corsia.ai import web_qual as W  # noqa: E402
from corsia.ai import client as CL  # noqa: E402

HERE = Path(__file__).resolve().parent
DOCS = json.loads((HERE / 'dataset' / 'documents.json').read_text(encoding='utf-8'))


def docs():
    return copy.deepcopy(DOCS)


def real_like(kind='ok'):
    """실제 호출처럼 취급되는 모의 함수(출처 api). 디스크 기억 시험에만 쓴다."""
    fn = W.mock_call(kind)
    fn.source = 'api'
    return fn


def run(documents=None, kind='ok', **kw):
    kw.setdefault('cache_dir', None)
    kw.setdefault('data_gap_pct', 0.0)
    fn = kw.pop('call_fn', None) or (real_like(kind) if kw['cache_dir'] else W.mock_call(kind))     # 기억을 쓰는 시험은 api 출처로
    return W.run_qualitative(documents if documents is not None else docs(), call_fn=fn, **kw), fn


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(W, 'RETRY_SLEEP', 0.0)
        p.start()
        self.addCleanup(p.stop)


class TestInputs(Base):
    def test_method_prefers_emp_then_er(self):
        d = docs()
        self.assertEqual(W.find_method(d['documents']), ('BLOCK_OFF_ON', 'EMP.monitoring_method'))
        del d['documents']['EMP']['monitoring_method']
        d['documents']['ER']['monitoring_method'] = 'METHOD_B'
        self.assertEqual(W.find_method(d['documents']), ('METHOD_B', 'ER.monitoring_method'))
        d['documents']['ER']['monitoring_method'] = ' '
        self.assertEqual(W.find_method(d['documents']), (None, None))

    def test_accepts_full_json_or_inner_documents(self):
        a, _ = run(DOCS)
        b, _ = run(DOCS['documents'])
        self.assertEqual(a['input_hash'], b['input_hash'])
        self.assertEqual(a['status'], 'ok')

    def test_plans_for_the_sample(self):
        plans, method = W.build_plans(DOCS, data_gap_pct=0.0)
        self.assertEqual([p['rule_id'] for p in plans], list(W.RULE_IDS))
        st = {p['rule_id']: p['state'] for p in plans}
        self.assertEqual(st['Q-06'], 'skipped_condition')             # 데이터갭 0% ≤ 5%
        self.assertEqual(sum(1 for v in st.values() if v == 'call'), 7)
        self.assertEqual(method, {'value': 'BLOCK_OFF_ON', 'source': 'EMP.monitoring_method'})

    def test_q06_runs_when_gap_over_5_and_text_present(self):
        d = docs()
        d['documents']['ER']['data_gap_explanation'] = '데이터갭 7.2%는 2024년 3월 ACARS 전송 장애로 발생하였다.'
        plans, _ = W.build_plans(d, data_gap_pct=7.2)
        self.assertEqual({p['rule_id']: p['state'] for p in plans}['Q-06'], 'call')
        d['documents']['ER']['data_gap_explanation'] = ''
        plans, _ = W.build_plans(d, data_gap_pct=7.2)
        self.assertEqual({p['rule_id']: p['state'] for p in plans}['Q-06'], 'no_text')

    def test_gap_falls_back_to_document_value(self):
        d = docs()
        d['documents']['ER']['data_gap_pct'] = 8.0
        d['documents']['ER']['data_gap_explanation'] = '사유 설명입니다.'
        plans, _ = W.build_plans(d, data_gap_pct=None)
        self.assertEqual({p['rule_id']: p['state'] for p in plans}['Q-06'], 'call')

    def test_prompts_follow_the_rule5_table_together_mode(self):
        plans, _ = W.build_plans(DOCS, data_gap_pct=0.0)
        from corsia.ai import judge, rubric_text
        rb = rubric_text.load_rubric()
        by = {p['rule_id']: judge.build_messages(p['rule_id'], p['values'], 'together', rb)[1] for p in plans}
        self.assertIn('EMP.records_retained', by['Q-01'])
        self.assertIn('EMP.measurement_timing', by['Q-02'])
        self.assertIn('BLOCK_OFF_ON', by['Q-02'])
        self.assertIn('EMP.data_management', by['Q-04'])
        self.assertNotIn('EMP.records_retained', by['Q-03'])


class TestMissingInputs(Base):
    def test_method_missing_judges_q02_and_q04_unable_without_calling_ai(self):
        d = docs()
        del d['documents']['EMP']['monitoring_method']
        del d['documents']['ER']['monitoring_method']
        r, fn = run(d)
        items = {i['rule_id']: i for i in r['items']}
        for rid in ('Q-02', 'Q-04'):
            self.assertEqual(items[rid]['status'], 'method_missing')
            self.assertEqual(items[rid]['verdict'], '판단 불가')
            self.assertIn('모니터링 방법 값', items[rid]['reason'])
        self.assertEqual(items['Q-01']['status'], 'ok')                  # 나머지는 그대로 판정
        self.assertEqual(fn.calls['n'], 5)                                # 8 - Q-06(조건 미발생) - Q-02 - Q-04
        self.assertEqual(r['status'], 'ok')
        self.assertIsNone(r['monitoring_method']['value'])

    def test_er_method_is_used_when_emp_has_none(self):
        d = docs()
        del d['documents']['EMP']['monitoring_method']
        r, fn = run(d)
        self.assertEqual(r['monitoring_method'], {'value': 'BLOCK_OFF_ON', 'source': 'ER.monitoring_method'})
        self.assertEqual(fn.calls['n'], 7)

    def test_empty_narrative_is_not_sent(self):
        d = docs()
        d['documents']['EMP']['route_determination'] = '  '
        r, fn = run(d)
        it = {i['rule_id']: i for i in r['items']}['Q-03']
        self.assertEqual(it['status'], 'no_text')
        self.assertIsNone(it['verdict'])
        self.assertEqual(fn.calls['n'], 6)

    def test_nothing_to_judge(self):
        r, fn = run({'documents': {}})
        self.assertEqual(r['status'], 'no_text')
        self.assertEqual(fn.calls['n'], 0)
        r = W.run_qualitative(None, call_fn=W.mock_call('ok'), cache_dir=None)          # 입력이 이상해도 예외 없음
        self.assertEqual(r['status'], 'no_text')


class TestResultShape(Base):
    def test_ok_result(self):
        r, fn = run()
        self.assertEqual(r['schema'], 'qualitative_result_v1')
        self.assertEqual(r['status'], 'ok')
        self.assertTrue(r['ai_reference_only'])
        self.assertEqual([i['rule_id'] for i in r['items']], list(W.RULE_IDS))          # Q-01~Q-08 순서
        self.assertEqual(r['settings']['model_alias'], 'sonnet')
        self.assertEqual((r['settings']['thinking'], r['settings']['effort'], r['settings']['mode']), ('adaptive', 'low', 'together'))
        self.assertTrue(r['settings']['rubric_version'].startswith('v1.8'))
        self.assertEqual(r['cost']['n_calls'], 7)
        self.assertGreater(r['cost']['actual_usd'], 0)
        self.assertLess(r['cost']['actual_usd'], r['cost']['max_usd'])
        self.assertEqual(sum(r['summary']['verdicts'].values()), 7)
        it = r['items'][0]
        for k in ('rule_id', 'document', 'item', 'question', 'cited_clause', 'status', 'verdict', 'reason', 'missing_points', 'checks', 'flags',
                  'fake_citations', 'cached', 'cost_usd', 'tokens', 'seconds', 'source'):
            self.assertIn(k, it)
        for k in ('id', 'kind', 'text', 'status', 'ai_status', 'evidence', 'field', 'flag', 'citation_problem', 'fake_citation'):
            self.assertIn(k, it['checks'][0])

    def test_result_is_json_and_has_no_paths_or_keys(self):
        r, _ = run()
        blob = json.dumps(r, ensure_ascii=False)
        for bad in ('sk-ant', 'C:\\', '/home/', '/Users/', 'ANTHROPIC_API_KEY'):
            self.assertNotIn(bad, blob)
        self.assertEqual(json.loads(blob)['schema'], 'qualitative_result_v1')

    def test_call_settings_are_the_confirmed_ones(self):
        seen = []

        def spy(user, **kw):
            seen.append(kw)
            return W.mock_call('ok')(user, **kw)
        run(call_fn=spy)
        self.assertEqual(len(seen), 7)
        for kw in seen:
            self.assertEqual((kw['model_key'], kw['thinking'], kw['effort'], kw['max_tokens']), ('sonnet', 'adaptive', 'low', 4000))

    def test_weak_narrative_needs_supplement_and_final_verdict_is_computed_by_code(self):
        r, _ = run(kind='weak')
        for it in r['items']:
            if it['status'] == 'ok':
                self.assertEqual(it['verdict'], '보완 필요')
                self.assertTrue(it['missing_points'])

    def test_fake_quotes_are_marked(self):
        r, _ = run(kind='fake_quote')
        self.assertGreaterEqual(r['summary']['fake_citations'], 1)
        it = r['items'][0]
        bad = [c for c in it['checks'] if c['fake_citation']]
        self.assertTrue(bad)
        self.assertEqual(bad[0]['status'], '판단 불가')                       # 인용이 글에 없으면 판단 불가로 바뀜(판정 코드)
        self.assertTrue(bad[0]['citation_problem'])

    def test_excel_rows(self):
        r, _ = run()
        rows = W.excel_rows(r)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['규칙'], 'Q-01')
        self.assertIn('AI 참고 판정', rows[0])
        self.assertEqual(rows[5]['AI 참고 판정'], '해당 없음(조건 미발생)')           # Q-06
        self.assertEqual(W.excel_rows(None), [])


class TestFailuresDoNotStop(Base):
    def test_no_key_returns_status_without_calls(self):
        env = {k: v for k, v in os.environ.items() if k != CL.ENV_KEY}
        with mock.patch.dict(os.environ, env, clear=True):
            r = W.run_qualitative(docs(), data_gap_pct=0.0, cache_dir=None)           # call_fn 없음 = 실제 호출 경로
            self.assertEqual(r['status'], 'no_key')
            self.assertEqual(r['items'], [])
            self.assertEqual(r['status_message'], '서술 사용'.replace('서술 사용', W.NO_KEY_TEXT))
            self.assertEqual(r['cost']['actual_usd'], 0.0)
            self.assertFalse(W.estimate_qualitative(docs(), 0.0, cache_dir=None)['key_set'])

    def test_no_key_even_if_cache_has_results(self):
        with tempfile.TemporaryDirectory() as td:
            run(cache_dir=td)
            env = {k: v for k, v in os.environ.items() if k != CL.ENV_KEY}
            with mock.patch.dict(os.environ, env, clear=True):
                r = W.run_qualitative(docs(), data_gap_pct=0.0, cache_dir=td)
            self.assertEqual(r['status'], 'no_key')                                    # 인수 시험(키 제거 → 안내 한 줄)과 같은 동작

    def test_network_error_is_a_status_after_one_retry(self):
        r, fn = run(kind='network')
        self.assertEqual(r['status'], 'network_error')
        self.assertTrue(all(i['status'] == 'network_error' for i in r['items'] if i['rule_id'] != 'Q-06'))
        self.assertEqual(fn.calls['n'], 14)                                            # 7개 × (처음 + 재시도 1)
        self.assertIn('네트워크', r['status_message'])

    def test_rate_limit_once_recovers(self):
        r, fn = run(kind='rate_limit_once')
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(fn.calls['n'], 8)

    def test_auth_error_is_api_error_with_no_retry(self):
        r, fn = run(kind='auth_error')
        self.assertEqual(r['status'], 'api_error')
        self.assertEqual(fn.calls['n'], 7)
        self.assertIn('인증', r['items'][0]['message'] or r['items'][1]['message'])

    def test_unparsable_answer_is_a_status(self):
        r, fn = run(kind='bad_json')
        self.assertEqual(r['status'], 'parse_error')
        self.assertEqual(fn.calls['n'], 14)                                            # 해석 실패는 한 번 다시 물음

    def test_flaky_answers_recover_on_retry(self):
        r, fn = run(kind='flaky', max_workers=1)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(fn.calls['n'], 14)

    def test_unexpected_exception_in_call_is_a_status(self):
        def boom(user, **kw):
            raise RuntimeError('이상한 오류')
        r, _ = run(call_fn=boom)
        self.assertEqual(r['status'], 'api_error')

    def test_partial_when_only_some_fail(self):
        n = {'k': 0}
        ok = W.mock_call('ok')
        lock = threading.Lock()

        def half(user, **kw):
            with lock:
                n['k'] += 1
                k = n['k']
            if 'Q-03' in user.split('\n')[0] or 'Q-05' in user.split('\n')[0]:
                raise CL.AIError('인증 오류', 'x', 401, '')
            return ok(user, **kw)
        r, _ = run(call_fn=half)
        self.assertEqual(r['status'], 'partial')
        st = {i['rule_id']: i['status'] for i in r['items']}
        self.assertEqual((st['Q-03'], st['Q-05'], st['Q-01']), ('api_error', 'api_error', 'ok'))


class TestBudget(Base):
    def test_estimate_is_below_the_limit_for_the_sample(self):
        est = W.estimate_qualitative(docs(), 0.0, cache_dir=None)
        self.assertEqual(est['n_calls'], 7)
        self.assertTrue(0.05 < est['estimated_usd'] < 0.15)
        self.assertTrue(est['within_budget'])
        self.assertEqual(W.MAX_USD_DEFAULT, 0.30)

    def test_estimate_hash_matches_result_hash(self):
        est = W.estimate_qualitative(docs(), 0.0, cache_dir=None)
        r, _ = run()
        self.assertEqual(est['input_hash'], r['input_hash'])
        d = docs()
        d['documents']['EMP']['risk_assessment'] += ' 다른 문장.'
        self.assertNotEqual(W.estimate_qualitative(d, 0.0, cache_dir=None)['input_hash'], est['input_hash'])

    def test_over_budget_is_a_status_and_nothing_is_called(self):
        r, fn = run(max_usd=0.01)
        self.assertEqual(r['status'], 'budget_exceeded')
        self.assertEqual(fn.calls['n'], 0)
        self.assertEqual(r['cost']['actual_usd'], 0.0)

    def test_running_cost_stops_new_calls(self):
        r, fn = run(kind='expensive', max_workers=1, max_usd=0.12)          # 호출 하나가 약 $0.09
        st = [i['status'] for i in r['items'] if i['rule_id'] != 'Q-06']
        self.assertIn('budget_exceeded', st)
        self.assertEqual(r['status'], 'partial')
        self.assertLessEqual(fn.calls['n'], 3)


class TestCache(Base):
    def test_second_run_costs_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            r1, fn1 = run(cache_dir=td)
            r2, fn2 = run(cache_dir=td)
            self.assertEqual(fn1.calls['n'], 7)
            self.assertEqual(fn2.calls['n'], 0)
            self.assertEqual(r2['cost']['actual_usd'], 0.0)
            self.assertEqual(r2['cost']['n_cached'], 7)
            self.assertTrue(all(i['cached'] for i in r2['items'] if i['status'] == 'ok'))
            self.assertEqual([i['verdict'] for i in r1['items']], [i['verdict'] for i in r2['items']])
            self.assertEqual(r1['input_hash'], r2['input_hash'])
            self.assertEqual(W.estimate_qualitative(docs(), 0.0, cache_dir=td)['n_calls'], 0)

    def test_changed_text_calls_only_that_rule(self):
        with tempfile.TemporaryDirectory() as td:
            run(cache_dir=td)
            d = docs()
            d['documents']['EMP']['risk_assessment'] += ' 추가 문장입니다.'
            r, fn = run(d, cache_dir=td)
            self.assertEqual(fn.calls['n'], 1)                       # 위험도 서술(5d)을 판정 칸으로 받는 Q-05만 다시 부름(다른 규칙의 함께 주는 칸이 아님)
            self.assertNotEqual(r['input_hash'], run(cache_dir=td)[0]['input_hash'])

    def test_corrupted_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            run(cache_dir=td)
            for f in Path(td).glob('*.json'):
                f.write_text('{깨진 파일', encoding='utf-8')
            r, fn = run(cache_dir=td)
            self.assertEqual(fn.calls['n'], 7)
            self.assertEqual(r['status'], 'ok')

    def test_failed_items_are_not_cached_and_rerun_only_those(self):
        with tempfile.TemporaryDirectory() as td:
            ok = W.mock_call('ok')

            def fail_q03(user, **kw):
                if user.split('\n')[0].startswith('[규칙] Q-03'):
                    raise CL.AIError('네트워크 연결 실패', 'x', 0, '')
                return ok(user, **kw)
            r1, _ = run(call_fn=fail_q03, cache_dir=td)
            self.assertEqual(r1['status'], 'partial')
            r2, fn2 = run(cache_dir=td)
            self.assertEqual(fn2.calls['n'], 1)                      # 받지 못한 Q-03만 다시 부름
            self.assertEqual(r2['status'], 'ok')

    def test_cache_dir_is_inside_engine_and_named_ai_cache(self):
        self.assertEqual(W.CACHE_DIR.name, 'ai_cache')
        self.assertEqual(W.CACHE_DIR.parent.name, 'engine')


class TestSourceAndMockIsolation(Base):
    """2026-11-17 실제 호출 시험에서 나온 문제: 모의 실행이 디스크 기억에 섞여 진짜 실행에서 가짜 판정이 나옴"""

    def test_items_carry_a_source(self):
        r, _ = run()                                                                  # 모의 호출
        src = {i['rule_id']: i['source'] for i in r['items']}
        self.assertEqual(src['Q-06'], None)                                          # 호출하지 않은 항목
        self.assertTrue(all(v == 'mock' for k, v in src.items() if k != 'Q-06'))
        self.assertEqual(r['summary']['sources'], {'api': 0, 'cache': 0, 'mock': 7})
        with tempfile.TemporaryDirectory() as td:
            r1, _ = run(cache_dir=td)
            self.assertEqual(r1['summary']['sources'], {'api': 7, 'cache': 0, 'mock': 0})
            r2, _ = run(cache_dir=td)
            self.assertEqual(r2['summary']['sources'], {'api': 0, 'cache': 7, 'mock': 0})
            self.assertTrue(all(i['source'] == 'cache' and i['cached'] for i in r2['items'] if i['status'] == 'ok'))

    def test_mock_run_never_reads_or_writes_the_disk_cache(self):
        with tempfile.TemporaryDirectory() as td:
            r, fn = run(call_fn=W.mock_call('ok'), cache_dir=td)
            self.assertEqual(list(Path(td).iterdir()), [])                           # 아무것도 쓰지 않음
            self.assertEqual(r['summary']['sources']['mock'], 7)
            self.assertEqual(fn.calls['n'], 7)
            # 진짜 실행이 남긴 기억이 있어도 모의 실행은 읽지 않는다(항상 7번 호출, 기억에서 0회)
            run(cache_dir=td)
            self.assertTrue(list(Path(td).glob('*.json')))
            r2, fn2 = run(call_fn=W.mock_call('ok'), cache_dir=td)
            self.assertEqual((fn2.calls['n'], r2['summary']['sources']['cache']), (7, 0))

    def test_mock_then_real_run_does_not_use_mock_results(self):
        with tempfile.TemporaryDirectory() as td:
            run(call_fn=W.mock_call('ok'), cache_dir=td)                              # ① 모의 실행(--mock ok)
            r, fn = run(call_fn=real_like('ok'), cache_dir=td)                        # ② 진짜 실행(--weak 같은 입력)
            self.assertEqual(fn.calls['n'], 7)                                        # 모의 기억에 걸리지 않고 7번 모두 호출
            self.assertEqual(r['summary']['sources'], {'api': 7, 'cache': 0, 'mock': 0})
            self.assertEqual(W.estimate_qualitative(docs(), 0.0, cache_dir=td)['n_cached'], 7)      # ②가 남긴 진짜 기억만 있음

    def test_old_mock_cache_files_without_source_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            run(cache_dir=td)
            files = list(Path(td).glob('*.json'))
            self.assertEqual(len(files), 7)
            for f in files:                                                          # 출처 표시가 없던 옛 모의 기억(모의 문구 포함)을 흉내
                rec = json.loads(f.read_text(encoding='utf-8'))
                del rec['source']
                f.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
            r, fn = run(cache_dir=td)
            self.assertEqual(fn.calls['n'], 7)                                        # 모두 무시하고 다시 호출
            for f in files[:2]:                                                      # 출처가 mock으로 적힌 기억도 무시
                rec = json.loads(f.read_text(encoding='utf-8'))
                rec['source'] = 'mock'
                f.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
            r, fn = run(cache_dir=td)
            self.assertEqual(fn.calls['n'], 2)

    def test_old_real_cache_files_without_source_are_still_used(self):
        with tempfile.TemporaryDirectory() as td:
            run(cache_dir=td)
            for f in Path(td).glob('*.json'):                                         # 실제 응답이었던 옛 기억(모의 문구 없음, 출처 없음)
                rec = json.loads(f.read_text(encoding='utf-8'))
                del rec['source']
                rec['raw_text'] = rec['raw_text'].replace(W._MOCK_MARK, '실제 응답입니다')
                f.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
            r, fn = run(cache_dir=td)
            self.assertEqual(fn.calls['n'], 0)
            self.assertEqual(r['summary']['sources']['cache'], 7)

    def test_cli_mock_does_not_touch_the_cache_folder(self):
        import contextlib
        import io
        import run_web_qual as CLI
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(W, 'CACHE_DIR', Path(td)):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(CLI.main(['--mock', 'ok']), 0)
                self.assertEqual(list(Path(td).iterdir()), [])
                self.assertIn('읽지도 쓰지도 않습니다', out.getvalue())
                run(call_fn=real_like('ok'), cache_dir=td)                           # 진짜 기억이 있어도
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    CLI.main(['--mock', 'ok', '--dry-run'])
                self.assertIn('기억에서 0회', out.getvalue())

    def test_cli_help_works(self):
        import contextlib
        import io
        import run_web_qual as CLI
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                CLI.main(['--help'])
        self.assertEqual(cm.exception.code, 0)                                       # (%) 때문에 ValueError가 나던 문제
        self.assertIn('--mock', buf.getvalue())
        self.assertIn('데이터갭', buf.getvalue())


class TestProgressAndSpeed(Base):
    def test_items_arrive_one_by_one_and_in_parallel(self):
        t0 = time.time()
        stamps, types = [], []
        for ev in W.iter_qualitative(docs(), data_gap_pct=0.0, call_fn=W.mock_call('slow'), cache_dir=None, max_workers=4):
            types.append(ev['type'])
            stamps.append(time.time() - t0)
        total = time.time() - t0
        self.assertEqual(types[0], 'plan')
        self.assertEqual(types[-1], 'done')
        self.assertEqual(types.count('item'), 8)
        self.assertLess(total, 0.15 * 7 * 0.8)                       # 순차(약 1.05초)보다 눈에 띄게 짧음
        item_stamps = [s for s, t in zip(stamps, types) if t == 'item']
        self.assertLess(item_stamps[1], total - 0.05)                # 마지막이 끝나기 전에 앞 항목이 먼저 나옴

    def test_on_item_callback(self):
        got = []
        run(on_item=lambda item, done, total: got.append((item['rule_id'], done, total)))
        self.assertEqual(len(got), 8)
        self.assertEqual(sorted(d for _, d, _ in got), list(range(1, 9)))
        self.assertTrue(all(t == 8 for _, _, t in got))


class TestEngineIsUntouched(Base):
    def test_score_verdict_and_violations_are_the_same_before_and_after(self):
        from corsia.verify.engine import verify
        d = docs()
        before = verify(str(HERE / 'dataset'), **d)
        run(d)
        run(d, kind='weak')
        after = verify(str(HERE / 'dataset'), **d)
        self.assertEqual(before['score'], after['score'])
        self.assertEqual(before['opinion'], after['opinion'])
        self.assertEqual([(f['rule_id'], f['status']) for f in before['findings']], [(f['rule_id'], f['status']) for f in after['findings']])

    def test_module_does_not_import_the_verify_engine_entry(self):
        src = (HERE / 'corsia' / 'ai' / 'web_qual.py').read_text(encoding='utf-8')
        self.assertNotIn('scoring', src.replace('score', ''))        # 점수 계산을 가져다 쓰지 않음
        self.assertNotIn('compute_score', src)
        self.assertNotIn('resolve_opinion', src)


class TestStatusMessages(Base):
    def test_messages(self):
        self.assertEqual(W.status_message('no_key'), 'AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다')
        self.assertEqual(W.BADGE_TEXT, 'AI 참고용 · 최종 판정 아님')
        for st in W.RESULT_STATUSES:
            if st != 'ok':
                self.assertTrue(W.status_message(st), st)


if __name__ == '__main__':
    unittest.main(verbosity=1)
