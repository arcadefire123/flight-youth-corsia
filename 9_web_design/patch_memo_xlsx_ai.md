# 패치 메모 — 웹 AI 정성 검증 + 엑셀 내려받기 (채팅 A, 2026-11-17)

| 항목 | 내용 |
|---|---|
| 용도 | `check_all.py` · `requirements.txt` · `README.md`는 **통째로 보내지 않았다**(다른 채팅 변경과 충돌 방지). 아래 줄만 직접 바꿔 넣는다 |
| 함께 받은 zip | `carbon_project_stage10_xlsx_ai.zip` — 새 파일 6개와 바뀐 파일 4개(아래 표), 바뀐 파일만 |
| 검증 | 팀장이 보낸 `engine_for_chatA.zip`에 이 zip을 덮어쓰고 이 메모의 패치를 적용한 뒤 `check_all.py` **14개 항목 모두 통과**를 확인했다 |

## 0. 덮어쓰기 순서

1. `engine` 폴더를 통째로 복사해 백업한다 (11/17 밤까지 안 되면 백업으로 돌아간다).
2. zip을 `carbon_project`가 있는 위치에서 풀어 덮어쓴다 (경로가 `carbon_project/…`로 시작한다).
3. 아래 1~3의 패치를 적용한다 (`check_all.py` → `requirements.txt` → `README.md` 순서).
4. 아나콘다 프롬프트에서 `cd …\engine` 후 `pip install -r requirements.txt` (openpyxl이 아나콘다에 이미 있으면 바로 끝난다), `python check_all.py`.

| 파일 | 구분 |
|---|---|
| `engine/app.py` | 수정 (fix2 이후 팀장이 고치지 않았음을 확인) |
| `engine/corsia/verify/display.py` | 수정 — `skip_summary` 함수 하나 추가 |
| `engine/corsia/excel_export.py` | 신규 — 엑셀·JSON 내려받기 |
| `engine/corsia/web_view.py` | 신규 — AI 결과 카드 HTML |
| `engine/dataset/gen_weak.py` · `documents_weak.json` | 신규 — 서술 부실 예시 샘플 |
| `engine/tests/test_excel.py` · `test_web_ui.py` | 신규 시험 |
| `9_web_design/web_design_v1.md` | 수정 (v1.5) |
| `9_web_design/patch_memo_xlsx_ai.md` | 신규 (이 파일) |

## 1. `engine/check_all.py` (3곳)

### 1-1. 필수 파일 목록에 6개 추가 (`REQUIRED` 안, `'requirements.txt', '.streamlit/config.toml',` 줄 바로 아래)

**찾을 내용**

```
    'requirements.txt', '.streamlit/config.toml',
```

**바꿀 내용**

```
    'requirements.txt', '.streamlit/config.toml',
    'corsia/excel_export.py', 'corsia/web_view.py', 'tests/test_excel.py', 'tests/test_web_ui.py',
    'dataset/documents_weak.json', 'dataset/gen_weak.py',
```

### 1-2. 단위테스트 루프에 새 시험 2개 추가

**찾을 내용**

```
for name in ('test_engine', 'test_verify'):
```

**바꿀 내용**

```
for name in ('test_engine', 'test_verify', 'test_excel', 'test_web_ui'):
```

### 1-3. 파일 끝부분, `print('-' * 64)` 바로 위에 점검 2개 추가

**찾을 내용**

```
print('-' * 64)
print('전체 결과:'
```

**바꿀 내용**

```
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
print('전체 결과:'
```


적용하면 점검 항목이 10개에서 **14개**가 된다 (시험 2개 + 서술 부실 예시 점검 2개 추가). 필수 파일은 38개에서 44개.

## 2. `engine/requirements.txt` (1곳)

### 2-1. `openpyxl>=3.1` 한 줄 추가 (영문·ASCII만, 주석 없음)

**찾을 내용**

```
streamlit>=1.30
```

**바꿀 내용**

```
streamlit>=1.30
openpyxl>=3.1
```


한글·주석을 넣지 않는다(한국어 윈도우 pip이 cp949로 읽어 실패한다). `anthropic` 패키지는 일부러 넣지 않았다 — 채팅 C의 사용법 7항대로 키가 있는 PC에서만 필요하고, 없으면 안내만 나온다. 4명 모두에게 필요하면 팀이 정해 한 줄(`anthropic>=0.40` 등 실제 설치한 버전 이상)을 추가한다.

## 3. `engine/README.md` (4곳)

### 3-1. 6행: 의존성 안내

**찾을 내용**

```
웹 화면(`app.py`)만 Streamlit을 쓴다 — `pip install -r requirements.txt`.
```

**바꿀 내용**

```
웹 화면(`app.py`)만 Streamlit을, 엑셀 내려받기만 openpyxl을 쓴다 — `pip install -r requirements.txt`. (AI 정성 검증 버튼은 `anthropic` 패키지와 환경 변수 `ANTHROPIC_API_KEY`가 있는 PC에서만 동작한다. 없으면 안내 한 줄만 나온다.)
```

### 3-2. 14행: 점검 항목 수

**찾을 내용**

```
(10개 항목 모두 "통과"면 정상)
```

**바꿀 내용**

```
(14개 항목 모두 "통과"면 정상)
```

### 3-3. 「현재 상태」 표의 검증엔진 단위테스트 줄 아래에 두 줄 추가

**찾을 내용**

```
| 검증엔진 단위테스트 | **51 / 51 통과** |
```

**바꿀 내용**

```
| 검증엔진 단위테스트 | **51 / 51 통과** |
| 엑셀 내려받기 단위테스트 (`tests/test_excel.py`) | **21개** (LibreOffice 열기 1개는 `CORSIA_TEST_LIBREOFFICE=1`일 때만) |
| 웹 화면 시험 (`tests/test_web_ui.py`) | **15개** (AI는 모의 응답, 기억 폴더는 임시 폴더) |
```

### 3-4. 「웹 화면 (10단계)」 절 끝, `## 파라미터 바꾸기` 바로 위에 단락 3개 추가

**찾을 내용**

```
---

## 파라미터 바꾸기
```

**바꿀 내용**

```
**결과 내려받기** — `결과 내려받기 (JSON)`과 `결과 내려받기 (엑셀)`이 같은 결과 객체에서 만들어진다. 엑셀 시트는 요약 · 위반 목록 · 점수 근거 · 계산 상세, 그리고 AI 정성 검증을 실행했을 때만 정성 검증이다(`corsia/excel_export.py`). 올린 파일의 글자가 `=`·`+`·`-`·`@`로 시작해도 수식으로 저장되지 않도록 모든 글자 칸을 문자열로 저장한다. 엑셀에는 PC 경로를 넣지 않는다.

**AI 정성 검증** — 정성 검증 구역에서 「AI 정성 검증 실행」 버튼을 눌렀을 때만 호출한다(자동 실행 없음). 버튼에 예상 비용과 1회 상한($0.30)이 보인다. 같은 서류는 화면과 `engine/ai_cache/`에 기억해 다시 부르지 않는다(비용 0). 결과는 참고용이며 요약 상자·종합점수·위반 목록은 AI와 무관하게 코드가 계산한 값 그대로다. 키가 없으면 「AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다」 한 줄만 나온다. 시험(`tests/test_web_ui.py`)은 AI를 부르지 않고, 기억 폴더도 임시 폴더로 바꿔 모의 응답이 `engine/ai_cache/`에 섞이지 않게 한다.

**샘플 3종** — 정상본 · 오류주입본 · 서술 부실 예시(가상 서류, `dataset/gen_weak.py`가 정상본에 시험 세트 Q03-D2를 덮어써 만든다). 서술 부실 예시의 코드 판정·점수는 정상본과 같다(코드는 서술이 비어 있는지만 보므로). 정상본의 서술도 AI는 일부 항목에서 「보완 필요」로 볼 수 있으므로, 화면·문서에 「정상본의 서술이 충분하다」는 뜻으로 쓰지 않는다.

---

## 파라미터 바꾸기
```


## 4. 인수 시험 (팀장 PC)

| 확인 | 기준 / 이렇게 한다 |
|---|---|
| `python check_all.py` | 14개 모두 통과 |
| 정상본 · 오류주입본 | 100.0점 · 25.8점, 위반 41건(치명 22)이 그대로. 엑셀 요약 시트도 같은 값 |
| 키가 있을 때, 샘플 「서술 부실 예시」 | 버튼 라벨에 「예상 약 $0.10, 최대 $0.30」. 누르면 진행 막대 → 카드 8개. **판정 「만족」·점수 100.0·상쇄의무량 265,773 t는 실행 전과 같다** |
| 키가 없을 때 | 새 아나콘다 프롬프트에서 `set ANTHROPIC_API_KEY=` (그 프롬프트에서만 키가 사라진다) 후 `streamlit run app.py` → 오류 없이 지금 화면 + 한 줄 |
| 같은 서류로 다시 실행 | 브라우저 새로고침 → 버튼이 「기억에서 N건 · 비용 0」 → 실행 → 「기억에서」 배지, 실제 비용 $0.0000, 호출 없음 |
| 엑셀 | 엑셀에서 「복구」 메시지 없이 바로 열린다 (**이 환경에서는 실제 엑셀로 못 열어 봤다 — 꼭 확인**). 위반 목록·점수 근거·계산 상세가 JSON과 같은 값. AI 실행 후에만 「정성 검증」 시트 |
| JSON | AI를 실행했을 때만 `qualitative` 키가 있다 |

알아 둘 것
- 정상본·서술 부실 예시 모두 AI가 Q-01을 「보완 필요」로 볼 수 있다(기준표 C2·C3: 보고서 작성 단계·담당부서). 오류가 아니다. 화면·문서에 「정상본의 서술이 충분하다」는 뜻을 쓰지 않았다.
- AI 실행이 끝나면 화면이 한 번 다시 그려진다. 그 뒤 기억이 있으면 「다시 실행 (기억 사용 · 비용 0)」 버튼만 남는다.
- 시험(`test_web_ui.py`)은 AI를 부르지 않고 기억 폴더를 임시 폴더로 바꾼다. 시험이 `engine/ai_cache/`를 만들거나 바꾸지 않는다.
- `LibreOffice`가 있는 PC에서 엑셀 열기까지 시험하려면 `set CORSIA_TEST_LIBREOFFICE=1` 후 `python tests/test_excel.py`.

## 5. 채팅 C가 줄 수정 zip과의 관계

- 항목별 `source`(api / cache / mock)가 있으면 「기억에서」(cache)·「시험용 모의 응답」(mock) 배지로 표시한다. 없으면 기존 `cached` 값으로 「기억에서」를 판단한다 — **C의 zip 없이도, 받은 뒤에도 동작한다.**
- C의 zip이 `web_qual.py`의 `excel_rows` 열 이름을 바꾸면 엑셀 「정성 검증」 시트가 그대로 따라간다(열 이름을 코드에 고정하지 않았다). 단 `tests/test_excel.py`의 `test_qual_sheet_columns_and_badge`는 `excel_rows`와 같은 열인지만 본다.
- C가 `iter_qualitative` / `estimate_qualitative`의 인자 이름·결과 구조를 바꾸면 `app.py`의 `ai_block`·`run_ai` 두 함수만 고치면 된다.

## 6. 문서에 옮길 내용 (가이드북 · 현황 색인 · MANIFEST — 다른 채팅이 수정 중이라 직접 고치지 않음)

- **MANIFEST**: `engine/corsia/excel_export.py`, `engine/corsia/web_view.py`, `engine/dataset/gen_weak.py`, `engine/dataset/documents_weak.json`, `engine/tests/test_excel.py`, `engine/tests/test_web_ui.py`, `9_web_design/patch_memo_xlsx_ai.md`
- **현황 색인**: 10단계 — 「AI 정성 검증 버튼 · 엑셀 내려받기 구현(모의 시험 + 실제 브라우저 확인 완료, 실제 엑셀 열기는 인수 시험). check_all 14개, 시험: 계산 49 · 검증 51 · 엑셀 21 · 화면 15」
- **가이드북 7·10단계**: 「웹은 AI를 버튼으로만 부른다. 판정·점수·위반 목록은 AI와 무관하게 코드가 계산한다. 같은 서류는 기억해 다시 부르지 않는다. 결과는 JSON과 엑셀로 내려받는다(PDF 없음).」
- **발표 이야기 거리**: 「AI를 붙여도 판정·점수는 한 글자도 안 바뀐다는 것을 시험으로 고정했다」 · 「엑셀에 올린 파일의 글자가 `=`로 시작하면 수식으로 실행될 수 있어 전부 문자열로 저장한다」 · 「시험이 모의 응답을 진짜 기억 폴더에 남기면 나중에 실제 호출이 모의 결과를 돌려받는다 — 시험은 임시 폴더만 쓴다」
