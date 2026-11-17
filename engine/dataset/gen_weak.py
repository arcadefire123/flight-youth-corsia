"""
서술 부실 예시 샘플 서류 생성 (documents_weak.json)

정상본 서류(documents.json)에 시험 세트(7_qualitative/qual_testset_v1.json)의 사례 Q03-D2를 덮어쓴다.
  Q03-D2 : 노선 결정 절차 서술(EMP.route_determination)이 「국제와 국내로 나눈다」는 말만 있고
           무엇을 기준으로 나누는지(출발·도착 국가)가 없는 사례 (시험 세트 정답: 보완 필요)

가상 서류다. 기존 샘플과 같게 서류 안의 「한국항공탄소검증원(가상)」이 그대로 들어 있고, 서류에 새 항목은 추가하지 않는다
(verify()는 알려진 항목만 받는다). 숫자·다른 서술은 정상본과 같으므로 코드의 판정·점수·상쇄의무량도 정상본과 같다.

데이터 파일을 손으로 고치지 말고 이 스크립트를 다시 돌린다 (결과는 항상 같다).
    engine 폴더에서:  python dataset/gen_weak.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
sys.path.insert(0, str(ENGINE))

from run_web_qual import apply_testset_case   # noqa: E402

CASE_ID = 'Q03-D2'


def build():
    base = json.loads((HERE / 'documents.json').read_text(encoding='utf-8'))
    return apply_testset_case(base, CASE_ID)


def main():
    out = HERE / 'documents_weak.json'
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'저장: dataset/{out.name}  (정상본 + 시험 세트 {CASE_ID})')


if __name__ == '__main__':
    main()
