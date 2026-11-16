"""
Claude API 호출부 (7단계)

원칙
  1. API 키는 윈도우 사용자 환경 변수 ANTHROPIC_API_KEY에서만 읽는다.
     이 코드는 키 값을 읽어 출력하거나 저장하지 않는다. 있는지 여부만 확인한다.
  2. 모델은 별칭('haiku', 'sonnet', 'opus')으로 고른다. 실제 ID와 단가는 models.py에만 있다.
  3. 호출할 때마다 사용 토큰과 예상 비용을 함께 돌려준다.
  4. temperature 같은 샘플링 값은 보내지 않는다.
     Haiku 5.5는 기본값이 아닌 temperature·top_p·top_k를 400 오류로 거절한다(공식 이전 안내서).
  5. 응답의 첫 블록을 답으로 가정하지 않고, type이 'text'인 블록만 모은다
     (5.5 모델은 생각 블록이 먼저 올 수 있다).
  6. 응답을 압축하지 말라고 요청한다(Accept-Encoding: identity).
     오래된 brotli 패키지가 있는 PC에서 압축 해제 중 TypeError가 나는 문제를 피한다
     (2026-11-01 팀장 PC, 아나콘다 기본 brotli 1.0.9에서 발생).
  7. 생각(thinking)과 노력 수준(effort)은 호출할 때 고를 수 있다. 5.5 모델은 생각이 기본으로
     켜져 있고 생각 토큰도 max_tokens 안에 들어가며 출력 단가로 청구된다.
     max_tokens는 상한일 뿐이고 실제로 만든 토큰만 청구되므로, 답이 잘리지 않게 여유 있게 둔다.

anthropic 패키지는 함수 안에서 불러온다. 이 패키지가 없어도 계산·검증 엔진과
check_all.py는 그대로 돌아간다.
"""
import os
import re
from dataclasses import dataclass, field

from .models import get_model, estimate_cost

ENV_KEY = 'ANTHROPIC_API_KEY'
_KEY_PATTERN = re.compile(r'sk-ant-[A-Za-z0-9_\-]+')

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
THINKING = ('adaptive', 'disabled', 'between_tools', 'off')
# 생각을 끌 수 있는 노력 수준 (공식 안내: Haiku 5.5의 disabled, Sonnet 5.5의 between_tools 모두 high 이하에서만)
_THINKING_OFF_OK = ('low', 'medium', 'high')

MAX_TOKENS_JUDGE = 4000   # 기준표 판정(JSON) 권장 상한. 생각 토큰까지 들어가므로 여유 있게
_NO_COMPRESSION = {'Accept-Encoding': 'identity'}


def key_is_set():
    """키가 설정되어 있는지만 본다. 값은 돌려주지 않는다."""
    return bool((os.environ.get(ENV_KEY) or '').strip())


def _scrub(text, limit=200):
    """오류 문구에 혹시 키 모양 문자열이 섞여 있으면 가리고, 길이를 줄인다."""
    s = _KEY_PATTERN.sub('[가림]', str(text or ''))
    return s if len(s) <= limit else s[:limit] + '…'


@dataclass
class AIError(Exception):
    kind: str            # 사람이 읽는 오류 종류
    advice: str          # 무엇을 하면 되는지
    status: int = 0      # HTTP 상태 코드 (없으면 0)
    detail: str = ''     # 키를 가린 짧은 원문

    def __str__(self):
        code = f', HTTP {self.status}' if self.status else ''
        return f'{self.kind}{code}'


@dataclass
class CallResult:
    model_key: str
    model_id: str
    text: str
    cost: dict = field(default_factory=dict)   # estimate_cost() 결과 (usd, tokens, ...)
    stop_reason: str = ''
    settings: dict = field(default_factory=dict)  # 이번 호출의 max_tokens, thinking, effort

    @property
    def truncated(self):
        """max_tokens에 닿아 답이 중간에 끊겼는지. 생각 토큰이 상한을 다 쓰면 text가 비어 있을 수도 있다."""
        return self.stop_reason == 'max_tokens'


def _looks_like_decode_error(exc):
    """연결 오류의 원인 사슬에 압축 해제(decode·brotli·gzip) 흔적이 있는지 본다."""
    words = ('decod', 'brotli', 'gzip', 'zlib', 'decompress')
    seen, cur = 0, exc
    while cur is not None and seen < 8:
        text = f'{type(cur).__module__}.{type(cur).__name__} {cur}'.lower()
        if any(w in text for w in words):
            return True
        # 오류 문구에 흔적이 없어도(예: 맨 TypeError) 오류가 난 코드 위치가 압축 해제 쪽인지 본다
        tb = cur.__traceback__
        while tb is not None:
            where = (tb.tb_frame.f_code.co_filename + ' ' + tb.tb_frame.f_code.co_name).lower()
            if any(w in where for w in words):
                return True
            tb = tb.tb_next
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


def resolve_thinking(model_key, thinking, effort):
    """요청에 실제로 넣을 thinking 종류를 정하고, 모델이 받아들이지 않을 조합은 호출 전에 막는다.

    thinking : None(보내지 않음 = 모델 기본값) | 'adaptive' | 'off'(모델에 맞는 끄기 방식으로 바꿈)
               | 'disabled' | 'between_tools'(이름을 직접 지정. 그 모델이 받는 경우만 허용)
    반환: 요청에 넣을 type 문자열 또는 None. 잘못된 조합이면 AIError(설정 오류) — 요청은 보내지 않는다.
    """
    m = get_model(model_key)
    if thinking is not None and thinking not in THINKING:
        raise AIError('설정 오류', f'thinking은 {THINKING} 중 하나여야 합니다.')
    if effort is not None and effort not in EFFORTS:
        raise AIError('설정 오류', f'effort는 {EFFORTS} 중 하나여야 합니다.')
    if thinking is None or thinking == 'adaptive':
        return thinking
    kind = m.thinking_off if thinking == 'off' else thinking
    if not m.thinking_off:
        raise AIError('설정 오류', f'{m.model_id}에서 생각을 끄는 방법은 확인하지 않았습니다(corsia/ai/models.py의 thinking_off). '
                                  '생각 설정을 빼고(기본값) 쓰세요.')
    if kind != m.thinking_off:
        raise AIError('설정 오류', f'{m.model_id}는 thinking {kind!r}를 받지 않습니다(공식 문서 확인). '
                                  f"생각을 끄려면 {m.thinking_off!r}(또는 'off')를 쓰세요.")
    if effort not in (None,) + _THINKING_OFF_OK:
        raise AIError('설정 오류', f'생각 끄기({kind})는 effort가 high 이하일 때만 가능합니다(xhigh·max는 400 오류).')
    return kind


def _describe(exc, anthropic):
    """SDK 예외를 AIError로 바꾼다. 원문은 키를 가린 짧은 형태로만 남긴다."""
    status = getattr(exc, 'status_code', 0) or 0
    msg = _scrub(getattr(exc, 'message', '') or exc)
    low = msg.lower()
    if isinstance(exc, anthropic.APITimeoutError):
        return AIError('응답 시간 초과', '잠시 뒤 다시 실행하세요.', 0, msg)
    if isinstance(exc, anthropic.APIConnectionError):
        if _looks_like_decode_error(exc):
            return AIError('응답을 푸는 단계의 오류 (네트워크 연결 실패로 표시됨)',
                           '응답 압축 해제 라이브러리 문제일 가능성이 큽니다. 아나콘다 프롬프트에서 '
                           ' pip install -U brotli  를 실행한 뒤 다시 시도하세요.', 0, msg)
        return AIError('네트워크 연결 실패',
                       '인터넷 연결이나 학교 방화벽을 확인하세요. 인터넷이 정상인데도 계속 나오면 '
                       '응답을 푸는 단계의 오류일 수 있습니다(오래된 brotli 패키지 등).  pip install -U brotli  '
                       '를 실행한 뒤 다시 시도하세요.', 0, msg)
    if status == 401:
        return AIError('인증 오류(키가 틀렸거나 삭제·만료됨)',
                       'Console의 API 키 목록에서 키 상태를 확인하고, 필요하면 새 키를 만들어 setx로 다시 저장하세요.',
                       status, msg)
    if status == 403:
        return AIError('권한 오류', '키의 범위(워크스페이스)와 조직 상태를 Console에서 확인하세요.', status, msg)
    if status == 404:
        return AIError('모델을 찾을 수 없음', 'corsia/ai/models.py의 모델 ID를 확인하세요.', status, msg)
    if status == 400 and 'credit balance' in low:
        return AIError('크레딧 부족', '크레딧이 바닥나 호출이 멈춘 상태입니다. 결제수단 등록·충전은 하지 말고 팀장에게 알리세요.',
                       status, msg)
    if status == 400 and 'thinking' in low:
        return AIError('생각(thinking) 설정을 모델이 받아들이지 않음',
                       'Sonnet 5.5는 disabled 대신 between_tools(--thinking off)를 씁니다. 모델별 허용 값은 '
                       'corsia/ai/models.py의 thinking_off를 보세요.', status, msg)
    if status == 400 and ('usage limit' in low or 'spend' in low):
        return AIError('지출 한도 도달', 'Console 지출 한도에 닿았습니다. 한도를 올릴지는 팀장이 결정합니다.',
                       status, msg)
    if status == 429:
        return AIError('속도 제한', '1분쯤 기다렸다가 다시 실행하세요.', status, msg)
    if status >= 500:
        return AIError('서버 쪽 일시 오류', '잠시 뒤 다시 실행하세요.', status, msg)
    if status == 400:
        return AIError('요청 형식 오류', '요청 내용(모델, 매개변수)을 확인하세요.', status, msg)
    return AIError(f'알 수 없는 오류({type(exc).__name__})', '오류 종류를 팀장에게 알려 주세요.', status, msg)


def call(user_text, model_key='sonnet', system=None, max_tokens=1024, timeout=120,
         thinking=None, effort=None, cache_prefix=None):
    """Claude를 한 번 호출하고 CallResult를 돌려준다. 실패하면 AIError를 던진다.

    thinking : None(모델 기본값, 5.5는 adaptive) | 'adaptive' | 'off'(모델에 맞게: Haiku disabled, Sonnet between_tools)
               | 'disabled' | 'between_tools'
    effort   : None(모델 기본값, Haiku 5.5는 medium, Sonnet 5.5는 high) | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
    둘 다 SDK 버전과 무관하게 동작하도록 요청 본문(extra_body)으로 보낸다.
    cache_prefix : 여러 호출에서 똑같이 반복되는 긴 앞부분(예: 법령 전체). 주면 그 부분에 5분 캐시 표시(cache_control)를 달아
                   두 번째 호출부터 캐시 읽기 단가(입력의 약 1/20)로 청구된다. 비용은 estimate_cost가 캐시 쓰기·읽기로 따로 계산한다.
                   캐시가 실제로 먹었는지는 cost['tokens']의 cache_read로 확인한다.
    """
    thinking_type = resolve_thinking(model_key, thinking, effort)
    if not key_is_set():
        raise AIError('API 키가 설정되지 않음',
                      '아나콘다 프롬프트에서 setx로 ANTHROPIC_API_KEY를 저장한 뒤 창을 새로 여세요.')
    # SDK의 디버그 로그(요청·응답 내용을 화면에 찍음)가 켜져 있으면 이 실행에서만 끈다.
    # SDK는 불러오는 순간 이 값을 읽으므로 import보다 먼저 지워야 한다.
    os.environ.pop('ANTHROPIC_LOG', None)
    try:
        import anthropic
    except ImportError:
        raise AIError('anthropic 패키지가 없음', '아나콘다 프롬프트에서  pip install anthropic  을 실행하세요.')
    import logging
    for _name in ('anthropic', 'httpx', 'httpx2', 'httpcore'):
        logging.getLogger(_name).setLevel(logging.WARNING)

    m = get_model(model_key)
    kwargs = {
        'model': m.model_id,
        'max_tokens': max_tokens,
        'messages': [{'role': 'user', 'content': user_text}],
    }
    if cache_prefix:
        kwargs['messages'] = [{'role': 'user', 'content': [
            {'type': 'text', 'text': cache_prefix, 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': user_text}]}]
    if system:
        kwargs['system'] = system
    body = {}
    if thinking_type is not None:
        body['thinking'] = {'type': thinking_type}
    if effort is not None:
        body['output_config'] = {'effort': effort}
    if body:
        kwargs['extra_body'] = body

    # 키는 SDK가 환경 변수에서 직접 읽는다. 응답 압축은 받지 않는다(원칙 6).
    client = anthropic.Anthropic(timeout=timeout, default_headers=_NO_COMPRESSION)
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.APIError as exc:
        raise _describe(exc, anthropic) from None   # 원래 예외 체인은 출력에 남기지 않는다

    text = ''.join(getattr(b, 'text', '') for b in resp.content if getattr(b, 'type', '') == 'text')
    return CallResult(model_key=model_key, model_id=m.model_id, text=text,
                      cost=estimate_cost(model_key, resp.usage),
                      stop_reason=getattr(resp, 'stop_reason', '') or '',
                      settings={'max_tokens': max_tokens,
                                'thinking': (f'{thinking}→{thinking_type}' if thinking == 'off' else thinking_type) or '모델 기본값',
                                'effort': effort or '모델 기본값'})


def format_usage(cost, truncated=False):
    """사용 토큰과 예상 비용을 한 줄씩 사람이 읽기 좋게 만든다.
    truncated=True면 답이 max_tokens에서 잘렸다는 경고를 붙인다."""
    t = cost['tokens']
    tok = f"입력 {t['input']:,} · 출력 {t['output']:,}"
    if t['cache_write'] or t['cache_read']:
        tok += f" · 캐시 쓰기 {t['cache_write']:,} · 캐시 읽기 {t['cache_read']:,}"
    usd = cost['usd']
    money = f'${usd:.6f}' if usd < 0.01 else f'${usd:.4f}'
    if usd < 0.01:
        money += ' (1센트 미만)'
    if cost.get('long_prompt'):
        money += ' · 10만 토큰 초과 단가 적용'
    if cost.get('batch'):
        money += ' · 배치 50% 할인 적용'
    if truncated:
        tok += ' · [주의] max_tokens에 닿아 답이 잘렸을 수 있음'
    return tok, money
