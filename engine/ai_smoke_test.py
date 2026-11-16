"""
7단계 A - Claude API 연결 시험

    engine 폴더에서:  python ai_smoke_test.py

Haiku 5.5에 짧은 질문 1개를 보내 연결이 되는지 본다. 예상 비용은 1센트 미만이다.
화면에는 성공 여부, 사용 토큰, 비용만 나온다. API 키는 어디에도 출력하지 않는다.
"""
import sys
from pathlib import Path

# 윈도우 한글 콘솔(cp949)에서 표현 못 하는 문자가 있어도 멈추지 않도록
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corsia.ai import call, AIError, format_usage, MODELS   # noqa: E402

MODEL = 'haiku'
PROMPT = '연결 시험입니다. "확인"이라고만 답하세요.'


def main():
    print(f'연결 시험 (모델: {MODELS[MODEL].model_id})')
    try:
        r = call(PROMPT, model_key=MODEL, max_tokens=200)
    except AIError as e:
        print(f'  성공 여부 : 실패 - {e}')
        print(f'  할 일     : {e.advice}')
        print('  사용 토큰 : 0 (응답 없음)')
        print('  비용      : $0')
        return 1
    tok, money = format_usage(r.cost, truncated=r.truncated)
    print('  성공 여부 : 성공')
    print(f'  사용 토큰 : {tok}')
    print(f'  비용      : {money}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
