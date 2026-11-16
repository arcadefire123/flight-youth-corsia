"""7단계 RAG 단위시험 (AI 호출 없음). engine 폴더에서:  python test_rag.py"""
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings('ignore')

import rag_eval as RE  # noqa: E402
import rag_ingest as RI  # noqa: E402
from corsia.rag import answer as A  # noqa: E402
from corsia.rag import corpus as K  # noqa: E402
from corsia.rag import qa as Q  # noqa: E402
from corsia.rag import report as RP  # noqa: E402
from corsia.rag.search import Index  # noqa: E402

TEMPLATE = K.RAG_DIR / 'qa_testset_template_v1.xlsx'
CHUNKS = K.load()
INDEX = Index(CHUNKS)

LAW = ' 제1조(목적)\n 제2조(정의)\n\n제1조(목적) 이 법은 국제항공 탄소 배출량의 관리에 관한 사항을 정함을 목적으로 한다. 가나다라마바사.\n' \
      '제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다. 가나다라마바사아자차카타파하.\n' \
      '제3조(긴 조문) ① ' + '가나다라 ' * 120 + ' ② ' + '마바사아 ' * 120 + '\n'
GUIDE = '제1조(목적) 이 지침은 법에서 위임한 사항을 정함을 목적으로 한다. 가나다라마바사아자차카타.\n' \
        '\f■ 국제항공 탄소 배출량 관리에 관한 지침 [별표 1]\n\n         모니터링 계획서 작성 방법(제3조제2항 관련)\n' \
        '1. 일반 정보\n a) 법인명 : 법인명을 작성한다.\n   a1) 주소\n   a2) 연락처 : 연락처를 작성한다.\n b) 담당자 : 담당자를 작성한다.\n' \
        '2. 자료관리\n a) 절차 : 절차를 작성한다.\n b4) 사유 : 사유를 작성한다.\n' \
        '\f■ 국제항공 탄소 배출량 관리에 관한 지침 [별표 2]\n\n         산식(제9조 관련)\n' + '배출량은 연료량에 계수를 곱해 구한다. 계산식은 다음과 같다.\n' * 3


def fill_qa(dst, rows):
    """양식 사본에 질문을 채운다. rows: [dict(질문 유형, 질문, 정답, 핵심 값, 근거 조항, 문서에 답이 있나, 용도)]"""
    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb['1_질문정답']
    head = {c.value: c.column for c in ws[1] if c.value}
    for i, r in enumerate(rows):
        for k, v in r.items():
            ws.cell(row=2 + i, column=head[k], value=v)
    wb.save(dst)


QA_ROWS = [
    {'질문 유형': '기한', '질문': '배출량보고서와 검증보고서는 매년 언제까지 내야 하나요?', '정답': '매년 4월 30일까지', '핵심 값': '4월 30일',
     '근거 조항': '법 제8조제4항 / 시행규칙 제7조', '문서에 답이 있나': '있음', '용도': '채점용'},
    {'질문 유형': '기한', '질문': '이행의무자로 지정되면 모니터링 계획 승인은 언제까지 신청하나요?', '정답': '지정·고시된 날부터 3개월 이내', '핵심 값': '3개월 이내',
     '근거 조항': '법 제6조제3항', '문서에 답이 있나': '있음', '용도': '개발용'},
    {'질문 유형': '문서에 없음', '질문': 'CORSIA 적격연료의 국제 시장 가격은 얼마인가요?', '정답': '제공된 법령·고시에는 없음', '문서에 답이 있나': '없음', '용도': '채점용'},
    {'질문 유형': '별표 작성방법', '질문': '배출량보고서에 항공기 등록은 어떻게 써야 하나요?', '정답': '최대이륙중량 5,700kg 이상 항공기 목록', '핵심 값': '5,700kg',
     '근거 조항': '지침 별표 4 3.a)', '문서에 답이 있나': '있음', '용도': '채점용'},
]


class TestCorpus(unittest.TestCase):
    def test_shipped_corpus_has_key_chunks(self):
        ids = {c['id'] for c in CHUNKS}
        for i in ('법 제6조제3항', '시행규칙 제7조', '시행규칙 제9조', '지침 별표 1 5.b4)', '지침 별표 4 3.a)', '지침 별표 7 2.a)', '지침 별표 5 3.b1)'):
            self.assertIn(i, ids, i)
        by = {c['id']: c for c in CHUNKS}
        self.assertIn('4월 30일', by['시행규칙 제7조']['text'])
        self.assertIn('3개월 이내', by['법 제6조제3항']['text'])
        self.assertTrue(all(len(c['text']) >= 8 for c in CHUNKS))
        self.assertGreater(len(CHUNKS), 350)

    def test_only_annex_ids_repeat(self):
        dup = [c['id'] for c in CHUNKS if '#' in c['id']]
        self.assertTrue(all('별표' in i for i in dup))
        self.assertEqual(len({c['id'] for c in CHUNKS}), len(CHUNKS))        # 꼬리를 붙여 모두 서로 다름

    def test_size_matches_plan(self):
        tok = K.tokens_est(K.full_context(CHUNKS))
        self.assertTrue(35_000 < tok < 55_000, tok)                          # 통째 넣기 약 4.3만 토큰

    def test_unit_and_cite_parsing(self):
        self.assertEqual(K.unit_of('법 제8조제4항'), ('법', '8'))
        self.assertEqual(K.unit_of('시행규칙 제9조제2항'), ('시행규칙', '9'))
        self.assertEqual(K.unit_of('지침 별표 4 3.a)'), ('지침', '별표4'))
        self.assertEqual(K.unit_of('지침 제5조제1항'), ('지침', '5'))
        self.assertIsNone(K.unit_of('시행령 기본계획'))
        self.assertEqual(K.split_cites('법 제8조제4항 / 시행규칙 제7조'), ['법 제8조제4항', '시행규칙 제7조'])
        self.assertEqual(K.split_cites('-'), [])

    def test_chunker_on_synthetic_text(self):
        ch = K.build({'법': LAW, '시행령': LAW, '시행규칙': LAW, '지침': GUIDE})
        ids = {c['id'] for c in ch}
        self.assertTrue({'법 제1조', '법 제2조', '법 제3조제1항', '법 제3조제2항'} <= ids)      # 목차 줄은 버리고, 긴 조는 항으로 나눔
        self.assertNotIn('법 제3조', ids)
        self.assertTrue({'지침 별표 1 1.', '지침 별표 1 1.a)', '지침 별표 1 1.a1)', '지침 별표 1 1.b)', '지침 별표 1 2.b4)', '지침 별표 2 (1)'} <= ids)
        t = next(c['text'] for c in ch if c['id'] == '지침 별표 1 1.a2)')
        self.assertIn('연락처를 작성한다', t)
        self.assertIn('[별표 1] 1. 일반 정보', t)                                              # 절 제목이 붙어 맥락이 남음

    def test_ingest_from_text_dir(self):
        with tempfile.TemporaryDirectory() as td:
            for doc, txt in (('법', LAW), ('시행령', LAW), ('시행규칙', LAW), ('지침', GUIDE)):
                (Path(td) / f'{doc}.txt').write_text(txt, encoding='utf-8')
            out = Path(td) / 'c.jsonl'
            self.assertEqual(RI.main(['--text-dir', td, '--out', str(out)]), 0)
            self.assertGreater(len(K.load(out)), 10)


class TestSearch(unittest.TestCase):
    def top_ids(self, q, k=3):
        return [c['id'] for c, _s, _w in INDEX.search(q, k=k)]

    def test_facts_are_found_without_article_numbers(self):
        self.assertIn('시행규칙 제7조', self.top_ids('배출량보고서는 매년 언제까지 제출해야 하나요?'))
        self.assertIn('법 제6조제3항', self.top_ids('이행의무자로 지정되면 모니터링 계획 승인은 언제까지 신청하나요?'))

    def test_direct_mentions(self):
        self.assertEqual(self.top_ids('법 제6조제3항 내용이 뭔가요', 1), ['법 제6조제3항'])        # 항까지 말하면 그 항만 가산
        self.assertEqual(self.top_ids('시행규칙 제9조제2항', 1), ['시행규칙 제9조'])
        top = INDEX.search('별표 4에서 항공기 등록은 어떻게 쓰나요', k=3)
        self.assertEqual(top[0][0]['unit'], '별표 4')

    def test_scores_sorted_and_k(self):
        hits = INDEX.search('자료 보관 기간', k=8)
        self.assertEqual(len(hits), 8)
        self.assertEqual([h[1] for h in hits], sorted((h[1] for h in hits), reverse=True))


class TestAnswer(unittest.TestCase):
    def test_check_citations(self):
        ok, bad = A.check_citations(['법 제6조제3항', '법 제7조', '지침 별표 1 5.'], ['법 제6조', '지침 별표 1 5.b4)#2'])
        self.assertEqual(ok, ['법 제6조제3항', '지침 별표 1 5.'])        # 하위(제3항)·상위(5.) 표기도 자료 안의 것으로 봄, #꼬리 무시
        self.assertEqual(bad, ['법 제7조'])
        ok, bad = A.check_citations(['아무 조항'], None)                   # 자료 없는 변형은 점검하지 않음
        self.assertEqual((ok, bad), (['아무 조항'], []))

    def test_parse_result(self):
        r = A.parse_result('설명\n```json\n{"answerable": true, "answer": "매년 4월 30일", "citations": "시행규칙 제7조 / 법 제8조제4항"}\n```', ['시행규칙 제7조'])
        self.assertTrue(r['answerable'])
        self.assertEqual(r['citations'], ['시행규칙 제7조', '법 제8조제4항'])
        self.assertEqual(r['invalid'], ['법 제8조제4항'])
        self.assertIsNone(A.parse_result('답 없음', [])['answerable'])

    def test_prompts(self):
        s, u, hits = A.build_rag('질문입니다', INDEX, 5)
        self.assertEqual(u.count('\n[') , 5)
        self.assertIn('answerable', u)
        s, u, prefix = A.build_full('질문입니다', CHUNKS)
        self.assertNotIn('## 자료', u)
        self.assertEqual(prefix.count('\n['), len(CHUNKS))
        s, u = A.build_closed('질문입니다')
        self.assertNotIn('## 자료', u)

    def test_ask_passes_cache_only_for_full(self):
        seen = {}

        class R_:
            text = '{"answerable": false, "answer": "근거 없음", "citations": []}'
            cost = {'usd': 0.01, 'tokens': {'input': 1}}
            truncated = False

        def fake(user, **kw):
            seen.update(kw)
            return R_()
        for v in ('rag', 'closed', 'full'):
            seen.clear()
            out = A.ask('질문', v, INDEX, CHUNKS, fake)
            self.assertEqual('cache_prefix' in seen, v == 'full', v)
            self.assertEqual(out['answerable'], False)
            self.assertEqual(len(out['retrieved']), 5 if v == 'rag' else 0)


class TestQA(unittest.TestCase):
    def test_read_filled_form(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'q.xlsx'
            fill_qa(p, QA_ROWS)
            items = Q.read_qa([p])
            self.assertEqual(len(items), 4)
            self.assertEqual(items[0]['key_values'], ['4월 30일'])
            self.assertEqual(items[0]['gold_cites'], ['법 제8조제4항', '시행규칙 제7조'])
            self.assertFalse(items[2]['answerable'])
            self.assertEqual(items[1]['purpose'], '개발용')
        self.assertEqual(Q.read_qa([TEMPLATE]), [])                            # 빈 양식

    def _q(self, **kw):
        base = {'answerable': True, 'key_values': ['4월 30일'], 'gold_cites': ['법 제8조제4항', '시행규칙 제7조']}
        base.update(kw)
        return base

    def _res(self, **kw):
        base = {'answerable': True, 'answer': '매년 4/30까지', 'citations': ['시행규칙 제7조', '법 제8조제4항'], 'invalid': []}
        base.update(kw)
        return base

    def test_judge(self):
        j = Q.judge(self._q(), self._res())
        self.assertTrue(j['answer_ok'] and j['cite_hit'] and j['cite_exact'] and j['correct'])            # 4월 30일 = 4/30
        self.assertFalse(Q.judge(self._q(), self._res(citations=['법 제8조제4항']))['correct'] and False)
        self.assertTrue(Q.judge(self._q(), self._res(citations=['법 제8조제4항']))['cite_hit'] is False)  # 시행규칙 제7조가 빠짐
        self.assertFalse(Q.judge(self._q(), self._res(answer='5월 31일'))['correct'])
        self.assertFalse(Q.judge(self._q(), self._res(invalid=['법 제99조']))['correct'])                 # 가짜 인용이 있으면 종합 오답
        self.assertTrue(Q.judge(self._q(), self._res(invalid=['법 제99조']))['fake_cite'])
        self.assertFalse(Q.judge(self._q(), self._res(answerable=None))['correct'])

    def test_judge_unanswerable_question(self):
        q = {'answerable': False, 'key_values': [], 'gold_cites': []}
        self.assertTrue(Q.judge(q, self._res(answerable=False, answer='근거 없음', citations=[]))['correct'])
        self.assertFalse(Q.judge(q, self._res(answerable=True, answer='가격은 100원입니다'))['correct'])

    def test_retrieval_recall(self):
        items = [{'no': 1, 'question': '배출량보고서는 매년 언제까지 제출해야 하나요?', 'gold_cites': ['시행규칙 제7조'], 'answerable': True},
                 {'no': 2, 'question': '가격은?', 'gold_cites': [], 'answerable': False}]
        hit, n, miss = Q.retrieval_recall(items, INDEX)
        self.assertEqual(n, 1)                                                  # 문서에 없음 질문은 세지 않음
        self.assertEqual(hit[3], 1)

    def test_wilson(self):
        lo, hi = RP.wilson(5, 10)
        self.assertTrue(0.23 < lo < 0.25 and 0.75 < hi < 0.77)
        self.assertIsNone(RP.wilson(0, 0))


class TestEval(unittest.TestCase):
    def _form(self, td):
        p = Path(td) / 'q.xlsx'
        fill_qa(p, QA_ROWS)
        return str(p)

    def test_retrieval_only_and_dry_run_leave_no_run_folder(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._form(td)
            out = Path(td) / 'runs'
            self.assertEqual(RE.main(['--qa', f, '--retrieval-only', '--out', str(out)]), 0)
            self.assertEqual(RE.main(['--qa', f, '--dry-run', '--out', str(out)]), 0)
            self.assertFalse(out.exists())
            self.assertEqual(RE.main(['--qa', f, '--max-usd', '0.0001', '--out', str(out)]), 2)      # 상한 초과로 멈춤
            self.assertEqual(RE.main(['--qa', str(TEMPLATE), '--out', str(out)]), 1)                  # 빈 양식

    def test_mock_perfect_run(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._form(td)
            self.assertEqual(RE.main(['--qa', f, '--variant', 'rag,full,closed', '--mock', 'perfect', '--out', td, '--label', 'm']), 0)
            run = next(p for p in Path(td).iterdir() if p.name.endswith('_m'))
            recs = RP.load_results(run)
            self.assertEqual(len(recs), 12)
            full = [r for r in recs if r['variant'] == 'full']
            self.assertTrue(all(r['j']['correct'] for r in full))                                    # 통째 넣기는 자료 전체가 있어 인용이 모두 유효
            closed = [r for r in recs if r['variant'] == 'closed']
            self.assertFalse(any(r['j']['fake_cite'] for r in closed))                               # 자료 없는 변형은 인용 점검 안 함
            md = (run / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('95% 구간', md)
            self.assertIn('검색만의 정확도', md)
            wb = openpyxl.load_workbook(run / 'qa_results.xlsx')
            ws = wb['문항별']
            self.assertEqual(ws.max_row - 1, 12)
            self.assertEqual(len(ws.data_validations.dataValidation), 1)

    def test_purpose_filter_and_noisy_mock_flags_fake_citations(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._form(td)
            self.assertEqual(RE.main(['--qa', f, '--variant', 'full', '--purpose', '채점용', '--mock', 'noisy', '--out', td, '--label', 'n']), 0)
            run = next(p for p in Path(td).iterdir() if p.name.endswith('_n'))
            recs = RP.load_results(run)
            self.assertEqual(len(recs), 3)                                                           # 채점용 3개만(개발용 1개 제외)
            self.assertTrue(any(r['j']['fake_cite'] for r in recs))                                  # 「법 제99조」 같은 없는 인용이 잡힘
            self.assertFalse(all(r['j']['correct'] for r in recs))

    def test_estimate_full_is_costlier_than_rag(self):
        items = Q.read_qa([TEMPLATE])
        items = [{'question': '배출량보고서는 언제까지 내나요?'}] * 30
        class Args:
            model, k = 'sonnet', 5
        est, cached = RE.estimate(items, ['rag', 'full', 'closed'], INDEX, CHUNKS, Args)
        self.assertGreater(est['full'][2], est['rag'][2] * 5)
        self.assertLess(cached, est['full'][2] / 3)                                                  # 캐시가 먹으면 크게 줄어듦


if __name__ == '__main__':
    unittest.main(verbosity=1)
