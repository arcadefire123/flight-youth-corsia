"""
전체 점검 스크립트 - 팀원 PC에서 이것 하나만 돌리면 된다.
    engine 폴더에서:  python check_all.py

확인하는 것
  1. 필수 파일이 제자리에 다 있는가
  2. 골든 케이스 28건 (ICAO 공개 예시)
  3. 계산엔진·검증엔진 단위테스트
  4. 정상본 → 최종 상쇄의무량 265,773톤, 검증의견 "만족", 종합점수 100.0
  5. 오류주입본 → 주입한 규칙이 전부 걸리고 검증의견 "불만족", 종합점수 25.8
  6. 모든 규칙에 화면 표시용 위반 문구가 붙어 있는가
  7. 외부 도구가 직접 여는 파일(requirements.txt 등)에 한글이 없는가
     - 한국어 윈도우의 pip은 파일을 cp949로 읽어 UTF-8 한글이 있으면 설치가 실패한다
"""
import csv
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# 윈도우 한글 콘솔(cp949)에서 표현 못 하는 문자가 있어도 멈추지 않도록
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'dataset'
sys.path.insert(0, str(ROOT))

REQUIRED = [
    'corsia/__init__.py', 'corsia/__main__.py', 'corsia/constants.py', 'corsia/fuel_use.py',
    'corsia/co2.py', 'corsia/offsetting.py', 'corsia/cef_reduction.py', 'corsia/final_offset.py',
    'corsia/io_utils.py', 'corsia/cli.py',
    'corsia/verify/__init__.py', 'corsia/verify/rules.py', 'corsia/verify/format_threshold.py',
    'corsia/verify/consistency.py', 'corsia/verify/qualitative.py', 'corsia/verify/engine.py',
    'corsia/verify/scoring.py', 'corsia/verify/violation_text.py', 'corsia/verify/display.py', 'app.py',
    'requirements.txt', '.streamlit/config.toml',
    'corsia/excel_export.py', 'corsia/web_view.py', 'tests/test_excel.py', 'tests/test_web_ui.py',
    'dataset/documents_weak.json', 'dataset/gen_weak.py',
    'tests/test_engine.py', 'tests/test_verify.py',
    'dataset/flights_clean.csv', 'dataset/flights_dirty.csv', 'dataset/aircraft.csv',
    'dataset/saf_batches.csv', 'dataset/saf_batches_dirty.csv', 'dataset/params.csv',
    'dataset/golden_cases.csv', 'dataset/boundary_cases.csv',
    'dataset/documents.json', 'dataset/documents_dirty.json',
    'dataset/error_injection_log.csv', 'dataset/document_error_log.csv',
    'dataset/corsia_states_2024.csv', 'corsia/verify/documents_rules.py',
]
EXPECTED_FOR = 265773
EXPECTED_SCORE_CLEAN = '100.0'   # 정상본 종합점수 (web_design_v1.md §2.4 S-0)
EXPECTED_SCORE_DIRTY = '25.8'    # 오류주입본 종합점수 (S-5)
results = []


def step(name, ok, detail=''):
    results.append(ok)
    print(f"  [{'통과' if ok else '실패'}] {name}" + (f'  - {detail}' if detail else ''))


print('=' * 64)
print('CORSIA 엔진 전체 점검')
print('=' * 64)

# 1. 파일
missing = [p for p in REQUIRED if not (ROOT / p).exists()]
step(f'필수 파일 {len(REQUIRED)}개', not missing, '누락: ' + ', '.join(missing) if missing else '')
if missing:
    print('\n누락 파일이 있어 이후 점검을 중단합니다. carbon_project.zip을 다시 풀거나 가이드북 「파일 보관 구조」를 확인하세요.')
    sys.exit(1)

from corsia.cli import run_golden, run_pipeline   # noqa: E402
from corsia.verify import verify                  # noqa: E402

# 2. 골든
g = run_golden(DATA)
step('골든 케이스 (ICAO 공개 예시)', g['failed'] == 0, f"{g['passed']}/{g['total']}")

# 3. 단위테스트
for name in ('test_engine', 'test_verify', 'test_excel', 'test_web_ui'):
    buf = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=f'{name}.py')
    with redirect_stdout(buf):
        r = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
    step(f'단위테스트 {name}', r.wasSuccessful(), f'{r.testsRun}개 실행, 실패 {len(r.failures) + len(r.errors)}')

# 4. 정상본
p = run_pipeline(DATA)
step('정상본 최종 상쇄의무량', p['C11_final']['FOR_t'] == EXPECTED_FOR,
     f"{p['C11_final']['FOR_t']:,}톤 (기대 {EXPECTED_FOR:,}톤)")
docs = json.loads((DATA / 'documents.json').read_text(encoding='utf-8'))
v = verify(DATA, **docs)
fails = [f['rule_id'] for f in v['findings'] if f['status'] == 'FAIL']
step('정상본 검증 (오탐 0)', not fails and v['opinion']['result'] == '만족',
     f"의견 {v['opinion']['result']}, 오탐 {len(fails)}건")

# 5. 오류주입본
injected = set()
for fn in ('error_injection_log.csv', 'document_error_log.csv'):
    with open(DATA / fn, encoding='utf-8-sig') as f:
        injected |= {row['rule_id'] for row in csv.DictReader(f)}
dd = json.loads((DATA / 'documents_dirty.json').read_text(encoding='utf-8'))
vd = verify(DATA, flights_file='flights_dirty.csv', saf_file='saf_batches_dirty.csv', **dd)
caught = {f['rule_id'] for f in vd['findings'] if f['status'] == 'FAIL'}
miss = injected - caught
step('오류주입본 검증 (미탐 0)', not miss and vd['opinion']['result'] == '불만족',
     f"주입 {len(injected)}종 중 검출 {len(injected - miss)}종" + (f', 미탐 {sorted(miss)}' if miss else ''))

# 6. 종합점수 (9단계 설계 · verification_rules §5.1)
sc_c, sc_d = v['score'], vd['score']
ok_score = (str(sc_c['overall']) == EXPECTED_SCORE_CLEAN
            and str(sc_d['overall']) == EXPECTED_SCORE_DIRTY)
step('종합점수', ok_score,
     f"정상본 {sc_c['overall']} (기대 {EXPECTED_SCORE_CLEAN}) · "
     f"오류주입본 {sc_d['overall']} (기대 {EXPECTED_SCORE_DIRTY})")

# 7. 위반 문구 누락 여부
from corsia.verify.violation_text import missing_ids   # noqa: E402
from corsia.verify.rules import get_registry           # noqa: E402
lack = missing_ids([r.id for r in get_registry()])
step('위반 문구 (화면 표시용)', not lack,
     f"규칙 {len(get_registry())}개 전부 등록" if not lack else f'누락: {lack}')

# 8. 외부 도구용 파일은 ASCII만 (한국어 윈도우 cp949 대비)
ASCII_ONLY = ['requirements.txt', '.streamlit/config.toml']
bad = [fn for fn in ASCII_ONLY if not (ROOT / fn).read_bytes().isascii()]
step('외부 도구용 파일 한글 없음', not bad,
     ', '.join(ASCII_ONLY) if not bad else '한글 포함: ' + ', '.join(bad))

# 9. 서술 부실 예시 샘플: 코드 판정은 정상본과 같고, 생성 스크립트(gen_weak.py) 결과와 같아야 한다
dw = json.loads((DATA / 'documents_weak.json').read_text(encoding='utf-8'))
vw = verify(DATA, **dw)
same = (vw['opinion']['result'] == '만족' and str(vw['score']['overall']) == EXPECTED_SCORE_CLEAN
        and vw['computed']['FOR_t'] == EXPECTED_FOR)
step('서술 부실 예시 (코드 판정은 정상본과 같음)', same,
     f"{vw['opinion']['result']} · {vw['score']['overall']}점 · {vw['computed']['FOR_t']:,}톤")
sys.path.insert(0, str(DATA))
import gen_weak                                        # noqa: E402
step('서술 부실 예시 = 생성 스크립트 결과', gen_weak.build() == dw,
     'documents_weak.json을 손으로 고쳤다면 python dataset/gen_weak.py로 다시 만든다')

print('-' * 64)
print('전체 결과:', '모두 통과' if all(results) else f'{results.count(False)}건 실패')
sys.exit(0 if all(results) else 1)
