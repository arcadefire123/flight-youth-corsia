"""
AI 모델 이름과 가격표 (7단계)

모델을 바꿀 때는 이 파일의 MODELS만 고친다. 호출 코드는 'haiku', 'sonnet', 'opus' 같은
별칭만 쓰고 실제 모델 ID나 단가를 직접 적지 않는다.

가격 단위: USD / 100만 토큰. 2026-11-01 확인.
  - Haiku 5.5  : 공식 모델 페이지 platform.claude.com/docs/en/models/haiku-5-5/overview
                 (프롬프트가 10만 토큰을 넘으면 그 요청 전체가 모든 단가 x5)
  - Sonnet 5.5 : 공식 가격표를 인용한 보도 여러 건 교차 확인
  - Opus 5.5   : 위와 같음. 캐시 쓰기 단가는 입력 단가 x1.25로 계산(직접 확인 못 함)
여기서 계산한 값은 '예상 비용'이다. 실제 차감액은 Console의 비용 페이지가 기준이다.
"""
from dataclasses import dataclass

PRICE_CHECKED = '2026-11-01'
PRICE_SOURCE = 'https://platform.claude.com/docs/en/about-claude/pricing'


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    input: float            # 일반 입력
    output: float           # 출력 (생각 토큰 포함)
    cache_write: float      # 5분 캐시 쓰기
    cache_read: float       # 캐시 읽기
    long_prompt_tokens: int = 0       # 이 값을 넘는 프롬프트는 단가가 바뀜 (0이면 없음)
    long_prompt_factor: float = 1.0   # 그때 곱하는 배수
    thinking_off: str = ''            # 생각을 끄는 요청 방식: 'disabled' | 'between_tools' | ''(확인 안 됨 → 끄기 막음)
    default_effort: str = ''          # 노력 수준을 안 보낼 때 API 기본값 ('' = 확인 안 됨)


# 생각 끄기·기본 노력 수준 (공식 문서로 확인한 값만 적는다. 2026-11-13)
#   Sonnet 5.5: thinking {"type":"disabled"}는 400 오류. 끄려면 {"type":"between_tools"}(effort low·medium·high에서만,
#               다른 필드와 같이 못 보냄). thinking을 안 보내면 adaptive, API 기본 effort는 high
#               https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide
#   Haiku 5.5 : thinking {"type":"disabled"} 가능(effort high 이하에서만). API 기본 effort는 medium
#   Opus 5.5  : 기본 노력 수준 medium(Anthropic 발표문 anthropic.com/claude-opus-5-5, 2026-11-13 확인).
#               생각 끄기는 공식 문서를 직접 확인하지 못해(보조 출처는 「끌 수 없음」) 막아 둠 → 생각 설정을 보내지 않는 기본만 씀
MODELS = {
    'haiku':  ModelInfo('claude-haiku-5-5',  0.10,  0.50, 0.125, 0.01,
                        long_prompt_tokens=100_000, long_prompt_factor=5.0,
                        thinking_off='disabled', default_effort='medium'),
    'sonnet': ModelInfo('claude-sonnet-5-5', 2.00, 10.00, 2.50, 0.10,
                        thinking_off='between_tools', default_effort='high'),
    'opus':   ModelInfo('claude-opus-5-5',   4.00, 20.00, 5.00, 0.20,
                        default_effort='medium'),
}

DEFAULT_MODEL = 'sonnet'   # 반복 작업 기본값. 최종 비교만 opus, 연결 시험은 haiku


def get_model(key):
    if key not in MODELS:
        raise KeyError(f"모르는 모델 별칭: {key!r} (가능: {', '.join(MODELS)})")
    return MODELS[key]


def usage_numbers(usage):
    """API 응답의 usage(객체 또는 dict)를 정수 4개로 정리한다. 없는 값은 0."""
    def g(name):
        v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(v or 0)
    return {
        'input': g('input_tokens'),
        'output': g('output_tokens'),
        'cache_write': g('cache_creation_input_tokens'),
        'cache_read': g('cache_read_input_tokens'),
    }


def estimate_cost(model_key, usage, batch=False):
    """예상 비용(USD)과 계산 근거를 돌려준다.
    batch=True면 입력·출력 50% 할인(Batch API)을 적용한다."""
    m = get_model(model_key)
    u = usage_numbers(usage)
    prompt_tokens = u['input'] + u['cache_write'] + u['cache_read']
    factor = 1.0
    if m.long_prompt_tokens and prompt_tokens > m.long_prompt_tokens:
        factor = m.long_prompt_factor
    disc = 0.5 if batch else 1.0
    usd = (u['input'] * m.input * disc
           + u['output'] * m.output * disc
           + u['cache_write'] * m.cache_write
           + u['cache_read'] * m.cache_read) * factor / 1_000_000
    return {
        'usd': usd,
        'tokens': u,
        'long_prompt': factor != 1.0,
        'batch': batch,
        'model_id': m.model_id,
    }
