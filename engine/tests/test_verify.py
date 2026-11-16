"""
검증엔진 테스트
    python tests/test_verify.py

핵심은 두 숫자다.
  - 정상본에서 걸린 규칙 = 0  (오탐)
  - 오류주입본에서 심은 규칙이 전부 걸림 (미탐)
한쪽만 재면 규칙을 헐겁게 만들어도 점수가 나온다.
"""
import csv
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from corsia.verify.scoring import compute_score                             # noqa: E402
from corsia.verify.violation_text import missing_ids                        # noqa: E402
from corsia.verify.rules import get_registry                                # noqa: E402
from corsia.verify.display import (INPUT_LABELS, SAMPLE_LABELS,             # noqa: E402
                                   format_sample, format_value)
from corsia.constants import (OPINION_SATISFACTORY, OPINION_WITH_COMMENTS,  # noqa: E402
                              OPINION_UNSATISFACTORY)
from corsia.verify import verify, get_registry, resolve_opinion  # noqa: E402
from corsia.verify.rules import (Finding, FAIL, PASS, CRITICAL, MAJOR,  # noqa: E402
                                 MINOR, BLOCKING, LAYER_FORMAT)
from corsia.verify.qualitative import build_llm_payload, VERDICTS  # noqa: E402

DATASET = BASE / 'dataset'


def load_docs(name):
    return json.loads((DATASET / name).read_text(encoding='utf-8'))


def injected_rule_ids():
    ids = set()
    with open(DATASET / 'error_injection_log.csv', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            ids.add(row['rule_id'])
    with open(DATASET / 'document_error_log.csv', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            ids.add(row['rule_id'])
    return ids


class TestRegistry(unittest.TestCase):

    def test_rules_registered(self):
        reg = get_registry()
        self.assertGreaterEqual(len(reg), 30,
                                '붙임2-2 요구치는 누적 30건 이상')

    def test_rule_ids_unique(self):
        ids = [r.id for r in get_registry()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_rule_has_source(self):
        for r in get_registry():
            self.assertTrue(r.source.strip(), f'{r.id}에 근거 조항이 없음')

    def test_all_four_layers_present(self):
        layers = {r.layer for r in get_registry()}
        self.assertEqual(layers, {'형식', '임계값', '일관성', '정성'})


class TestOpinionResolution(unittest.TestCase):
    """SARPs App.6 3.10 검증의견 3종 수렴"""

    def _f(self, severity, status=FAIL):
        return Finding('X-00', LAYER_FORMAT, severity, 't', 's', status)

    def test_critical_to_unsatisfactory(self):
        self.assertEqual(resolve_opinion([self._f(CRITICAL)])[0], OPINION_UNSATISFACTORY)

    def test_major_to_with_comments(self):
        self.assertEqual(resolve_opinion([self._f(MAJOR)])[0], OPINION_WITH_COMMENTS)

    def test_minor_to_with_comments(self):
        # ICAO: 중대하지 않은 허위진술·부적합이 있으면 '의견부 만족'으로 명시
        self.assertEqual(resolve_opinion([self._f(MINOR)])[0], OPINION_WITH_COMMENTS)

    def test_blocking_to_unsatisfactory(self):
        # ICAO: 검증 범위가 제한적이거나 데이터를 신뢰할 수 없으면 '불만족'
        self.assertEqual(resolve_opinion([self._f(BLOCKING)])[0], OPINION_UNSATISFACTORY)

    def test_critical_wins_over_major(self):
        self.assertEqual(
            resolve_opinion([self._f(MAJOR), self._f(CRITICAL)])[0], OPINION_UNSATISFACTORY)

    def test_no_failure_is_satisfactory(self):
        self.assertEqual(resolve_opinion([self._f(CRITICAL, PASS)])[0], OPINION_SATISFACTORY)


class TestCleanDataset(unittest.TestCase):
    """오탐 측정 - 정상본에서는 아무 규칙도 걸리면 안 된다"""

    @classmethod
    def setUpClass(cls):
        cls.res = verify(DATASET, **load_docs('documents.json'))

    def test_zero_false_positives(self):
        fails = [f['rule_id'] for f in self.res['findings'] if f['status'] == FAIL]
        self.assertEqual(fails, [], f'정상본 오탐: {fails}')

    def test_opinion_is_ok(self):
        self.assertEqual(self.res['opinion']['result'], OPINION_SATISFACTORY)

    def test_computed_values_match_calc_engine(self):
        c = self.res['computed']
        self.assertEqual(c['total_co2_t'], Decimal('1899987.0'))
        self.assertEqual(c['data_gap_pct'], Decimal('0.0'))

    def test_materiality_is_icao_2pct(self):
        self.assertEqual(self.res['computed']['materiality']['applied_pct'], Decimal('0.02'))

    def test_no_kr_ets_rule_left(self):
        # 환경부 검증지침 기준(T-12 병기 알림)은 적용하지 않기로 확정
        self.assertNotIn('T-12', {r.id for r in get_registry()})

    def test_q06_not_required_without_gap(self):
        """데이터갭이 없으면 '갭 초과 사유' 서술은 요구되지 않는다 (조건부 규칙)"""
        q06 = next(f for f in self.res['findings'] if f['rule_id'] == 'Q-06')
        self.assertEqual(q06['status'], PASS)

    def test_x22_uses_route_median_not_type_median(self):
        """기종 기준으로 비교하면 EU 역내 단거리편이 장거리 중앙값에 걸려
        정상 데이터가 대량 오탐된다. 노선별 비교로 바뀌었는지 확인."""
        x22 = next(f for f in self.res['findings'] if f['rule_id'] == 'X-22')
        self.assertEqual(x22['status'], PASS)


class TestDirtyFlights(unittest.TestCase):
    """미탐 측정 - 항공편 데이터에 심은 오류"""

    @classmethod
    def setUpClass(cls):
        cls.res = verify(DATASET, flights_file='flights_dirty.csv',
                         saf_file='saf_batches_dirty.csv')
        cls.fails = {f['rule_id'] for f in cls.res['findings'] if f['status'] == FAIL}

    def test_all_flight_level_injections_detected(self):
        expected = {'F-16', 'F-17', 'T-04', 'X-14b', 'X-15', 'X-16',
                    'X-18', 'X-19', 'X-20', 'X-21', 'X-22', 'X-24'}
        self.assertEqual(expected - self.fails, set(),
                         f'미탐: {expected - self.fails}')

    def test_opinion_is_fail(self):
        self.assertEqual(self.res['opinion']['result'], OPINION_UNSATISFACTORY)

    def test_data_gap_over_limit(self):
        self.assertGreater(self.res['computed']['data_gap_pct'], Decimal('5.0'))

    def test_x18_reports_the_deleted_flight(self):
        x18 = next(f for f in self.res['findings'] if f['rule_id'] == 'X-18')
        self.assertEqual(x18['samples'][0]['missing_flight_id'], 'F003302')

    def test_calc_engine_does_not_crash_on_bad_fuel_type(self):
        """F-17 위반 편이 있어도 계산은 끝까지 진행되어야 한다"""
        self.assertGreater(self.res['computed']['total_co2_t'], 0)
        self.assertEqual(len(self.res['computed']['calc_errors']), 1)


class TestDirtyDocuments(unittest.TestCase):
    """미탐 측정 - 문서에 심은 오류"""

    @classmethod
    def setUpClass(cls):
        cls.res = verify(DATASET, flights_file='flights_dirty.csv',
                         saf_file='saf_batches_dirty.csv',
                         **load_docs('documents_dirty.json'))
        cls.fails = {f['rule_id'] for f in cls.res['findings'] if f['status'] == FAIL}

    def test_all_document_injections_detected(self):
        expected = {'X-01', 'X-07', 'X-08', 'X-09', 'X-10', 'X-11', 'X-12',
                    'X-17', 'X-26', 'F-21', 'Q-01',
                    'F-01', 'F-02', 'F-03', 'F-04', 'F-05', 'F-06', 'F-07', 'F-09',
                    'F-10', 'F-26', 'F-28', 'X-02', 'X-03', 'X-04', 'X-05', 'X-06'}
        self.assertEqual(expected - self.fails, set(),
                         f'미탐: {expected - self.fails}')

    def test_every_injected_rule_detected(self):
        """주입 로그에 기록된 모든 규칙이 걸려야 한다"""
        self.assertEqual(injected_rule_ids() - self.fails, set())

    def test_q06_required_when_gap_exceeds(self):
        """갭이 5%를 넘으면 사유 서술이 요구된다"""
        q06 = next(f for f in self.res['findings'] if f['rule_id'] == 'Q-06')
        self.assertEqual(q06['status'], FAIL)

    def test_x12_uses_materiality_as_tolerance(self):
        x12 = next(f for f in self.res['findings'] if f['rule_id'] == 'X-12')
        self.assertIn('materiality_limit_t', x12['samples'][0])


class TestQualitativePayload(unittest.TestCase):
    """LLM에 넘길 페이로드 - 판정은 코드가 하지 않는다"""

    def test_payload_contains_question_and_clause(self):
        docs = load_docs('documents.json')['documents']
        payload = build_llm_payload(docs)
        self.assertGreater(len(payload), 0)
        for p in payload:
            self.assertTrue(p['question'])
            self.assertTrue(p['cited_clause'])
            self.assertIn('판단 불가', p['expected_response_schema']['verdict'])

    def test_empty_items_are_excluded(self):
        docs = load_docs('documents_dirty.json')['documents']
        ids = {p['rule_id'] for p in build_llm_payload(docs)}
        self.assertNotIn('Q-01', ids)  # 비어 있는 항목은 LLM에 보내지 않는다

    def test_verdicts_include_unable(self):
        self.assertIn('판단 불가', VERDICTS)


class TestSkippedBehaviour(unittest.TestCase):
    """자료가 없으면 통과가 아니라 SKIPPED여야 한다"""

    @classmethod
    def setUpClass(cls):
        cls.res = verify(DATASET)   # 문서 없이 실행

    def test_document_rules_are_skipped_not_passed(self):
        for rid in ('X-01', 'X-07', 'X-09', 'Q-01'):
            f = next(x for x in self.res["findings"] if x["rule_id"] == rid)
            self.assertEqual(f['status'], 'SKIPPED', f'{rid}가 SKIPPED가 아님')

    def test_skipped_does_not_affect_opinion(self):
        self.assertEqual(self.res['opinion']['result'], OPINION_SATISFACTORY)



class TestDocumentRules(unittest.TestCase):
    """5단계 잔여 규칙 - 규칙 정의를 바로잡은 부분과 SKIPPED 원칙"""

    @classmethod
    def setUpClass(cls):
        cls.docs = load_docs('documents.json')

    def _run(self, docs):
        return {f['rule_id']: f for f in verify(DATASET, **docs)['findings']}

    def test_f02_both_is_valid(self):
        """ER 서식 선택지는 3가지라 '지정기호와 등록부호 둘 다'도 정상이어야 한다"""
        d = json.loads(json.dumps(self.docs))
        for n in ('EMP', 'ER'):
            d['documents'][n]['aircraft_identification'] = 'BOTH'
            d['documents'][n]['registration_marks'] = ['HL8001', 'HL8002']
        self.assertEqual(self._run(d)['F-02']['status'], PASS)

    def test_f02_both_requires_both_values(self):
        d = json.loads(json.dumps(self.docs))
        d['documents']['ER']['aircraft_identification'] = 'BOTH'   # 등록부호 값 없음
        self.assertEqual(self._run(d)['F-02']['status'], FAIL)

    def test_missing_vr_is_skipped_not_passed(self):
        """검증보고서가 없으면 F-05·X-03·Q-07은 통과가 아니라 SKIPPED"""
        d = json.loads(json.dumps(self.docs))
        del d['documents']['VR']
        res = self._run(d)
        for rid in ('F-05', 'X-03', 'Q-07', 'Q-08'):
            self.assertEqual(res[rid]['status'], 'SKIPPED', rid)

    def test_x16_non_participant_marked_in_scope(self):
        res = verify(DATASET, flights_file='flights_dirty.csv', saf_file='saf_batches_dirty.csv')
        x16 = next(f for f in res['findings'] if f['rule_id'] == 'X-16')
        self.assertEqual(x16['status'], FAIL)
        self.assertEqual(x16['samples'][0]['expected'], '0')


class TestScoring(unittest.TestCase):
    """종합점수 (verification_rules_v1.md §5.1 · web_design_v1.md §2)"""

    @classmethod
    def setUpClass(cls):
        docs = json.loads((DATASET / 'documents.json').read_text(encoding='utf-8'))
        dd = json.loads((DATASET / 'documents_dirty.json').read_text(encoding='utf-8'))
        cls.clean = verify(DATASET, **docs)
        cls.dirty = verify(DATASET, flights_file='flights_dirty.csv',
                           saf_file='saf_batches_dirty.csv', **dd)

    def test_clean_is_full_marks(self):
        """위반 0건이면 전체·계층 모두 100.0 (만족은 항상 100점)"""
        sc = self.clean['score']
        self.assertEqual(sc['overall'], Decimal('100.0'))
        self.assertFalse(sc['capped'])
        for layer, v in sc['by_layer'].items():
            self.assertEqual(v['score'], Decimal('100.0'), layer)

    def test_dirty_matches_design(self):
        """오류주입본 실측값이 설계 문서와 일치해야 한다"""
        sc = self.dirty['score']
        self.assertEqual(sc['overall'], Decimal('25.8'))
        self.assertEqual(sc['raw'], Decimal('25.8'))
        expect = {'형식': '17.8', '임계값': '76.2', '일관성': '12.5', '정성': '75.0'}
        for layer, want in expect.items():
            self.assertEqual(sc['by_layer'][layer]['score'], Decimal(want), layer)

    def test_skipped_excluded_from_denominator(self):
        """SKIPPED는 분모에서 빠진다 - 감점도 가점도 아니다"""
        sc = self.clean['score']
        n_skipped = sum(1 for f in self.clean['findings'] if f['status'] == 'SKIPPED')
        self.assertEqual(sc['skipped_rules'], n_skipped)
        self.assertEqual(sc['scored_rules'] + n_skipped, len(self.clean['findings']))

    def test_cap_applies_only_to_overall(self):
        """치명 1건만 있으면 원점수는 높아도 전체 점수는 상한 59"""
        findings = [dict(f) for f in self.clean['findings']]
        for f in findings:
            if f['rule_id'] == 'F-04':
                f['status'] = FAIL
        sc = compute_score(findings, OPINION_UNSATISFACTORY)
        self.assertTrue(sc['capped'])
        self.assertEqual(sc['overall'], Decimal('59.0'))
        self.assertGreater(sc['raw'], Decimal('90'))
        # 계층 점수에는 상한이 걸리지 않는다
        self.assertGreater(sc['by_layer']['형식']['score'], Decimal('59.0'))

    def test_count_does_not_affect_score(self):
        """위반 건수는 점수에 반영하지 않는다 (X-19 1,102건도 1회분)"""
        base = [dict(f) for f in self.clean['findings']]
        for f in base:
            if f['rule_id'] == 'X-19':
                f['status'], f['count'] = FAIL, 1
        many = [dict(f) for f in base]
        for f in many:
            if f['rule_id'] == 'X-19':
                f['count'] = 9999
        self.assertEqual(compute_score(base, OPINION_UNSATISFACTORY)['raw'],
                         compute_score(many, OPINION_UNSATISFACTORY)['raw'])

    def test_deductions_recorded(self):
        """점수마다 어떤 규칙에서 몇 점이 깎였는지 근거가 남아야 한다"""
        sc = self.dirty['score']
        fails = {f['rule_id'] for f in self.dirty['findings'] if f['status'] == FAIL}
        self.assertEqual({d['rule_id'] for d in sc['deductions']}, fails)
        for d in sc['deductions']:
            self.assertTrue(d['basis'])
            self.assertGreater(d['points'], Decimal('0'))


class TestViolationText(unittest.TestCase):
    """위반 문구 (화면 표시용)"""

    def test_every_rule_has_violation_text(self):
        ids = [r.id for r in get_registry()]
        self.assertEqual(missing_ids(ids), [], '위반 문구가 없는 규칙이 있다')

    def test_requirement_titles_become_violations(self):
        """'...없음'·'...일치' 같은 요건 문장이 위반 문장으로 바뀌어야 한다"""
        docs = json.loads((DATASET / 'documents_dirty.json').read_text(encoding='utf-8'))
        res = verify(DATASET, flights_file='flights_dirty.csv',
                     saf_file='saf_batches_dirty.csv', **docs)
        got = {f['rule_id']: f['violation'] for f in res['findings']}
        self.assertEqual(got['X-19'], '미해결 데이터 공백 있음 (블록온 연료·급유량 누락)')
        self.assertIn('불일치', got['X-12'])
        for rid in ('X-19', 'X-12', 'F-04'):
            self.assertNotEqual(got[rid], next(
                f['title'] for f in res['findings'] if f['rule_id'] == rid))

    def test_x22_title_says_median(self):
        """X-22는 노선 중앙값 기준이다 (구현·문서와 제목이 같아야 한다)"""
        r = next(r for r in get_registry() if r.id == 'X-22')
        self.assertIn('중앙값', r.title)
        self.assertNotIn('평균', r.title)


class TestDisplayLabels(unittest.TestCase):
    """화면 표시 대응표 (display.py)"""

    def test_every_needed_input_has_label(self):
        """규칙이 요구하는 자료(needs)는 모두 한글 이름이 있어야 한다"""
        needs = {k for r in get_registry() for k in r.needs}
        self.assertEqual(sorted(needs - set(INPUT_LABELS)), [])

    def test_every_skip_reason_has_label(self):
        """서류를 하나씩 빼 보며 나오는 판정 보류 사유가 모두 대응표에 있어야 한다"""
        docs = json.loads((DATASET / 'documents.json').read_text(encoding='utf-8'))
        cases = [{}] + [{**docs, 'documents': {k: v for k, v in docs['documents'].items() if k != drop}}
                        for drop in docs['documents']]
        seen = set()
        for kw in cases:
            for f in verify(DATASET, **kw)['findings']:
                if f['status'] == 'SKIPPED':
                    self.assertTrue(f['missing_inputs'], f['rule_id'])
                    seen |= set(f['missing_inputs'])
        self.assertEqual(sorted(seen - set(INPUT_LABELS)), [], '대응표에 없는 자료 이름')
        self.assertIn('documents.VR', seen)

    def test_skip_note_is_korean(self):
        res = verify(DATASET)
        f01 = next(f for f in res['findings'] if f['rule_id'] == 'F-01')
        self.assertEqual(f01['note'], '판정에 필요한 자료 없음: 서류 JSON')

    def test_sample_format(self):
        """X-12 상세: 한글 이름, 천 단위 쉼표, 정수"""
        line = format_sample({'reported_t': 2051985.9600000002, 'computed_t': '1807081.2',
                              'diff_t': 244904.7600000002})
        self.assertEqual(line, '보고값 2,051,986 t · 재계산 1,807,081 t · 차이 244,905 t')

    def test_unlabeled_keeps_name_two_decimals(self):
        self.assertEqual(format_value('zz_ratio', 0.123456), 'zz_ratio 0.12')
        self.assertEqual(format_value('zz_id', 'F001'), 'zz_id F001')

    def test_ids_and_years_not_formatted_as_numbers(self):
        self.assertEqual(format_value('reporting_year', '2024'), '보고연도 2024')
        self.assertEqual(format_value('flight_id', 'F005002'), '항공편 F005002')

    def test_dirty_sample_keys_all_labeled(self):
        """오류주입본에서 실제로 나오는 수치 항목은 모두 한글 이름이 있어야 한다"""
        dd = json.loads((DATASET / 'documents_dirty.json').read_text(encoding='utf-8'))
        res = verify(DATASET, flights_file='flights_dirty.csv', saf_file='saf_batches_dirty.csv', **dd)
        keys = {k for f in res['findings'] for smp in f['samples'] for k in smp}
        self.assertEqual(sorted(keys - set(SAMPLE_LABELS)), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
