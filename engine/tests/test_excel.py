"""
결과 엑셀·JSON 내려받기 단위시험  (corsia/excel_export.py)
    python tests/test_excel.py        또는   python -m unittest discover -s tests -v

확인하는 것
  1. 시트 구성: 요약 / 위반 목록 / 점수 근거 / 계산 상세, 그리고 AI를 실행했을 때만 정성 검증
  2. 만든 엑셀을 다시 읽어 JSON 내려받기와 같은 값인지 (요약·위반 목록·점수 근거·계산 상세)
  3. AI 정성 검증을 붙여도 요약·위반 목록·점수 근거·계산 상세가 그대로인지 (원칙: 판정·점수는 AI와 무관)
  4. 문자열 주입 방지: 올린 파일의 값이 =, +, -, @로 시작해도 수식으로 저장되지 않는지
  5. 파일 구조(엑셀 「복구」 메시지 방지): 필수 부품, XML, 시트 이름, 수식·병합 칸 없음
  6. PC 전체 경로가 들어가지 않는지, openpyxl이 없을 때 JSON은 동작하는지

AI는 부르지 않는다(모의 응답, 기억 폴더는 임시 폴더).
"""
import copy
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook                          # noqa: E402

from corsia import excel_export as X                         # noqa: E402
from corsia.ai import web_qual                               # noqa: E402
from corsia.cli import run_pipeline                          # noqa: E402
from corsia.verify import verify                             # noqa: E402
from corsia.verify.display import skip_summary               # noqa: E402

DATASET = Path(__file__).resolve().parent.parent / 'dataset'
FIXED = datetime(2026, 11, 17, 21, 0)
NOTE = '가상 데이터 — 한빛항공은 팀이 만든 가상 항공사이며 실제 항공사의 자료가 아닙니다'
_CACHE = {}


def _results(kind):
    """(calc, res, docs). 정상본·오류주입본 결과를 한 번만 만든다."""
    if kind not in _CACHE:
        if kind == 'clean':
            docs = json.loads((DATASET / 'documents.json').read_text(encoding='utf-8'))
            calc = run_pipeline(DATASET)
            res = verify(DATASET, **docs)
        else:
            docs = json.loads((DATASET / 'documents_dirty.json').read_text(encoding='utf-8'))
            calc = run_pipeline(DATASET, 'BLOCK_OFF_ON', 'flights_dirty.csv')
            res = verify(DATASET, flights_file='flights_dirty.csv', saf_file='saf_batches_dirty.csv', **docs)
        _CACHE[kind] = (calc, res, docs)
    return _CACHE[kind]


def _ai(docs, res, kind='weak'):
    """모의 응답으로 만든 AI 정성 검증 결과 (기억 폴더는 임시 폴더라 engine/ai_cache를 건드리지 않는다)."""
    tmp = tempfile.mkdtemp()
    try:
        return web_qual.run_qualitative(docs, data_gap_pct=res['computed']['data_gap_pct'],
                                        call_fn=web_qual.mock_call(kind), cache_dir=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build(kind, qual=None, **kw):
    calc, res, _ = _results(kind)
    return X.build_workbook(calc, res, qual, operator='한빛항공 주식회사', data_note=NOTE, created=FIXED, **kw)


def _read(data):
    return load_workbook(io.BytesIO(data))


def read_table(ws, first_label):
    """첫 칸이 first_label인 줄을 머리글로 보고, 빈 줄이 나올 때까지 행을 {열 이름: 값}으로 읽는다."""
    rows = list(ws.iter_rows(values_only=True))
    for i, r in enumerate(rows):
        if r and r[0] == first_label:
            head = [h for h in r if h is not None]
            out = []
            for rr in rows[i + 1:]:
                if rr[0] is None:
                    break
                out.append({h: rr[j] for j, h in enumerate(head)})
            return out
    raise AssertionError(f'{first_label}로 시작하는 표를 찾지 못했습니다 ({ws.title})')


def kv(ws):
    """요약·점수 근거 위쪽처럼 (이름, 값)이 두 칸씩인 줄 → {이름: 값}"""
    return {r[0]: r[1] for r in ws.iter_rows(values_only=True) if r and r[0] is not None and len(r) > 1}


def _json_of(kind, qual=None):
    calc, res, _ = _results(kind)
    return json.loads(X.to_json_bytes(X.result_payload(calc, res, qual)).decode('utf-8'))


class TestSheets(unittest.TestCase):
    def test_four_sheets_without_ai(self):
        wb = _read(_build('clean'))
        self.assertEqual(wb.sheetnames, ['요약', '위반 목록', '점수 근거', '계산 상세'])

    def test_five_sheets_with_ai(self):
        calc, res, docs = _results('clean')
        wb = _read(_build('clean', _ai(docs, res)))
        self.assertEqual(wb.sheetnames, ['요약', '위반 목록', '점수 근거', '계산 상세', '정성 검증'])

    def test_partial_ai_included_failed_ai_not(self):
        calc, res, docs = _results('clean')
        q = _ai(docs, res)
        part = copy.deepcopy(q)
        part['status'] = 'partial'
        self.assertIn('정성 검증', _read(_build('clean', part)).sheetnames)
        for st in ('no_key', 'network_error', 'budget_exceeded', 'no_text'):
            bad = copy.deepcopy(q)
            bad['status'] = st
            self.assertNotIn('정성 검증', _read(_build('clean', bad)).sheetnames, st)
        self.assertNotIn('정성 검증', _read(_build('clean', None)).sheetnames)


class TestParityWithJson(unittest.TestCase):
    """엑셀의 값이 JSON 내려받기의 값과 같다 (같은 결과 객체에서 만들기 때문)"""

    def _check(self, kind):
        j = _json_of(kind)
        v = j['verification']
        wb = _read(_build(kind))

        s = kv(wb['요약'])
        fails = [f for f in v['findings'] if f['status'] == 'FAIL']
        self.assertEqual(s['최종 판정 (ICAO 3종)'], v['opinion']['result'])
        self.assertAlmostEqual(s['종합점수 (보조 지표)'], float(v['score']['overall']), places=6)
        self.assertEqual(s['최종 상쇄의무량 (t)'], v['computed']['FOR_t'])
        self.assertEqual(s['위반 건수 (규칙 수)'], len(fails))
        self.assertEqual(s['판정 보류 규칙 수'], sum(1 for f in v['findings'] if f['status'] == 'SKIPPED'))
        self.assertEqual(s['법인명'], '한빛항공 주식회사')
        self.assertEqual(s['보고연도'], v['meta']['reporting_year'])
        self.assertIn('가상 데이터', s['데이터 구분'])
        self.assertEqual(s['엔진 버전'], __import__('corsia').__version__)
        if v['opinion']['result'] == '불만족':
            self.assertIn('검증을 통과하지 못한', s['상쇄의무량 안내'])

        rows = read_table(wb['위반 목록'], '번호') if fails else []
        self.assertEqual(len(rows), len(fails))
        ded = {d['rule_id']: d for d in v['score']['deductions']}
        order = {'치명': 0, '중요': 1, '경미': 2}
        want = sorted(fails, key=lambda f: (order.get(f['severity'], 9), f['rule_id']))
        for row, f in zip(rows, want):
            self.assertEqual(row['규칙'], f['rule_id'])
            self.assertEqual(row['심각도'], f['severity'])
            self.assertEqual(row['위반 내용'], f['violation'])
            self.assertEqual(row['근거 조항'], f['source'])           # 엔진에 적힌 전체 (자르지 않음)
            self.assertEqual(row['건수'], f['count'])
            self.assertAlmostEqual(row['감점(점)'], float(ded[f['rule_id']]['points']), places=6)

        ws = wb['점수 근거']
        layers = {r['계층']: r for r in read_table(ws, '계층')}
        for name, lv in v['score']['by_layer'].items():
            self.assertAlmostEqual(layers[name]['점수'], float(lv['score']), places=6)
            self.assertEqual(layers[name]['규칙 수'], lv['rules'])
            self.assertEqual(layers[name]['위반 수'], lv['failed'])
            self.assertEqual(layers[name]['산식'], lv['formula'])
        dr = [r for r in read_table(ws, '규칙') if r['규칙'] != '감점 없음']      # 감점이 없으면 「감점 없음」 안내 줄이 들어간다
        self.assertEqual({r['규칙'] for r in dr}, set(ded))
        for r in dr:
            self.assertAlmostEqual(r['감점(점)'], float(ded[r['규칙']]['points']), places=6)
            self.assertEqual(r['산출 근거'], ded[r['규칙']]['basis'])
        kvs = kv(ws)
        self.assertAlmostEqual(kvs['원점수'], float(v['score']['raw']), places=6)
        self.assertAlmostEqual(kvs['전체 종합점수'], float(v['score']['overall']), places=6)
        self.assertEqual(kvs['상한 기준'], '팀 자체 보조 기준 (ICAO 기준 아님)')

        calc_rows = {r['항목']: r for r in read_table(wb['계산 상세'], '항목')}
        c = v['computed']
        self.assertAlmostEqual(calc_rows['총 CO₂ (C06)']['값'], c['total_co2_t'], places=6)
        self.assertAlmostEqual(calc_rows['상쇄 대상 배출량']['값'], c['OE_y_t'], places=6)
        self.assertAlmostEqual(calc_rows['상쇄의무량 (C08)']['값'], c['OR_y_t'], places=6)
        self.assertAlmostEqual(calc_rows['적격연료 감축 (C10)']['값'], c['ER_y_t'], places=6)
        self.assertEqual(calc_rows['최종 상쇄의무량 (C11)']['값'], c['FOR_t'])
        self.assertAlmostEqual(calc_rows['중요성 임계']['값'], c['materiality']['limit_t'], places=6)
        self.assertAlmostEqual(calc_rows['중요성 적용률']['값'], c['materiality']['applied_pct'] * 100, places=6)
        self.assertEqual(calc_rows['최종 상쇄의무량 (C11)']['단위'], 't')
        return wb

    def test_clean_matches_json(self):
        wb = self._check('clean')
        rows = list(wb['위반 목록'].iter_rows(values_only=True))
        self.assertTrue(any(r[0] and '위반 없음' in str(r[0]) for r in rows))
        self.assertEqual(kv(wb['요약'])['종합점수 (보조 지표)'], 100.0)
        self.assertEqual(kv(wb['요약'])['최종 상쇄의무량 (t)'], 265773)

    def test_dirty_matches_json(self):
        wb = self._check('dirty')
        s = kv(wb['요약'])
        self.assertEqual(s['위반 건수 (규칙 수)'], 41)
        self.assertEqual(s['위반 심각도'], '치명 22 · 중요 15 · 경미 4')
        self.assertEqual(s['종합점수 (보조 지표)'], 25.8)

    def test_number_formats(self):
        """단위·천 단위 쉼표: 숫자 칸은 숫자로 저장하고 표시 서식을 준다"""
        ws = _read(_build('dirty'))['계산 상세']
        fm = {r[0].value: r[1] for r in ws.iter_rows(min_row=4) if r[0].value}
        self.assertEqual(fm['총 CO₂ (C06)'].number_format, '#,##0.0')
        self.assertEqual(fm['최종 상쇄의무량 (C11)'].number_format, '#,##0')
        self.assertEqual(fm['상쇄의무량 (C08)'].number_format, '#,##0.00')
        for label in ('총 CO₂ (C06)', '최종 상쇄의무량 (C11)', '중요성 임계'):
            self.assertIsInstance(fm[label].value, (int, float), label)   # 글자가 아니라 숫자
        top = _read(_build('dirty'))['요약']
        cells = {r[0].value: r[1] for r in top.iter_rows() if r[0].value}
        self.assertEqual(cells['최종 상쇄의무량 (t)'].number_format, '#,##0')
        self.assertEqual(cells['종합점수 (보조 지표)'].number_format, '0.0')


class TestAiIsolation(unittest.TestCase):
    """AI 정성 검증을 붙여도 판정·종합점수·위반 목록·점수 근거·계산 상세는 그대로다 (원칙 1)"""

    def test_other_sheets_identical_with_and_without_ai(self):
        for kind in ('clean', 'dirty'):
            calc, res, docs = _results(kind)
            q = _ai(json.loads((DATASET / 'documents.json').read_text(encoding='utf-8')), res)
            a, b = _read(_build(kind)), _read(_build(kind, q))
            for name in ('위반 목록', '점수 근거', '계산 상세'):
                ra = [[c.value for c in r] for r in a[name].iter_rows()]
                rb = [[c.value for c in r] for r in b[name].iter_rows()]
                self.assertEqual(ra, rb, f'{kind}/{name}')
            sa, sb = kv(a['요약']), kv(b['요약'])
            self.assertEqual(sa['AI 정성 검증'], '실행하지 않음')
            self.assertIn('실행함', sb['AI 정성 검증'])
            sa.pop('AI 정성 검증'), sb.pop('AI 정성 검증')
            self.assertEqual(sa, sb)                       # 판정·점수·상쇄의무량 등 나머지 요약은 같다

    def test_summary_has_no_ai_verdicts(self):
        calc, res, docs = _results('clean')
        wb = _read(_build('clean', _ai(docs, res)))
        text = ' '.join(str(v) for r in wb['요약'].iter_rows(values_only=True) for v in r if v)
        for word in ('보완 필요', '판단 불가'):
            self.assertNotIn(word, text)

    def test_qual_sheet_columns_and_badge(self):
        calc, res, docs = _results('clean')
        q = _ai(docs, res)
        ws = _read(_build('clean', q))['정성 검증']
        text = [r[0] for r in ws.iter_rows(values_only=True) if r and r[0]]
        self.assertIn('AI 참고용 · 최종 판정 아님', text)
        rows = read_table(ws, '규칙')
        want = web_qual.excel_rows(q)
        self.assertEqual(len(rows), len(want))
        self.assertEqual(list(rows[0]), list(want[0]))                   # 사용법 5절의 열 이름 그대로
        self.assertEqual([r['규칙'] for r in rows], [f'Q-0{i}' for i in range(1, 9)])
        for r, w in zip(rows, want):
            self.assertEqual((r['AI 참고 판정'], r['근거 조항']), (w['AI 참고 판정'], w['근거 조항']))


class TestInjection(unittest.TestCase):
    """올린 파일의 값이 =, +, -, @로 시작해도 수식으로 저장되지 않는다"""

    EVIL = ['=HYPERLINK("http://example.com","x")', '=1+1', '+cmd|calc', '-2+3', '@SUM(1,1)', '=SUM(A1:A2)']

    def _evil_results(self):
        calc, res, docs = _results('dirty')
        res = copy.deepcopy(res)
        fails = [f for f in res['findings'] if f['status'] == 'FAIL']
        for i, f in enumerate(fails[:6]):
            f['violation'] = self.EVIL[i]
            f['source'] = self.EVIL[(i + 1) % 6]
            f['samples'] = [{'flight_id': self.EVIL[i], 'note': self.EVIL[(i + 2) % 6]}]
        res['meta']['flights_file'] = '=evil.csv'
        return calc, res

    def test_no_formulas_anywhere(self):
        calc, res = self._evil_results()
        _, _, docs = _results('clean')
        q = _ai(docs, res)
        q['items'][0]['reason'] = '=SUM(1,1)'
        q['items'][1]['missing_points'] = ['+악성', '@악성']
        data = X.build_workbook(calc, res, q, operator='=HYPERLINK("http://example.com","x")',
                                data_note='+가상', created=FIXED)
        wb = _read(data)
        n_text = 0
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    self.assertNotEqual(c.data_type, 'f', f'수식으로 저장됨: {ws.title}!{c.coordinate} {c.value!r}')
                    if isinstance(c.value, str) and c.value[:1] in '=+-@':
                        n_text += 1
        self.assertGreater(n_text, 6)                        # 위험한 글자가 실제로 칸에 들어갔고, 전부 문자열이다
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                    self.assertNotRegex(z.read(name).decode('utf-8'), r'<f[ >]', name)

    def test_text_preserved_exactly(self):
        calc, res = self._evil_results()
        ws = _read(X.build_workbook(calc, res, operator='=1+1', created=FIXED))['요약']
        self.assertEqual(kv(ws)['법인명'], '=1+1')
        self.assertEqual(kv(ws)['비행 기록 파일'], '=evil.csv')

    def test_illegal_characters_removed_and_long_text_cut(self):
        calc, res, _ = _results('clean')
        data = X.build_workbook(calc, res, operator='한빛\x00\x0b항공', data_note='가' * 40000, created=FIXED)
        s = kv(_read(data)['요약'])
        self.assertEqual(s['법인명'], '한빛항공')
        self.assertLessEqual(len(s['데이터 구분']), X.MAX_TEXT + 1)


class TestStructure(unittest.TestCase):
    """엑셀 「복구」 메시지가 나올 만한 구조 문제가 없는지 (엑셀 자체는 못 열어 보므로 구조를 확인)"""

    def test_package_parts_and_xml(self):
        calc, res, docs = _results('dirty')
        data = _build('dirty', _ai(json.loads((DATASET / 'documents.json').read_text(encoding='utf-8')), res))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            for need in ('[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels',
                         'xl/styles.xml'):
                self.assertIn(need, names)
            self.assertEqual(sum(1 for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)), 5)
            for n in names:
                if n.endswith('.xml') or n.endswith('.rels'):
                    ET.fromstring(z.read(n))                  # XML이 깨지지 않았다
            for n in names:
                if n.startswith('xl/worksheets/sheet'):
                    x = z.read(n).decode('utf-8')
                    self.assertNotIn('<mergeCell', x)          # 병합 칸 없음
                    self.assertNotIn('<dataValidation', x)

    def test_sheet_names_valid(self):
        wb = _read(_build('clean'))
        for n in wb.sheetnames:
            self.assertLessEqual(len(n), 31)
            self.assertIsNone(re.search(r'[\[\]:*?/\\]', n))
        self.assertEqual(len(set(wb.sheetnames)), len(wb.sheetnames))

    def test_no_pc_paths(self):
        calc, res, docs = _results('dirty')
        q = _ai(json.loads((DATASET / 'documents.json').read_text(encoding='utf-8')), res)
        wb = _read(_build('dirty', q))
        text = '\n'.join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None)
        text += X.to_json_bytes(X.result_payload(calc, res, q)).decode('utf-8')
        for pat in (r'[A-Za-z]:\\', r'/home/', r'/Users/', r'/tmp/', r'\\Users\\'):
            self.assertIsNone(re.search(pat, text), pat)

    def test_deterministic(self):
        self.assertEqual(_read(_build('clean')).sheetnames, _read(_build('clean')).sheetnames)

    @unittest.skipUnless(shutil.which('soffice') or shutil.which('libreoffice'), 'LibreOffice 없음')
    @unittest.skipUnless(__import__('os').environ.get('CORSIA_TEST_LIBREOFFICE') == '1',
                         '느려서 기본은 건너뜀 (CORSIA_TEST_LIBREOFFICE=1 로 켠다)')
    def test_opens_in_libreoffice(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / 'r.xlsx').write_bytes(_build('dirty'))
            exe = shutil.which('soffice') or shutil.which('libreoffice')
            r = subprocess.run([exe, '--headless', '--convert-to', 'pdf', '--outdir', str(tmp), str(tmp / 'r.xlsx')],
                               capture_output=True, timeout=180)
            self.assertEqual(r.returncode, 0)
            self.assertTrue((tmp / 'r.pdf').exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestJsonAndFallback(unittest.TestCase):
    def test_payload_has_qualitative_only_when_given(self):
        calc, res, docs = _results('clean')
        self.assertEqual(list(X.result_payload(calc, res)), ['calc', 'verification'])
        q = _ai(docs, res)
        p = X.result_payload(calc, res, q)
        self.assertEqual(list(p), ['calc', 'verification', 'qualitative'])
        self.assertEqual(p['qualitative']['schema'], 'qualitative_result_v1')

    def test_json_keeps_korean_and_numbers(self):
        calc, res, _ = _results('clean')
        raw = X.to_json_bytes(X.result_payload(calc, res)).decode('utf-8')
        self.assertTrue('심각도 가중 감점' in raw and '\\u' not in raw)      # 한글이 그대로 들어 있고 \\u 형태로 깨지지 않았다
        self.assertEqual(json.loads(raw)['verification']['computed']['FOR_t'], 265773)

    def test_without_openpyxl_json_still_works(self):
        calc, res, _ = _results('clean')
        with mock.patch.object(X, 'HAVE_OPENPYXL', False):
            with self.assertRaises(ImportError) as cm:
                X.build_workbook(calc, res)
            self.assertIn('openpyxl', str(cm.exception))
            self.assertTrue(X.to_json_bytes(X.result_payload(calc, res)))

    def test_skip_summary(self):
        self.assertEqual(skip_summary([]), '')
        s = skip_summary([{'missing_inputs': ['documents']}, {'missing_inputs': ['documents', 'afbr_values']}])
        self.assertTrue(s.startswith('일부 규칙 2개는 자료가 없어 검증하지 못했습니다'))
        self.assertIn('서류 JSON을 함께 올리면 검증됩니다', s)
        self.assertIn('기종별 평균 연료소모율(AFBR) 값', s)


if __name__ == '__main__':
    unittest.main(verbosity=2)
