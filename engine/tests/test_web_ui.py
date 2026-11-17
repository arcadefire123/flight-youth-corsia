"""
웹 화면 시험  (app.py · corsia/web_view.py)
    python tests/test_web_ui.py        또는   python -m unittest discover -s tests -v

AI는 부르지 않는다. 실제 호출 함수(corsia.ai.client.call)와 키 확인(key_is_set)을 모의로 바꿔서
web_qual 모듈의 진짜 흐름(예상 비용 → 버튼 → 항목별 진행 → 결과 → 기억)을 그대로 돌린다.
기억 폴더는 임시 폴더로 바꾼다 — 모의 응답이 팀장 PC의 engine/ai_cache에 섞이면 나중에 실제 호출이
모의 결과를 「기억」으로 돌려받게 되기 때문이다(시험 끝에 engine/ai_cache가 생기지 않았는지도 확인한다).

확인하는 것
  1. 키가 없으면: 안내 한 줄, 버튼 없음, 호출 없음
  2. 버튼을 눌러야만 호출한다(자동 실행 없음). 버튼에 예상 비용·상한이 보인다
  3. AI 결과가 나와도 상단 요약 상자와 위반 목록은 한 글자도 바뀌지 않는다
  4. 같은 서류는 다시 부르지 않는다(화면 기억·디스크 기억). 「기억에서」 배지
  5. 실패(네트워크 등)해도 화면이 멈추지 않고 안내와 「판정 못 받음」을 보여 준다
  6. 서류 없이 올리면 AI 버튼 대신 안내, openpyxl이 없으면 엑셀 버튼 대신 안내
  7. 카드 HTML: 이스케이프, source 필드(있을 때·없을 때), 가짜 인용 표시, 화면 CSS와 클래스 일치
  8. $ 처리: 이스케이프하지 않은 $가 한 글자에 둘 이상이면 실패(마크다운이 수식으로 읽어 글자가 사라진다),
     법인명에 $가 있어도 제목이 그대로 나온다
  9. AI 요약 줄: 숫자를 더하면 항목 수와 같다(서술 없음·해당 없음·판정 못 받음이 빠지지 않는다)
"""
import ast
import functools
import inspect
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from streamlit.testing.v1 import AppTest                    # noqa: E402

from corsia import web_view as V                             # noqa: E402
from corsia.ai import web_qual                               # noqa: E402

APP = str(ENGINE / 'app.py')
REAL_CACHE = ENGINE / 'ai_cache'
_ORIG_ITER = web_qual.iter_qualitative
_ORIG_EST = web_qual.estimate_qualitative

DIRTY, WEAK = 1, 2                                           # 샘플 선택 순서


def _html(at):
    return '\n'.join(m.value for m in at.markdown)


def _box(at):
    return next(m.value for m in at.markdown if 'class="sumbox"' in m.value)


def _list(at):
    return [m.value for m in at.markdown if 'class="vwrap"' in m.value] + [s.value for s in at.success]


def _ai_button(at):
    return next((b for b in at.button if 'AI 정성 검증' in b.label or '다시 실행' in b.label), None)


def _captions(at):
    return ' | '.join(c.value for c in at.caption)


def _redirect_cache(fn, cache):
    """fn의 cache_dir 인자가 없거나 진짜 기억 폴더(engine/ai_cache)이면 임시 폴더로 바꿔서 부른다.
    web_qual 안에서 위치 인자로 부르는 경우도 처리한다."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = sig.bind_partial(*args, **kwargs)
        cur = bound.arguments.get('cache_dir', web_qual.CACHE_DIR)
        bound.arguments['cache_dir'] = cache if cur == web_qual.CACHE_DIR else cur
        return fn(*bound.args, **bound.kwargs)
    return wrapper


class _Env:
    """키 있음/없음 + 모의 호출 + 임시 기억 폴더"""

    def __init__(self, key=True, kind='weak', cache=None):
        self.key, self.kind = key, kind
        self.cache = cache or tempfile.mkdtemp(prefix='corsia_test_cache_')
        self.fn = web_qual.mock_call(kind, source='api')   # 기억 동작을 시험하려면 「진짜처럼」(api)로 둔다. 기본값 mock은 기억을 쓰지 않는다
        self._patches = []

    def __enter__(self):
        ps = [mock.patch('corsia.ai.client.key_is_set', return_value=self.key),
              mock.patch('corsia.ai.client.call', self.fn),
              mock.patch.object(web_qual, 'iter_qualitative', _redirect_cache(_ORIG_ITER, self.cache)),
              mock.patch.object(web_qual, 'estimate_qualitative', _redirect_cache(_ORIG_EST, self.cache))]
        for p in ps:
            p.start()
        self._patches = ps
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()

    @property
    def calls(self):
        return self.fn.calls['n']

    def cleanup(self):
        shutil.rmtree(self.cache, ignore_errors=True)


def _app(sample_index=0):
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    if sample_index:
        at.selectbox[0].select_index(sample_index)
        at.run()
    return at


class TestWithoutKey(unittest.TestCase):
    def test_one_line_no_button_no_call(self):
        existed = REAL_CACHE.exists()
        with _Env(key=False) as env:
            at = _app(WEAK)
            self.assertEqual(len(at.exception), 0)
            self.assertIn(web_qual.NO_KEY_TEXT, _captions(at))
            self.assertIsNone(_ai_button(at))
            self.assertEqual(env.calls, 0)
            self.assertNotIn('class="qcard"', _html(at))
            self.assertIn('만족', _box(at))                      # 지금 화면 그대로 (판정·점수·상쇄의무량)
            self.assertIn('265,773', _box(at))
            labels = [b.proto.label for b in at.get('download_button')]
            self.assertEqual(labels, ['결과 내려받기 (JSON)', '결과 내려받기 (엑셀)'])
            env.cleanup()
        self.assertEqual(REAL_CACHE.exists(), existed)


class TestButtonFlow(unittest.TestCase):
    def test_no_auto_call_label_shows_cost_then_cards_on_click(self):
        with _Env() as env:
            at = _app(WEAK)
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(env.calls, 0)                        # 자동 실행 없음
            self.assertNotIn('class="qcard"', _html(at))
            btn = _ai_button(at)
            self.assertIsNotNone(btn)
            self.assertRegex(btn.label, r'예상 약 \\?\$\d\.\d\d, 최대 \\?\$0\.30')
            self.assertIn('AI 참고용 · 최종 판정 아님', _html(at))
            self.assertIn('버튼을 눌렀을 때만 호출', _captions(at))

            btn.click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(env.calls, 7)                        # Q-06은 데이터갭 5% 이하라 건너뜀
            h = _html(at)
            self.assertEqual(h.count('class="qcard"'), 8)
            order = re.findall(r'class="qcard".*?>(Q-0\d)</span>', h)
            self.assertEqual(order, [f'Q-0{i}' for i in range(1, 9)])          # Q-01~Q-08 순서
            self.assertEqual(h.count('보완 필요</span>'), 7)
            self.assertIn('해당 없음(조건 미발생)', h)
            env.cleanup()

    def test_summary_box_and_violations_unchanged_by_ai(self):
        """AI 결과가 나와도 상단 요약 상자·위반 목록은 한 글자도 바뀌지 않는다 (원칙 1)"""
        with _Env() as env:
            at = _app(DIRTY)
            before_box, before_list = _box(at), _list(at)
            self.assertIn('불만족', before_box)
            self.assertIn('25.8', before_box)
            _ai_button(at).click().run()
            self.assertGreater(_html(at).count('class="qcard"'), 0)
            self.assertEqual(_box(at), before_box)
            self.assertEqual(_list(at), before_list)
            env.cleanup()

    def test_same_document_not_called_again(self):
        with _Env() as env:
            at = _app(WEAK)
            _ai_button(at).click().run()
            n1 = env.calls
            self.assertEqual(n1, 7)
            at.run()                                              # 같은 서류로 화면만 다시 그림
            self.assertEqual(env.calls, n1)
            self.assertEqual(_html(at).count('class="qcard"'), 8)
            self.assertIn('같은 서류는 다시 부르지 않습니다', _captions(at))
            again = _ai_button(at)
            self.assertIsNotNone(again)
            self.assertIn('비용 0', again.label)
            again.click().run()                                   # 다시 실행 = 기억에서 가져오기 (호출·비용 0)
            self.assertEqual(env.calls, n1)
            h = _html(at)
            self.assertGreaterEqual(h.count('>기억에서<'), 7)
            self.assertIn('실제 비용 &#36;0.0000', h)
            env.cleanup()

    def test_new_session_uses_disk_memory(self):
        with _Env() as env:
            at = _app(WEAK)
            _ai_button(at).click().run()
            n1 = env.calls
            at2 = _app(WEAK)                                      # 새 화면(세션 기억 없음), 디스크 기억은 있음
            self.assertEqual(_html(at2).count('class="qcard"'), 0)
            btn = _ai_button(at2)
            self.assertIn('기억에서 7건 · 비용 0', btn.label)
            btn.click().run()
            self.assertEqual(env.calls, n1)
            self.assertGreaterEqual(_html(at2).count('>기억에서<'), 7)
            env.cleanup()

    def test_different_documents_do_not_share_session_result(self):
        with _Env() as env:
            at = _app(WEAK)
            _ai_button(at).click().run()
            at.selectbox[0].select_index(0)                       # 정상본(서술이 다르다)으로 바꿈
            at.run()
            self.assertEqual(_html(at).count('class="qcard"'), 0)
            self.assertIsNotNone(_ai_button(at))
            env.cleanup()


class TestFailures(unittest.TestCase):
    def test_network_error_does_not_crash(self):
        with _Env(kind='network') as env:
            at = _app(DIRTY)
            before_box = _box(at)
            _ai_button(at).click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(any('네트워크' in w.value for w in at.warning))
            self.assertIn('판정 못 받음', _html(at))
            self.assertEqual(_box(at), before_box)
            self.assertIsNotNone(_ai_button(at))                  # 실패한 결과는 기억하지 않으므로 다시 실행할 수 있다
            env.cleanup()

    def test_without_documents_shows_hint_not_button(self):
        """서류 JSON 없이 비행 기록만 올리면 AI 버튼 대신 안내"""
        wrapper = Path(tempfile.mkdtemp()) / 'wrap.py'
        wrapper.write_text(
            "import runpy, streamlit as st\n"
            "class _F:\n"
            "    def __init__(self, b): self._b = b\n"
            "    def getvalue(self): return self._b\n"
            "def _fake(label, *a, **k):\n"
            f"    return _F(open(r'{ENGINE / 'dataset' / 'flights_clean.csv'}', 'rb').read()) if label.startswith('비행 기록') else None\n"
            "st.file_uploader = _fake\n"
            f"runpy.run_path(r'{APP}', run_name='__main__')\n", encoding='utf-8')
        with _Env() as env:
            at = AppTest.from_file(str(wrapper), default_timeout=300)
            at.run()
            at.radio[0].set_value('파일 올리기')
            at.run()
            self.assertEqual(len(at.exception), 0)
            self.assertIn('서류 JSON을 올리면 AI 정성 검증을 실행할 수 있습니다', _captions(at))
            self.assertIsNone(_ai_button(at))
            self.assertEqual(env.calls, 0)
            env.cleanup()

    def test_missing_openpyxl_shows_notice(self):
        with _Env(key=False) as env, mock.patch('corsia.excel_export.HAVE_OPENPYXL', False):
            at = _app(0)
            self.assertEqual(len(at.exception), 0)
            self.assertIn('openpyxl이 필요합니다', _captions(at))
            self.assertEqual([b.proto.label for b in at.get('download_button')], ['결과 내려받기 (JSON)'])
            env.cleanup()


class TestCards(unittest.TestCase):
    """corsia/web_view.py — 화면 없이 HTML 조각만"""

    def _item(self, **kw):
        it = {'rule_id': 'Q-01', 'document': 'EMP', 'item': 'x', 'question': '질문', 'cited_clause': '지침 별표1 5a',
              'status': 'ok', 'message': '', 'verdict': '보완 필요', 'reason': '이유', 'missing_points': ['보완 하나'],
              'checks': [], 'flags': [], 'fake_citations': 0, 'cached': False}
        it.update(kw)
        return it

    def test_escapes_ai_text(self):
        evil = '<script>alert(1)</script>'
        it = self._item(reason=evil, question=evil, missing_points=[evil], cited_clause=evil,
                        checks=[{'id': 'C1', 'kind': '필수', 'text': evil, 'status': '부족', 'evidence': evil, 'note': evil,
                                 'flag': evil, 'citation_problem': True, 'fake_citation': False}])
        out = V.item_card_html(it)
        self.assertNotIn('<script>', out)
        self.assertIn('&lt;script&gt;', out)

    def test_source_field_present_and_absent(self):
        self.assertIsNone(V.item_source(self._item()))
        self.assertEqual(V.item_source(self._item(cached=True)), 'cache')            # source가 없으면 cached로
        self.assertEqual(V.item_source(self._item(source='cache')), 'cache')
        self.assertEqual(V.item_source(self._item(source='mock')), 'mock')
        self.assertEqual(V.item_source(self._item(source='api', cached=True)), 'api')  # source가 우선
        self.assertEqual(V.item_source(self._item(source='이상한값')), None)
        self.assertIn('>기억에서<', V.item_card_html(self._item(source='cache')))
        self.assertNotIn('기억에서', V.item_card_html(self._item(source='api')))
        self.assertIn('시험용 모의 응답', V.item_card_html(self._item(source='mock')))
        self.assertIn('>기억에서<', V.item_card_html(self._item(cached=True)))        # source 없는 옛 결과도 동작

    def test_badges_text_and_color(self):
        for verdict in ('충분', '보완 필요', '판단 불가'):
            out = V.item_card_html(self._item(verdict=verdict))
            self.assertIn(f'>{verdict}</span>', out)
            self.assertIn(V.VERDICT_STYLE[verdict][0], out)
        self.assertIn('해당 없음(조건 미발생)', V.item_card_html(self._item(status='skipped_condition', verdict=None, reason=None, message='갭 0%')))
        self.assertIn('서술 없음', V.item_card_html(self._item(status='no_text', verdict=None)))
        self.assertIn('판정 못 받음', V.item_card_html(self._item(status='network_error', verdict=None, message='연결 실패')))
        out = V.item_card_html(self._item(status='method_missing', verdict='판단 불가', reason=None, message='방법 값 없음'))
        self.assertIn('판단 불가', out)
        self.assertIn('방법 값 없음', out)

    def test_fake_citation_marked(self):
        chk = {'id': 'C1', 'kind': '필수', 'text': 't', 'status': '판단 불가', 'evidence': '없는 문장',
               'flag': '인용문이 서류 글에 없음', 'citation_problem': True, 'fake_citation': True}
        out = V.item_card_html(self._item(fake_citations=1, checks=[chk]))
        self.assertIn('인용 확인 필요', out)
        self.assertNotIn('인용 확인 필요', V.item_card_html(self._item()))

    def test_items_html_order_and_disclaimer(self):
        res = {'summary': {'verdicts': {'충분': 1, '보완 필요': 1, '판단 불가': 0}, 'n_failed': 0},
               'cost': {'n_calls': 2, 'n_cached': 0, 'actual_usd': 0.0123}, 'timing': {'seconds': 4.2},
               'disclaimer': 'AI 참고 의견입니다.',
               'items': [self._item(rule_id='Q-01'), self._item(rule_id='Q-02', verdict='충분')]}
        out = V.items_html(res)
        self.assertLess(out.index('Q-01'), out.index('Q-02'))
        self.assertIn('충분 1 · 보완 필요 1 · 판단 불가 0', out)
        self.assertIn('실제 비용 &#36;0.0123', out)
        self.assertIn('AI 참고 의견입니다.', out)

    def test_every_class_has_css_in_app(self):
        """카드 HTML이 쓰는 클래스가 app.py 스타일에 모두 정의되어 있다"""
        css = (ENGINE / 'app.py').read_text(encoding='utf-8')
        res = {'summary': {'verdicts': {}}, 'cost': {}, 'timing': {'seconds': 1}, 'disclaimer': 'd',
               'items': [self._item(source='mock', fake_citations=1,
                                    checks=[{'id': 'C1', 'kind': '필수', 'text': 't', 'status': '있음', 'evidence': 'e',
                                             'note': 'n', 'flag': 'f', 'citation_problem': True}],
                                    status='api_error', detail='d')]}
        out = V.items_html(res)
        classes = {c for m in re.findall(r'class="([^"]+)"', out) for c in m.split()}
        for cls in classes:
            if cls in ('ktable', 'kwrap', 'rid', 'qsrc'):          # 다른 곳에서 이미 쓰는 공용 클래스
                self.assertIn('.' + cls, css, cls)
            else:
                self.assertIn('.' + cls, css, f'app.py 스타일에 .{cls} 없음')


# ───────────────────────── $ 처리 ─────────────────────────
def _unescaped_dollar_strings(source, min_count=2):
    """파이썬 소스의 문자열(f-문자열은 {}를 가린 한 덩어리)에서 이스케이프하지 않은 $가 min_count개 이상인 것을 찾는다.
    Streamlit 마크다운은 $…$ 두 개를 수식으로 읽는다. 「예상 약 $0.10, 최대 $0.30」처럼 한 글자에 $가 둘이면 글자체가 바뀌고
    띄어쓰기가 사라진다. 이스케이프는 \\$ 로 쓴다(소스에서는 \\\\$). 독스트링은 화면에 나가지 않으므로 뺀다."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    bad = []
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.JoinedStr):
            text = ''.join(v.value if isinstance(v, ast.Constant) else '\x00' for v in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            continue
        if len(re.findall(r'(?<!\\)\$', text)) >= min_count:
            bad.append((node.lineno, text[:60]))
    return bad


def _item(rule_id='Q-01', **kw):
    it = {'rule_id': rule_id, 'document': 'EMP', 'item': 'x', 'question': '질문', 'cited_clause': '근거',
          'status': 'ok', 'message': '', 'verdict': '충분', 'reason': '이유', 'missing_points': [], 'checks': [],
          'flags': [], 'fake_citations': 0, 'cached': False}
    it.update(kw)
    return it


class TestDollarSigns(unittest.TestCase):
    def _all_texts(self, at):
        out = [b.label for b in at.button]
        for coll in (at.caption, at.markdown, at.warning, at.info, at.error, at.success):
            out += [e.value for e in coll]
        return out

    def _assert_no_math_pair(self, at, where):
        for t in self._all_texts(at):
            n = len(re.findall(r'(?<!\\)\$', t))
            self.assertLess(n, 2, f'{where}: 이스케이프하지 않은 $가 {n}개 — {t[:80]!r}')

    def test_source_has_no_string_with_two_unescaped_dollars(self):
        for rel in ('app.py', 'corsia/web_view.py'):
            bad = _unescaped_dollar_strings((ENGINE / rel).read_text(encoding='utf-8'))
            self.assertEqual(bad, [], f'{rel}: 이스케이프하지 않은 $가 둘 이상인 문자열이 있다(소스에서는 \\\\$로 쓴다)')

    def test_checker_catches_the_known_bug(self):
        """검사기 자체 시험: 팀장이 찾은 원래 버그(라벨에 $ 두 개)는 잡고, 고친 모양은 통과시킨다"""
        bug = 'label = f"AI 정성 검증 실행 (예상 약 ${a:.2f}, 최대 ${b:.2f})"'
        fixed = 'label = f"AI 정성 검증 실행 (예상 약 \\\\${a:.2f}, 최대 \\\\${b:.2f})"'
        self.assertTrue(_unescaped_dollar_strings(bug))
        self.assertEqual(_unescaped_dollar_strings(fixed), [])
        self.assertEqual(_unescaped_dollar_strings('x = "비용 $5"'), [])            # 하나뿐이면 수식이 되지 않는다
        self.assertTrue(_unescaped_dollar_strings('x = "비용 $5, 가격 $10"'))

    def test_card_html_never_contains_raw_dollar(self):
        evil = '비용은 $5에서 $10 사이, 가격 $20'
        it = _item(reason=evil, question=evil, missing_points=[evil], cited_clause=evil,
                   checks=[{'id': 'C1', 'kind': '필수', 'text': evil, 'status': '부족', 'evidence': evil, 'note': evil, 'flag': evil}])
        out = V.items_html({'items': [it], 'cost': {'actual_usd': 0.0123}, 'timing': {'seconds': 1}, 'disclaimer': evil})
        self.assertNotIn('$', out)                                   # 비용 줄의 $도 HTML 문자로
        self.assertIn('&#36;5', out)
        self.assertIn('실제 비용 &#36;0.0123', out)

    def test_runtime_texts_have_no_two_unescaped_dollars(self):
        with _Env() as env:
            for idx in (WEAK, DIRTY):
                at = _app(idx)
                self.assertIn('\\$', _ai_button(at).label)            # 라벨의 $는 \$ 로 이스케이프되어 있다
                self._assert_no_math_pair(at, f'샘플 {idx} 실행 전')
                _ai_button(at).click().run()
                self.assertEqual(len(at.exception), 0)
                self._assert_no_math_pair(at, f'샘플 {idx} 실행 후')
            env.cleanup()

    def test_company_name_with_dollars_shows_as_typed(self):
        """법인명에 $가 둘이면 제목이 수식으로 읽혀 글자가 사라졌다 — HTML 문자로 바꿔 그대로 보인다"""
        docs = json.loads((ENGINE / 'dataset' / 'documents.json').read_text(encoding='utf-8'))
        docs['documents']['EMP']['operator_name'] = '$A$ 항공 $B$'
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'docs.json').write_text(json.dumps(docs, ensure_ascii=False), encoding='utf-8')
        (tmp / 'wrap.py').write_text(
            "import runpy, streamlit as st\n"
            "class _F:\n"
            "    def __init__(self, b): self._b = b\n"
            "    def getvalue(self): return self._b\n"
            "def _fake(label, *a, **k):\n"
            f"    flights = r'{ENGINE / 'dataset' / 'flights_clean.csv'}'\n"
            f"    docs = r'{tmp / 'docs.json'}'\n"
            "    if label.startswith('비행 기록'):\n"
            "        return _F(open(flights, 'rb').read())\n"
            "    if label.startswith('서류 JSON'):\n"
            "        return _F(open(docs, 'rb').read())\n"
            "    return None\n"
            "st.file_uploader = _fake\n"
            f"runpy.run_path(r'{APP}', run_name='__main__')\n", encoding='utf-8')
        with _Env(key=False) as env:
            at = AppTest.from_file(str(tmp / 'wrap.py'), default_timeout=300)
            at.run()
            at.radio[0].set_value('파일 올리기')
            at.run()
            self.assertEqual(len(at.exception), 0)
            head = next(m.value for m in at.markdown if m.value.startswith('####'))
            self.assertIn('&#36;A&#36; 항공 &#36;B&#36;', head)
            self.assertNotIn('$', head)
            self._assert_no_math_pair(at, '업로드')
            env.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


class TestAiSummaryLine(unittest.TestCase):
    """AI 요약 줄: 항목을 빠짐없이 센다"""

    @staticmethod
    def _numbers(html_text):
        head = html_text.split('<div class="qsumsub">')[0]
        return [int(n) for n in re.findall(r'(?<![\w.])(\d+)(?![\w.])', re.sub(r'<[^>]+>', ' ', head))]

    def test_counts_add_up_to_item_count(self):
        items = [_item('Q-01', verdict='충분'), _item('Q-02', verdict='보완 필요'), _item('Q-03', verdict='판단 불가'),
                 _item('Q-04', status='method_missing', verdict='판단 불가'),
                 _item('Q-05', status='skipped_condition', verdict=None),
                 _item('Q-06', status='no_text', verdict=None), _item('Q-07', status='no_text', verdict=None),
                 _item('Q-08', status='network_error', verdict=None),
                 _item('Q-09', status='budget_exceeded', verdict=None),
                 _item('Q-10', status='api_error', verdict=None), _item('Q-11', status='parse_error', verdict=None),
                 _item('Q-12', status='ok', verdict=None)]                           # 어느 범주에도 안 맞는 것은 「기타」
        out = V.summary_html({'items': items})
        self.assertEqual(sum(self._numbers(out)), len(items))
        for want in ('서술 없음 2', '해당 없음 1', '판정 못 받음 4', '기타 1', '판단 불가 2'):
            self.assertIn(want, out)

    def test_no_text_shown_like_not_applicable(self):
        out = V.summary_html({'items': [_item('Q-01', verdict='충분'), _item('Q-06', status='no_text', verdict=None),
                                        _item('Q-05', status='skipped_condition', verdict=None)]})
        self.assertIn('충분 1 · 보완 필요 0 · 판단 불가 0 · 해당 없음 1 · 서술 없음 1', out)

    def test_zero_categories_hidden(self):
        out = V.summary_html({'items': [_item('Q-01', verdict='충분')]})
        for word in ('서술 없음', '해당 없음', '판정 못 받음', '기타'):
            self.assertNotIn(word, out)

    def test_dirty_sample_summary_counts_no_text(self):
        """오류주입본(Q-01·Q-06 서술이 비어 있음): 서술 없음 2가 요약 줄에 보이고 합이 8이다"""
        with _Env() as env:
            at = _app(DIRTY)
            _ai_button(at).click().run()
            h = next(m.value for m in at.markdown if 'class="qsum"' in m.value)
            seg = h[h.index('<div class="qsum">'):]
            self.assertIn('서술 없음 2', seg.split('<div class="qsumsub">')[0])
            self.assertEqual(sum(self._numbers(seg)), 8)
            env.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
