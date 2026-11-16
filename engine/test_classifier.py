"""8단계 분류기 단위시험 (AI 호출 없음). engine 폴더에서:  python test_classifier.py"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings('ignore')

import classify_items as CI  # noqa: E402
from corsia.classify import catalog as C  # noqa: E402
from corsia.classify import golden as G  # noqa: E402
from corsia.classify import prompt as P  # noqa: E402
from corsia.classify import report as RP  # noqa: E402
from corsia.classify import rules as R  # noqa: E402
from corsia.classify import validate as V  # noqa: E402

FORM = C.ROOT / '6_goldenset' / 'expert_request' / '골든셋_작성양식_v1.xlsx'
DOMESTIC_EXAMPLES = ['F0161', 'F0206', 'F0469', 'F0571']


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fill_form(dst, answers):
    """양식 사본에 정답을 채운다. answers: {ID: {속성: 값}}"""
    wb = openpyxl.load_workbook(FORM)
    ws = next(wb[n] for n in wb.sheetnames if '작성할항목' in n)
    rows = list(ws.iter_rows())
    hi = next(i for i, r in enumerate(rows) if any(c.value == 'ID' for c in r))
    head = {c.value: c.column for c in rows[hi] if c.value}
    for r in rows[hi + 1:]:
        iid = r[head['ID'] - 1].value
        if iid in answers:
            for a, v in answers[iid].items():
                ws.cell(row=r[0].row, column=head[a], value=v)
    wb.save(dst)


class TestCatalog(unittest.TestCase):
    def test_counts_and_examples(self):
        items, K = C.load_catalog()
        self.assertEqual(len(items), 667)
        g = C.gold_of_examples()
        self.assertEqual(sorted(g), sorted(C.EXAMPLE_IDS))
        self.assertEqual(g['F0664']['주체'], '검증기관')

    def test_domestic_section_lookup(self):
        ctx = C.context()
        got = {i: C.domestic_row(ctx['by_id'][i], ctx['K'])['섹션'] for i in DOMESTIC_EXAMPLES}
        self.assertEqual(got, {'F0161': '5', 'F0206': '3', 'F0469': '3', 'F0571': '2'})

    def test_link_label_and_candidates(self):
        ctx = C.context()
        self.assertEqual(C.link_label(ctx['by_id']['F0664']), '1.6 VR!s2')
        for iid in ('F0469', 'F0664'):                        # 견본의 정답 연결필드가 후보 안에 들어온다
            want = set(C.gold_of_examples()[iid]['연결필드'].split(' / '))
            self.assertTrue(want <= {C.link_label(c) for c, _ in C.candidates(iid)}, iid)

    def test_candidates_are_from_other_templates(self):
        ctx = C.context()
        for iid in ('F0003', 'F0161', 'F0548'):
            t = ctx['by_id'][iid]['템플릿']
            self.assertTrue(all(c['템플릿'] != t for c, _ in C.candidates(iid)))


class TestRules(unittest.TestCase):
    def test_code_decidable_attributes_match_examples(self):
        g = C.gold_of_examples()
        for iid, gold in g.items():
            p = R.rule_values(iid)
            for a in ('주체', '단계', '시점·기한', '입력유형', '의무성'):
                self.assertEqual(p[a], gold[a], (iid, a))
        for iid in DOMESTIC_EXAMPLES:                          # 국내 서식이 있는 칸의 근거는 매핑에서 조립
            self.assertEqual(R.rule_values(iid)['근거'], g[iid]['근거'], iid)

    def test_known_limits_of_rules(self):
        g = C.gold_of_examples()
        self.assertNotEqual(R.rule_values('F0469')['연결필드'], g['F0469']['연결필드'])   # 연결필드는 코드가 못 찾음
        self.assertNotEqual(R.rule_values('F0664')['근거'], g['F0664']['근거'])           # VR 근거는 글을 읽어야 함

    def test_fixed_flags(self):
        p = R.rule_predict('F0469')
        self.assertTrue(p['입력유형']['fixed'] and p['근거']['fixed'] and p['주체']['fixed'])
        self.assertFalse(p['의무성']['fixed'] or p['연결필드']['fixed'])
        self.assertTrue(R.rule_predict('F0664')['입력유형']['fixed'])                    # 선택 목록 → 확정
        self.assertFalse(R.rule_predict('F0664')['근거']['fixed'])

    def test_oom_rules_from_the_1116_meeting(self):
        oom = [it['ID'] for it in C.context()['items'] if it['템플릿'] == '1.4 OoM' and it['ID'] not in C.template_rules()['소제목_줄']['ids']]
        self.assertEqual(len(oom), 65)                                        # OoM 83개 중 제목 줄 18개(질문 줄 10 + a1~a8)는 제외
        p = R.rule_predict('F0471')
        self.assertEqual((p['주체']['value'], p['단계']['value'], p['시점·기한']['value']), ('국토교통부', '보고', '매년 7/31'))
        self.assertTrue(all(p[a]['fixed'] for a in ('주체', '단계', '시점·기한', '입력유형', '근거')))
        self.assertEqual(p['근거']['value'], 'Annex16 Vol.IV Part II §2.4.1.6 / Annex16 Vol.IV App.1 / ETM 부록1 1.4 OoM 1')   # 문항 번호만
        self.assertEqual(R.rule_values('F0477')['근거'].split(' / ')[-1], 'ETM 부록1 1.4 OoM 32')
        self.assertNotIn('a)', R.rule_values('F0536')['근거'])
        for i in oom[:10]:
            self.assertIn(R.rule_predict(i)['의무성']['value'], ('강행', '조건부'))

    def test_condition_cues_ignore_attachment_condition(self):
        self.assertIsNone(R.COND.search(R.COND_IGNORE.sub(' ', '(필요시 첨부) 모든 항공기의 목록')))
        self.assertTrue(R.COND.search('현재 2차 자료출처가 사용되지 않을 경우'))

    def test_input_type_priority(self):
        cells = C.template_cells()
        dd = [i for i, v in cells.items() if v['dropdown']][:5]
        for iid in dd:
            if iid in C.context()['by_id']:
                self.assertEqual(R._input_type(C.context()['by_id'][iid])[0], '선택')


class TestRulesV2(unittest.TestCase):
    """2026-11-15 표본 30건 분석 뒤의 규칙 보정(입력유형 v2, OoM 선택 목록, 섹션 단위 근거, 소제목 설정)"""

    def test_sample_false_positives_are_fixed(self):
        tp = {i: R.rule_values(i)['입력유형'] for i in ('F0084', 'F0085', 'F0126', 'F0579', 'F0006')}
        self.assertEqual(tp['F0084'], '텍스트')       # 형제 열과 같은 원문 설명의 「procedures」 때문에 서술로 잡히던 것
        self.assertEqual(tp['F0085'], '서술')         # 7×15 큰 칸
        self.assertEqual(tp['F0126'], '서술')         # 큰 칸이 「density」 단서보다 먼저
        self.assertEqual(tp['F0579'], '텍스트')       # 1×1 열 머리글 「Methodology」는 서술 단서가 아님
        self.assertEqual(tp['F0006'], '텍스트')       # 1×4 세로 병합은 큰 칸이 아님 + 날짜 서식(합의 안건 11 제안)

    def test_oom_status_column_is_a_dropdown(self):
        oom = [it['ID'] for it in C.context()['items'] if it['템플릿'] == '1.4 OoM']
        n = sum(1 for i in oom if R.rule_values(i)['입력유형'] == '선택')
        self.assertEqual(n, 54)                                   # x14 확장 데이터 검증을 읽게 되어 OoM 선택 목록 항목 64개, 그중 제목 줄(질문 줄) 10개를 뺀 54개
        for i in ('F0471', 'F0474', 'F0475', 'F0476', 'F0477', 'F0536'):   # 골든셋 1차 OoM 6건(작성자 4명이 모두 선택)
            self.assertEqual(R.rule_values(i)['입력유형'], '선택', i)
            self.assertTrue(R.rule_predict(i)['입력유형']['fixed'])
        self.assertIn('Not Applicable', C.template_cells()['F0471']['options'])

    def test_question_text_and_text_format_do_not_trigger_numeric(self):
        item = dict(C.context()['by_id']['F0481'])               # OoM 메모 열(Notes), 질문 항목명 + 숫자 단어
        item = dict(item, 항목명='Are there pairs with more than 250 tonnes of fuel?', 세부라벨=None, 원문_설명=None)
        item['원문 설명'] = None
        self.assertNotEqual(R._input_type(item)[0], '수치')

    def test_date_setting_is_configurable(self):
        from unittest import mock
        rules = C.template_rules()
        rules['입력유형_설정'] = {'날짜 서식 칸': '수치'}
        with mock.patch.object(C, 'template_rules', lambda: rules):
            self.assertEqual(R.rule_values('F0006')['입력유형'], '수치')
        self.assertEqual(R.rule_values('F0006')['입력유형'], '텍스트')

    def test_section_level_basis_is_assembled(self):
        p = R.rule_predict('F0579')['근거']
        self.assertTrue(p['fixed'])
        self.assertEqual(p['value'].split(' / ')[:2], ['지침 별지 제4호서식 3', '지침 별표 7 3'])
        toc = next(i for i, it in C.context()['by_id'].items() if str(it.get('매핑') or '').startswith('회의 결정 9'))
        self.assertFalse(R.rule_predict(toc)['근거']['fixed'])      # 목차 줄(11개)은 그대로

    def test_heading_ids_equal_the_team_criteria_computed_from_the_catalog(self):
        h = C.template_rules()['소제목_줄']
        self.assertEqual(h['정책'], 'exclude')
        self.assertEqual(len(h['ids']), 176)
        ids, cnt = C.compute_heading_ids()
        self.assertEqual(cnt, {'A': 157, 'B': 11, 'C': 8})            # 팀 정정 전달문(2026-11-16 밤)의 기준별 건수
        self.assertEqual(ids, set(h['ids']), '카탈로그가 바뀌었으면 규칙표의 소제목 ids를 다시 계산해야 함')
        by = {}
        for i in ids:
            by[C.context()['by_id'][i]['템플릿']] = by.get(C.context()['by_id'][i]['템플릿'], 0) + 1
        self.assertEqual(by, {'1.1 EMP': 62, '1.2 ER': 34, '1.3 CEF': 28, '1.4 OoM': 18, '1.5 EUCR': 7, '1.6 VR': 27})
        old17 = set('F0009 F0010 F0388 F0395 F0398 F0432 F0441 F0454 F0468 F0491 F0495 F0504 F0509 F0513 F0527 F0534 F0539'.split())
        self.assertTrue(old17 <= ids)
        for i in ('F0061', 'F0227', 'F0256', 'F0278', 'F0291', 'F0302', 'F0674', 'F0675'):   # 어제 「소제목이 아니다」라고 했던 8개는 정정되어 제목 줄
            self.assertTrue(R.is_excluded(i), i)
        for i in ('F0252', 'F0275', 'F0003', 'F0161', 'F0471'):          # 입력 항목은 제외되지 않음
            self.assertFalse(R.is_excluded(i), i)
        self.assertIn('F0571', ids)                                          # 견본 F0571도 A형 기준에 해당하지만 팀이 확정한 정답이 있어 제외하지 않음(팀 확인 필요)
        self.assertFalse(R.is_excluded('F0571'))
        self.assertEqual(len(C.context()['items']) - len(ids), 491)         # 팀 전달문의 「입력 항목 약 492개」는 F0571을 포함한 수
        self.assertEqual(len(C.context()['items']) - len(ids - set(C.EXAMPLE_IDS)), 492)
        for i in h['ids']:
            if i in C.EXAMPLE_IDS:
                continue
            self.assertTrue(all(d['value'] == '' for d in R.rule_predict(i).values()))

    def test_golden_items_that_are_headings_have_blank_gold(self):
        h = set(C.template_rules()['소제목_줄']['ids'])
        self.assertEqual(h & set(G.form_ids(FORM, '1차')), {'F0495', 'F0673'})   # 골든셋 1차의 제목 줄 2개는 정답을 비워 채점 제외

    def test_heading_policy_hooks_can_be_switched(self):
        from unittest import mock
        rules = C.template_rules()
        r_none = dict(rules, 소제목_줄={'정책': 'none', 'ids': ['F0454']})
        r_blank = dict(rules, 소제목_줄={'정책': 'blank_input', 'ids': ['F0454']})
        with mock.patch.object(C, 'template_rules', lambda: r_none):
            base = R.rule_values('F0454')
            self.assertNotEqual(base['주체'], '')
            self.assertFalse(R.is_excluded('F0454'))
        with mock.patch.object(C, 'template_rules', lambda: r_blank):
            p = R.rule_predict('F0454')
            self.assertEqual(p['입력유형']['value'], '')
            self.assertEqual(p['주체']['value'], base['주체'])
            self.assertFalse(R.is_excluded('F0454'))

    def test_excluded_headings_skip_the_ai(self):
        from unittest import mock
        rules = C.template_rules()
        r2 = dict(rules, 소제목_줄={'정책': 'exclude', 'ids': ['F0454']})
        with mock.patch.object(C, 'template_rules', lambda: r2), tempfile.TemporaryDirectory() as td:
            self.assertEqual(CI.main(['--out', td, '--set', 'F0003,F0454', '--variant', 'hybrid', '--mock', 'rule', '--label', 'h']), 0)
            run = next(Path(td).iterdir())
            recs = {r['id']: r for r in RP.load_predictions(run)['hybrid'].values()}
            self.assertEqual(recs['F0454']['values']['입력유형'], '')
            self.assertIn('_줄', recs['F0454']['flags'])
            self.assertEqual(recs['F0454']['confidence'], '')          # AI를 부르지 않음
            self.assertTrue(recs['F0003']['values']['의무성'])

    def test_heading_candidates_tool(self):
        sys.path.insert(0, str(C.ROOT / '8_classifier' / 'tools'))
        import make_heading_candidates as H
        ids = {x['it']['ID'] for x in H.candidates()}
        self.assertTrue({'F0454', 'F0527', 'F0495'} <= ids)          # 표본·선례의 소제목 줄이 후보에 있음
        self.assertTrue(all(C.context()['by_id'][i]['시트'] != 'Template Information' for i in ids))
        self.assertEqual(len(ids), 24)


class TestMeeting1116(unittest.TestCase):
    """2026-11-16 합의 회의 반영: 날짜 별칭, 근거 기본/추가 인용, 카드 예시 채점 제외, 임시 파일 건너뛰기, 행 머리글 근거"""

    def test_deadline_alias_counts_as_same(self):
        self.assertTrue(G.same('시점·기한', '이행완수연도 4/30', '이행기간 종료 다음다음 해 4/30'))
        self.assertFalse(G.same('시점·기한', '이행완수연도 4/30', '매년 4/30'))
        self.assertEqual(V.check_value('F0664', '시점·기한', '이행기간 종료 다음다음 해 4/30'), [])
        self.assertEqual(V.check_value('F0664', '시점·기한', '이행완수연도 4/30'), [])
        self.assertEqual(V.check_value('F0471', '시점·기한', '매년 7/31'), [])

    def test_etm_section_citation_is_a_valid_format(self):
        for c in ('ETM §3.3.4.4 표 3-11', 'Annex16 Vol.IV Part II §2.5.1.1~4', 'Annex16 Vol.IV Part II §2.3.3.3'):
            self.assertEqual(V.check_value('F0475', '근거', c), [], c)
        self.assertTrue(V.check_value('F0475', '근거', 'ETM 아무거나'))

    def test_card_examples_are_off_by_default(self):
        card = P.reference_card()
        self.assertNotIn('## OoM 문항 작성 예', card)                                        # 골든셋 항목 예시는 기본으로 넣지 않음
        self.assertIn('점검하는 값이 다른 서식의 특정 칸과 정확히 같은 값일 때만', card)        # 안건 4 문구는 카드에 있음
        self.assertIn('점검할 대상 서류·값이 없으면 하지 않는 문항은 「조건부」', card)         # 안건 13 문구
        self.assertEqual(C.card_examples(), [])
        self.assertEqual(C.card_example_cells(), set())

    def test_card_examples_on_adds_examples_and_excludes_those_cells(self):
        try:
            C.set_card_examples(True)
            card = P.reference_card()
            self.assertIn('## OoM 문항 작성 예', card)
            self.assertIn('연결필드 1.2 ER!5.1a', card)
            cells = C.card_example_cells()
            self.assertEqual(len(cells), 12)
            gold = {'F0475': {'gold': {a: '' for a in C.ATTRS}}, 'F0003': {'gold': {a: '' for a in C.ATTRS}}}
            gold['F0475']['gold'].update({'연결필드': '1.2 ER!5.1a', '의무성': '조건부', '주체': '국토교통부'})
            gold['F0003']['gold'].update({'의무성': '강행'})
            res, _ = G.score({'F0475': {'연결필드': '-', '의무성': '강행', '주체': '국토교통부'}, 'F0003': {'의무성': '강행'}}, gold, exclude_cells=cells)
            self.assertEqual(res['연결필드']['n'], 0)
            self.assertEqual(res['의무성']['n'], 1)
            self.assertEqual(res['주체']['n'], 1)
        finally:
            C.set_card_examples(False)

    def test_run_config_records_card_examples_and_scoring_follows_it(self):
        with tempfile.TemporaryDirectory() as td:
            ids = ['F0471', 'F0475']
            ans = {i: R.rule_values(i) for i in ids}
            ans['F0475']['연결필드'] = '1.2 ER!5.1a'
            gold = Path(td) / 'gold'
            gold.mkdir()
            fill_form(gold / 'g.xlsx', ans)
            for flag, expect_ids, n_link in (([], [], 2), (['--card-examples'], ['F0471', 'F0474', 'F0475', 'F0476', 'F0477', 'F0536'], 0)):
                try:
                    self.assertEqual(CI.main(['--out', td, '--set', ','.join(ids), '--variant', 'rule', '--mock', 'rule',
                                              '--label', 'x' + str(len(flag)), '--golden', str(gold)] + flag), 0)
                finally:
                    C.set_card_examples(False)
                run = next(p for p in Path(td).iterdir() if p.name.endswith('_x' + str(len(flag))))
                cfg = json.loads((run / 'config.json').read_text(encoding='utf-8'))
                self.assertEqual(sorted(cfg['card_example_ids']), sorted(expect_ids))
                acc = json.loads((run / 'accuracy.json').read_text(encoding='utf-8'))['results']
                self.assertEqual(acc['rule']['연결필드']['n'], n_link)       # 켠 실행에서는 그 칸이 채점에서 빠짐, 끈 실행(기본)은 그대로
                rep = (run / 'accuracy_report.md').read_text(encoding='utf-8')
                self.assertEqual('카드 예시로 쓴 칸' in rep, bool(flag))

    def test_basis_base_and_extra_citations(self):
        base = 'Annex16 Vol.IV Part II §2.4.1.6 / Annex16 Vol.IV App.1 / ETM 부록1 1.4 OoM 19'
        gold = {'F0475': {'gold': {a: '' for a in C.ATTRS}}, 'F0471': {'gold': {a: '' for a in C.ATTRS}}}
        gold['F0475']['gold']['근거'] = base + ' / ETM §3.3.4.4 표 3-11'                # 기본 + 추가 인용
        gold['F0471']['gold']['근거'] = 'Annex16 Vol.IV Part II §2.4.1.6 / Annex16 Vol.IV App.1 / ETM 부록1 1.4 OoM 1'
        preds = {'F0475': {'근거': base}, 'F0471': {'근거': gold['F0471']['gold']['근거']}}      # 규칙처럼 기본 인용만
        res, _ = G.score(preds, gold)
        r = res['근거']
        self.assertEqual((r['n'], r['correct']), (2, 1))              # 정확 일치: F0471만
        self.assertEqual(r['base_ok'], 2)                              # 기본 인용은 둘 다 맞음
        self.assertEqual((r['extra_items'], r['extra_hit_items'], r['extra_gold'], r['extra_found']), (1, 0, 1, 0))
        preds['F0475']['근거'] = base + ' / ETM §3.3.4.4 표 3-11'
        res, _ = G.score(preds, gold)
        self.assertEqual((res['근거']['correct'], res['근거']['extra_hit_items'], res['근거']['extra_found']), (2, 1, 1))

    def test_lock_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            ids = G.form_ids(FORM, '1차')[:1]
            v = {a: x for a, x in zip(C.ATTRS, ['이행의무자', '계획', '강행', '매년 4/30', '텍스트', '법 제6조제3항', '-'])}
            fill_form(Path(td) / 'a.xlsx', {ids[0]: v})
            (Path(td) / '~$a.xlsx').write_bytes(b'lock')                  # 엑셀이 열려 있을 때 생기는 임시 파일
            g, conf = G.read_golden([td])
            self.assertEqual(sorted(g), ids)

    def test_row_label_makes_er_summary_inputs_numeric(self):
        for i in ('F0252', 'F0275'):                                         # ER 5.1 K16:K20, 5.2 M16:M20 (입력 항목)
            self.assertEqual(R.rule_values(i)['입력유형'], '수치', i)
        for i in ('F0256', 'F0278'):                                         # 같은 줄 제목 항목은 제목 줄이라 제외
            self.assertTrue(R.is_excluded(i))
            self.assertEqual(R._input_type(C.context()['by_id'][i])[0], '수치')
        for i in ('F0405', 'F0431', 'F0470'):                                # 다른 항목의 줄이 섞이는 범위는 영향 없음
            self.assertNotEqual(R._input_type(C.context()['by_id'][i])[2], '같은 줄 행 머리글의 단위·수량 표현')

    def test_combined_domestic_number_bug_is_fixed(self):
        comb = [i for i, it in C.context()['by_id'].items() if C.split_domestic_no(it.get('국내 항목번호'))[0]]
        self.assertEqual(len(comb), 14)                                      # CEF 청구서 「3 a)」 형식 14개
        for i in comb:
            self.assertTrue(R.rule_predict(i)['근거']['fixed'], i)
        self.assertEqual(R.rule_values('F0402')['근거'], '지침 별지 제3호서식 3.a) / 지침 별표 5 3.a) / 지침 제7조제1항 / 시행규칙 제7조')
        self.assertEqual(C.split_domestic_no('3 a)'), ('3', 'a)'))
        self.assertEqual(C.split_domestic_no('b4)'), (None, 'b4)'))

    def test_refresh_marks_newly_excluded_headings_and_clears_ai_fields(self):
        from unittest import mock
        rules = C.template_rules()
        r_none = dict(rules, 소제목_줄={'정책': 'none', 'ids': []})
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(C, 'template_rules', lambda: r_none):      # 예전 규칙(제목 줄 제외 없음)으로 실행한 결과
                self.assertEqual(CI.main(['--out', td, '--set', 'F0256,F0003', '--variant', 'hybrid', '--mock', 'rule', '--label', 'h']), 0)
            run = next(Path(td).iterdir())
            before = {r['id']: r for r in map(json.loads, (run / 'predictions_hybrid.jsonl').read_text(encoding='utf-8').splitlines())}
            self.assertTrue(before['F0256']['values']['입력유형'])
            rep = CI.refresh_rules(run)                                         # 실제 규칙(176개 제외)을 적용
            after = {r['id']: r for r in map(json.loads, (run / 'predictions_hybrid.jsonl').read_text(encoding='utf-8').splitlines())}
            self.assertTrue(all(v == '' for v in after['F0256']['values'].values()))
            self.assertIn('_줄', after['F0256']['flags'])
            self.assertEqual(after['F0256']['confidence'], '')
            self.assertEqual(after['F0003']['values'], before['F0003']['values'])
            self.assertIn('소제목 줄 1개', (run / 'summary.md').read_text(encoding='utf-8'))

    def test_export_marks_excluded_headings(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(CI.main(['--out', td, '--set', 'F0256,F0003', '--variant', 'rule', '--mock', 'rule', '--label', 'x', '--export-xlsx', 'rule']), 0)
            run = next(Path(td).iterdir())
            ws = openpyxl.load_workbook(run / 'field_master_rule.xlsx')['1_field_master']
            head = {c.value: c.column for c in ws[1] if c.value}
            rows = {r[0].value: r for r in ws.iter_rows(min_row=2)}
            self.assertEqual(rows['F0256'][head['분류 변형·확신'] - 1].value, '제외(소제목 줄)')
            self.assertFalse(rows['F0256'][head['주체'] - 1].value)
            self.assertNotEqual(rows['F0003'][head['분류 변형·확신'] - 1].value, '제외(소제목 줄)')

    def test_dry_run_skips_headings_in_call_count(self):
        n_ai = len(C.context()['items']) - 175                                # 제목 줄 176개 중 견본 F0571은 제외하지 않음
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(CI.main(['--set', 'all', '--variant', 'hybrid', '--dry-run']), 0)
        out = buf.getvalue()
        self.assertIn('소제목 줄 175개는 AI 제외', out)
        self.assertIn(f'호출 {-(-n_ai // 10)}회', out)

    def test_report_warns_when_gold_has_values_for_a_heading_row(self):
        with tempfile.TemporaryDirectory() as td:
            gold = Path(td) / 'gold'
            gold.mkdir()
            ids = G.form_ids(FORM, '1차')[:2]
            v = R.rule_values('F0003')
            fill_form(gold / 'g.xlsx', {ids[0]: v, 'F0495': v})                    # F0495는 제목 줄인데 정답이 적혀 있는 잘못된 경우
            self.assertEqual(CI.main(['--out', td, '--set', ','.join(ids), '--variant', 'rule', '--mock', 'rule', '--label', 'w', '--golden', str(gold)]), 0)
            run = next(p for p in Path(td).iterdir() if p.name.endswith('_w'))
            self.assertIn('소제목 줄로 제외된 항목', (run / 'accuracy_report.md').read_text(encoding='utf-8'))

    def test_ai_input_type_that_disagrees_with_cell_size_is_flagged(self):
        small = CI.build_record('F0084', 'hybrid', {'값': {'입력유형': '서술', '의무성': '강행', '연결필드': '-'}, '확신': '보통', '이유': 'x'})
        self.assertTrue(any('칸이 작음' in x for x in small['flags'].get('입력유형', [])))
        big = CI.build_record('F0126', 'hybrid', {'값': {'입력유형': '서술', '의무성': '강행', '연결필드': '-'}, '확신': '보통', '이유': 'x'})
        self.assertFalse(big['flags'].get('입력유형'))
        txt = CI.build_record('F0126', 'hybrid', {'값': {'입력유형': '텍스트', '의무성': '강행', '연결필드': '-'}, '확신': '보통', '이유': 'x'})
        self.assertTrue(any('칸이 큼' in x for x in txt['flags'].get('입력유형', [])))

    def test_date_setting_confirmed(self):
        self.assertEqual(C.template_rules()['입력유형_설정']['날짜 서식 칸'], '텍스트')


class TestPrompt(unittest.TestCase):
    def test_hybrid_prompt_has_code_values_and_card(self):
        s, u = P.build_prompt(['F0003', 'F0548'], 'hybrid')
        self.assertIn('코드가 정한 값(확정', u)
        self.assertIn('## 템플릿별 상수표', u)
        self.assertIn('OoM', u)
        self.assertIn('채울 속성', u)

    def test_reason_comes_before_value_in_answer_format(self):
        for variant in ('ai', 'hybrid'):
            s, u = P.build_prompt(['F0003'], variant)
            spec = u.split('## 답 형식')[1]
            self.assertLess(spec.index('"이유"'), spec.index('"값"'))
            self.assertIn('이유가 강행이면 값도 강행', spec)

    def test_ai_prompt_has_no_code_values(self):
        s, u = P.build_prompt(['F0003'], 'ai')
        self.assertNotIn('코드가 정한 값', u)
        self.assertNotIn('코드의 잠정 판단', u)

    def test_only_the_five_examples_show_answers(self):
        s, u = P.build_prompt(['F0003', 'F0021'], 'ai')
        self.assertEqual(u.count('  정답: '), 5)
        for iid in C.EXAMPLE_IDS:
            self.assertIn(f'[{iid}]', u)
        body = u.split('## 분류할 항목')[1]
        self.assertNotIn('정답:', body)

    def test_extract_items_json(self):
        obj, err = P.extract_items_json('설명\n```json\n{"items":[{"id":"F0003","값":{"주체":"이행의무자"}}]}\n```')
        self.assertIsNone(err)
        self.assertEqual(obj['items'][0]['id'], 'F0003')
        self.assertIsNone(P.extract_items_json('답 없음')[0])


class TestValidate(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(V.check_value('F0003', '주체', '이행의무자'), [])
        self.assertTrue(V.check_value('F0003', '주체', '회사'))
        self.assertTrue(V.check_value('F0003', '의무성', '모름'))
        self.assertEqual(V.check_value('F0003', '시점·기한', '매년 4/30'), [])
        self.assertTrue(V.check_value('F0003', '시점·기한', '내년 5월'))
        self.assertEqual(V.check_value('F0003', '근거', '지침 별지 제1호서식 1.a) / 지침 별표 1 1.a) / 지침 제3조제1항 / 법 제6조제3항'), [])
        self.assertTrue(V.check_value('F0003', '근거', '시행령 기본계획'))
        self.assertTrue(V.check_value('F0469', '연결필드', '1.2 ER!99z'))       # 후보 목록 밖
        self.assertTrue(V.check_value('F0589', '근거', 'Annex16 Vol.IV App.6 §3.11.1 / ETM 부록1 1.6 VR -'))   # 항목번호 자리의 「-」
        self.assertEqual(V.check_value('F0664', '근거', 'Annex16 Vol.IV App.6 §3.11.1 / ETM 부록1 1.6 VR s2)'), [])
        self.assertEqual(V.check_value('F0003', '주체', C.NO_RULE), [])

    def test_normalization(self):
        self.assertEqual(V.norm_value('시점·기한', '4월 30일'), '4/30')
        self.assertEqual(V.norm_value('연결필드', ''), '-')
        self.assertEqual(V.norm_value('근거', ' 법  제6조제3항 / 지침 제3조제1항 '), '법 제6조제3항 / 지침 제3조제1항')


class TestGolden(unittest.TestCase):
    def test_form_has_50_items_and_is_empty(self):
        self.assertEqual(len(G.form_ids(FORM)), 50)
        self.assertEqual(len(G.form_ids(FORM, '1차')), 30)
        g, conf = G.read_golden([FORM])
        self.assertEqual(len(g), 0)

    def test_score_and_norule_separate(self):
        ids = G.form_ids(FORM, '1차')[:3]
        gold = {ids[0]: {'gold': {'주체': '이행의무자', '단계': '계획', '의무성': '강행', '시점·기한': '매년 4/30', '입력유형': '텍스트', '근거': '법 제6조제3항 / 지침 제3조제1항', '연결필드': '-'}},
                ids[1]: {'gold': {'주체': '기준 없음: 판정 기준 없음', '단계': '', '의무성': '조건부', '시점·기한': '', '입력유형': '', '근거': '', '연결필드': '1.2 ER!5.1a'}}}
        preds = {ids[0]: {'주체': '이행의무자', '단계': '보고', '의무성': '강행', '시점·기한': '매년 4월 30일', '입력유형': '텍스트', '근거': '지침 제3조제1항 / 법 제6조제3항', '연결필드': ''},
                 ids[1]: {'주체': C.NO_RULE, '의무성': '강행', '연결필드': '-'}}
        res, wrong = G.score(preds, gold)
        self.assertEqual((res['주체']['n'], res['주체']['norule_n'], res['주체']['norule_agree']), (1, 1, 1))   # 기준 없음은 따로
        self.assertEqual(res['단계']['correct'], 0)
        self.assertEqual(res['시점·기한']['correct'], 1)                                                      # 「매년 4월 30일」 = 「매년 4/30」(날짜 표기만 통일)
        self.assertEqual(res['근거']['correct'], 1)                                                          # 순서 무관
        self.assertEqual(res['연결필드']['n'], 2)
        self.assertEqual(res['연결필드']['correct'], 1)
        self.assertEqual(res['의무성']['correct'], 1)
        self.assertEqual(len(wrong), 3)                                                                      # 단계, 연결필드, 의무성

    def test_read_filled_form_and_exclude(self):
        with tempfile.TemporaryDirectory() as td:
            ids = G.form_ids(FORM, '1차')[:2]
            v = {a: x for a, x in zip(C.ATTRS, ['이행의무자', '계획', '강행', '매년 4/30', '텍스트', '법 제6조제3항', '-'])}
            fill_form(Path(td) / 'a.xlsx', {ids[0]: v, ids[1]: v})
            g, conf = G.read_golden([td])
            self.assertEqual(sorted(g), sorted(ids))
            res, _ = G.score({i: v for i in ids}, g, exclude=[ids[1]])
            self.assertEqual(res['주체']['n'], 1)


class TestRunner(unittest.TestCase):
    def _run(self, td, args):
        return CI.main(['--out', td] + args)

    def test_mock_hybrid_fixed_values_come_from_code(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run(td, ['--set', 'examples', '--variant', 'rule,ai,hybrid', '--mock', 'rule', '--label', 't']), 0)
            run = next(Path(td).iterdir())
            preds = RP.load_predictions(run)
            self.assertEqual(set(preds), {'rule', 'ai', 'hybrid'})
            h = preds['hybrid']['F0469']
            self.assertEqual(h['source']['근거'], '코드(확정)')
            self.assertEqual(h['values']['근거'], C.gold_of_examples()['F0469']['근거'])
            self.assertEqual(h['source']['의무성'], 'AI')
            self.assertEqual(preds['ai']['F0469']['source']['주체'], 'AI')
            self.assertTrue((run / 'summary.md').exists())

    def test_mock_noisy_flags_bad_code_and_retries_missing(self):
        with tempfile.TemporaryDirectory() as td:
            ids = 'F0003,F0009,F0016,F0021,F0028,F0041'
            self.assertEqual(self._run(td, ['--set', ids, '--variant', 'hybrid', '--mock', 'noisy', '--batch', '6', '--label', 'n']), 0)
            run = next(Path(td).iterdir())
            recs = RP.load_predictions(run)['hybrid']
            self.assertEqual(set(recs), set(ids.split(',')))                          # 빠진 항목은 다시 물어 모두 채워짐
            flagged = [r for r in recs.values() if r['flags']]
            self.assertTrue(flagged)                                                 # 코드값 오류가 표시됨
            calls = [json.loads(x) for x in (run / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(c.get('attempt') == 2 for c in calls))

    def test_dry_run_and_budget_stop(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run(td, ['--set', 'golden1', '--variant', 'hybrid', '--dry-run']), 0)
            self.assertEqual(list(Path(td).iterdir()), [])
            self.assertEqual(self._run(td, ['--set', 'all', '--variant', 'hybrid', '--max-usd', '0.5']), 2)

    def test_score_run_makes_report_and_error_sheet(self):
        with tempfile.TemporaryDirectory() as td:
            ids = G.form_ids(FORM, '1차')[:6]
            ctx = C.context()
            ans = {}
            for i, iid in enumerate(ids):                     # 정답 = 규칙 예측에서 일부러 한 칸씩 바꿈
                v = R.rule_values(iid)
                if i % 2 == 0:
                    v['의무성'] = '조건부' if v['의무성'] == '강행' else '강행'
                ans[iid] = v
            gold = Path(td) / 'gold'
            gold.mkdir()
            fill_form(gold / 'g.xlsx', ans)
            self.assertEqual(self._run(td, ['--set', ','.join(ids), '--variant', 'rule,hybrid', '--mock', 'rule', '--label', 's', '--golden', str(gold)]), 0)
            run = next(p for p in Path(td).iterdir() if p.name.endswith('_s'))
            rep = (run / 'accuracy_report.md').read_text(encoding='utf-8')
            self.assertIn('속성별 정확도', rep)
            self.assertIn('규칙만', rep)
            self.assertIn('혼합', rep)
            acc = json.loads((run / 'accuracy.json').read_text(encoding='utf-8'))['results']
            self.assertEqual(acc['rule']['의무성']['correct'], 3)                      # 6건 중 3건을 일부러 틀리게 만듦
            self.assertEqual(acc['rule']['주체']['accuracy'], 1.0)
            wb = openpyxl.load_workbook(run / '오답원인분석표.xlsx')
            ws = wb['오답원인분석']
            self.assertEqual(ws.max_row - 1, 3)                                       # 틀린 칸 3개(같은 틀림은 변형을 묶음)
            self.assertEqual(len(ws.data_validations.dataValidation), 1)

    def test_export_filled_master_keeps_original_and_examples(self):
        before = _sha(C.FIELD_MASTER)
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run(td, ['--set', 'F0003,F0161,F0548', '--variant', 'hybrid', '--mock', 'rule', '--label', 'x', '--export-xlsx', 'hybrid']), 0)
            run = next(Path(td).iterdir())
            wb = openpyxl.load_workbook(run / 'field_master_hybrid.xlsx')
            ws = wb['1_field_master']
            head = {c.value: c.column for c in ws[1] if c.value}
            rows = {r[0].value: r for r in ws.iter_rows(min_row=2)}
            self.assertEqual(rows['F0161'][head['근거'] - 1].value, C.gold_of_examples()['F0161']['근거'])   # 견본은 확정 정답 유지
            self.assertTrue(rows['F0003'][head['주체'] - 1].value)
            self.assertEqual(rows['F0548'][head['주체'] - 1].value, '국토교통부')                  # OoM 주체(합의 안건 3)
        self.assertEqual(before, _sha(C.FIELD_MASTER))                                                    # 원본 카탈로그는 그대로

    def test_score_classifier_cli_rescoring_without_ai(self):
        import score_classifier as SC
        with tempfile.TemporaryDirectory() as td:
            ids = G.form_ids(FORM, '1차')[:4]
            gold = Path(td) / 'gold'
            gold.mkdir()
            fill_form(gold / 'g.xlsx', {i: R.rule_values(i) for i in ids})
            self.assertEqual(self._run(td, ['--set', ','.join(ids), '--variant', 'rule', '--label', 'r']), 0)
            run = next(p for p in Path(td).iterdir() if p.name.endswith('_r'))
            self.assertFalse((run / 'accuracy_report.md').exists())
            self.assertEqual(SC.main(['--run', str(run), '--golden', str(gold)]), 0)
            self.assertTrue((run / 'accuracy_report.md').exists())
            self.assertEqual(SC.main(['--run', str(run), '--golden', str(FORM)]), 1)       # 비어 있는 양식은 채점하지 않음

    def test_refresh_rules_updates_only_code_values_without_ai(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run(td, ['--set', 'F0003,F0548', '--variant', 'rule,hybrid', '--mock', 'rule', '--label', 'x']), 0)
            run = next(Path(td).iterdir())
            before = {r['id']: r for r in map(json.loads, (run / 'predictions_hybrid.jsonl').read_text(encoding='utf-8').splitlines())}
            self.assertEqual(before['F0548']['values']['주체'], '국토교통부')
            rules = C.template_rules()
            rules['1.4 OoM'] = dict(rules['1.4 OoM'], 주체='검증기관', 단계='검증', **{'시점·기한': '매년 4/30'})
            with mock.patch.object(C, 'template_rules', lambda: rules):
                rep = CI.refresh_rules(run)
            after = {r['id']: r for r in map(json.loads, (run / 'predictions_hybrid.jsonl').read_text(encoding='utf-8').splitlines())}
            self.assertEqual(after['F0548']['values']['주체'], '검증기관')                 # OoM 코드 몫이 갱신됨
            self.assertEqual(after['F0548']['values']['단계'], '검증')
            self.assertEqual(after['F0548']['values']['의무성'], before['F0548']['values']['의무성'])   # AI가 채운 값은 그대로
            self.assertEqual(after['F0548']['source']['의무성'], 'AI')
            self.assertEqual(after['F0003'], before['F0003'])                                          # OoM이 아닌 항목은 그대로
            self.assertTrue((run / 'predictions_hybrid.jsonl.before_refresh').exists())
            self.assertEqual(sum(rep['hybrid'][0].values()), 3)
            cfg = json.loads((run / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(len(cfg['refreshed']), 1)
            self.assertEqual(self._run(td, ['--refresh-rules', str(run)]), 0)

    def test_special_rows_are_flagged(self):
        ctx = C.context()
        ti = next(i for i, it in ctx['by_id'].items() if it['시트'] == 'Template Information')
        toc = next(i for i, it in ctx['by_id'].items() if str(it.get('매핑') or '').startswith('회의 결정 9'))
        for iid in (ti, toc):
            self.assertIn('_줄', CI.build_record(iid, 'rule', None)['flags'])
            self.assertIn('_줄', CI.build_record(iid, 'hybrid', None)['flags'])
        self.assertNotIn('_줄', CI.build_record('F0003', 'rule', None)['flags'])

    def test_sample_set_is_outside_golden_and_reproducible(self):
        a = CI.pick_ids('sample:30', 1)
        b = CI.pick_ids('sample:30', 1)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 30)
        self.assertFalse(set(a) & (set(G.form_ids(FORM)) | set(C.EXAMPLE_IDS)))
        self.assertNotEqual(a, CI.pick_ids('sample:30', 2))


if __name__ == '__main__':
    unittest.main(verbosity=1)
