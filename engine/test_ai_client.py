"""
7단계 - Claude 호출부(client.py) 단위시험 (AI 호출 없음, 네트워크 없음, 비용 0)

    engine 폴더에서:  python test_ai_client.py

실제 서버 대신 가짜 응답기(MockTransport)를 끼워 요청 내용과 오류 처리를 확인한다.
anthropic 패키지가 없으면 시험을 건너뛴다.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import anthropic
    try:
        import httpx2 as httpx          # 새 anthropic 패키지가 쓰는 이름
    except ImportError:
        import httpx
except ImportError:                      # pragma: no cover
    anthropic = None

from corsia.ai import client as C           # noqa: E402
from corsia.ai.models import MODELS         # noqa: E402

FAKE_KEY = 'sk' + '-ant-' + 'test0000FAKE'   # 파일에 키 모양 문자열이 그대로 남지 않게 쪼개서 만든다


def ok_json(model='claude-sonnet-5-5', stop='end_turn', blocks=None):
    return {'id': 'msg_x', 'type': 'message', 'role': 'assistant', 'model': model,
            'content': blocks or [{'type': 'thinking', 'thinking': '', 'signature': 's'},
                                  {'type': 'text', 'text': '확인'}],
            'stop_reason': stop, 'stop_sequence': None,
            'usage': {'input_tokens': 31, 'output_tokens': 4, 'cache_creation_input_tokens': 0,
                      'cache_read_input_tokens': 0}}


@unittest.skipIf(anthropic is None, 'anthropic 패키지가 없음')
class TestClient(unittest.TestCase):
    def setUp(self):
        self.orig = anthropic.Anthropic
        self.old_key = os.environ.get('ANTHROPIC_API_KEY')
        os.environ['ANTHROPIC_API_KEY'] = FAKE_KEY
        self.sent = []

    def tearDown(self):
        anthropic.Anthropic = self.orig
        if self.old_key is None:
            os.environ.pop('ANTHROPIC_API_KEY', None)
        else:
            os.environ['ANTHROPIC_API_KEY'] = self.old_key

    def serve(self, handler):
        orig, sent = self.orig, self.sent

        def wrapped(req):
            sent.append((dict(req.headers), json.loads(req.content)))
            return handler(req)

        class Patched(orig):
            def __init__(self, **kw):
                kw['http_client'] = httpx.Client(transport=httpx.MockTransport(wrapped))
                kw['max_retries'] = 0
                super().__init__(**kw)
        anthropic.Anthropic = Patched

    def ok(self, **kw):
        self.serve(lambda req: httpx.Response(200, json=ok_json(**kw)))

    def err(self, status, message):
        self.serve(lambda req: httpx.Response(
            status, json={'type': 'error', 'error': {'type': 'invalid_request_error', 'message': message}}))

    def body(self):
        return self.sent[-1][1]

    # ---- 요청 내용 ----
    def test_default_request(self):
        self.ok()
        r = C.call('질문', model_key='sonnet', max_tokens=100)
        h, b = self.sent[-1]
        self.assertEqual(h.get('accept-encoding'), 'identity')
        self.assertFalse({'temperature', 'top_p', 'top_k'} & set(b))
        self.assertNotIn('thinking', b)
        self.assertNotIn('output_config', b)
        self.assertEqual(b['model'], 'claude-sonnet-5-5')
        self.assertEqual(r.text, '확인')                 # 생각 블록이 앞에 와도 text만 읽음
        self.assertFalse(r.truncated)

    def test_cache_prefix_marks_only_the_long_prefix(self):
        self.ok()
        C.call('질문입니다', model_key='sonnet', cache_prefix='법령 전체 …')
        content = self.body()['messages'][0]['content']
        self.assertEqual(content[0], {'type': 'text', 'text': '법령 전체 …', 'cache_control': {'type': 'ephemeral'}})
        self.assertEqual(content[1], {'type': 'text', 'text': '질문입니다'})
        self.ok()
        C.call('질문입니다', model_key='sonnet')                       # 주지 않으면 이전과 같은 글 하나
        self.assertEqual(self.body()['messages'][0]['content'], '질문입니다')

    def test_sonnet_thinking_off_uses_between_tools(self):
        self.ok()
        r = C.call('x', model_key='sonnet', thinking='off', effort='high')
        self.assertEqual(self.body()['thinking'], {'type': 'between_tools'})
        self.assertEqual(self.body()['output_config'], {'effort': 'high'})
        self.assertIn('between_tools', r.settings['thinking'])

    def test_sonnet_between_tools_without_effort_and_adaptive_low(self):
        self.ok()
        C.call('x', model_key='sonnet', thinking='between_tools')
        self.assertEqual(self.body()['thinking'], {'type': 'between_tools'})
        self.assertNotIn('output_config', self.body())
        C.call('x', model_key='sonnet', thinking='adaptive', effort='low')
        self.assertEqual(self.body()['thinking'], {'type': 'adaptive'})
        self.assertEqual(self.body()['output_config'], {'effort': 'low'})

    def test_haiku_off_is_disabled(self):
        self.ok(model='claude-haiku-5-5')
        C.call('x', model_key='haiku', thinking='off')
        self.assertEqual(self.body()['thinking'], {'type': 'disabled'})

    def test_bad_combinations_are_blocked_before_any_request(self):
        self.ok()
        cases = [dict(model_key='sonnet', thinking='disabled'),                       # Sonnet은 disabled를 거절
                 dict(model_key='sonnet', thinking='off', effort='xhigh'),            # between_tools는 high 이하만
                 dict(model_key='sonnet', thinking='between_tools', effort='max'),
                 dict(model_key='haiku', thinking='between_tools'),                   # Haiku는 between_tools 아님
                 dict(model_key='opus', thinking='off'),                              # Opus는 끄는 방법 미확인
                 dict(model_key='opus', thinking='disabled'),
                 dict(model_key='sonnet', thinking='nonsense'),
                 dict(model_key='sonnet', effort='extreme')]
        for kw in cases:
            with self.assertRaises(C.AIError, msg=str(kw)) as cm:
                C.call('x', **kw)
            self.assertEqual(cm.exception.kind, '설정 오류', str(kw))
        self.assertEqual(self.sent, [])                                               # 요청은 한 번도 안 나감

    def test_models_table_matches_checked_facts(self):
        self.assertEqual(MODELS['sonnet'].thinking_off, 'between_tools')
        self.assertEqual(MODELS['sonnet'].default_effort, 'high')
        self.assertEqual(MODELS['haiku'].thinking_off, 'disabled')
        self.assertEqual(MODELS['opus'].thinking_off, '')

    # ---- 키 ----
    def test_missing_key(self):
        os.environ.pop('ANTHROPIC_API_KEY', None)
        self.ok()
        with self.assertRaises(C.AIError) as cm:
            C.call('x')
        self.assertIn('키가 설정되지 않음', cm.exception.kind)
        self.assertEqual(self.sent, [])

    # ---- 오류 문구 (키가 섞여도 가려짐) ----
    def test_error_mapping_and_key_scrubbing(self):
        table = [(401, 'invalid x-api-key', '인증 오류'), (403, 'forbidden', '권한 오류'), (404, 'model not found', '모델을 찾을 수 없음'),
                 (400, 'Your credit balance is too low', '크레딧 부족'),
                 (400, 'You have reached your specified API usage limits', '지출 한도'),
                 (400, '"thinking.type.disabled" is not supported for this model', '생각(thinking) 설정'),
                 (400, 'something else', '요청 형식 오류'), (429, 'rate limited', '속도 제한'), (500, 'oops', '서버 쪽')]
        for status, msg, want in table:
            self.err(status, f'{msg} {FAKE_KEY}')
            with self.assertRaises(C.AIError, msg=f'{status} {msg}') as cm:
                C.call('x')
            e = cm.exception
            self.assertIn(want, e.kind, f'{status} {msg}')
            self.assertNotIn(FAKE_KEY, e.detail + e.kind + e.advice)
            self.assertNotIn(FAKE_KEY, str(e))
        # 생각 400이면 between_tools 안내가 나옴
        self.err(400, '"thinking.type.disabled" is not supported for this model')
        with self.assertRaises(C.AIError) as cm:
            C.call('x')
        self.assertIn('between_tools', cm.exception.advice)

    def test_connection_and_decode_errors(self):
        def boom_net(req):
            raise httpx.ConnectError('connection refused')
        self.serve(boom_net)
        with self.assertRaises(C.AIError) as cm:
            C.call('x')
        self.assertEqual(cm.exception.kind, '네트워크 연결 실패')
        self.assertIn('brotli', cm.exception.advice)

        ns = {}
        exec(compile("def decompress(d):\n    raise TypeError('an integer is required')", 'brotli_shim.py', 'exec'), ns)

        def boom_decode(req):
            ns['decompress'](b'')
        self.serve(boom_decode)
        with self.assertRaises(C.AIError) as cm:
            C.call('x')
        self.assertIn('응답을 푸는 단계', cm.exception.kind)

    def test_truncated_flag_and_usage_format(self):
        self.ok(stop='max_tokens')
        r = C.call('x', max_tokens=5)
        self.assertTrue(r.truncated)
        tok, money = C.format_usage(r.cost, truncated=True)
        self.assertIn('잘렸을 수 있음', tok)
        self.assertIn('1센트 미만', money)


if __name__ == '__main__':
    unittest.main(verbosity=1)
