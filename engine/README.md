# CORSIA 계산·검증 엔진 + 웹 화면 v0.5.0

`calc_spec_v1.md` (v1.2)의 산식 C-01 ~ C-12와
`verification_rules_v1.md` (v1.3)의 검증 규칙을 파이썬으로 구현한 것이다.
엔진은 외부 의존성이 없다. 표준 라이브러리만 쓴다 (Python 3.9+).
웹 화면(`app.py`)만 Streamlit을, 엑셀 내려받기만 openpyxl을 쓴다 — `pip install -r requirements.txt`. (AI 정성 검증 버튼은 `anthropic` 패키지와 환경 변수 `ANTHROPIC_API_KEY`가 있는 PC에서만 동작한다. 없으면 안내 한 줄만 나온다.)
(`requirements.txt`에는 한글을 넣지 않는다. 한국어 윈도우의 pip이 cp949로 읽어 설치가 실패한다.)

---

## 빠른 시작

```bash
# 전체 점검 — 설치가 제대로 됐는지 이것 하나로 확인 (14개 항목 모두 "통과"면 정상)
python check_all.py

# 웹 화면 — 반드시 engine 폴더에서 실행 (.streamlit/config.toml의 밝은 테마가 적용된다)
pip install -r requirements.txt
streamlit run app.py

# 기본 브라우저가 아닌 다른 브라우저에서 열고 싶으면: 아래로 실행한 뒤 그 브라우저에서 localhost:8501을 직접 연다
streamlit run app.py --server.headless true

# 계산 + 검증 + 종합점수 통합 실행 — 웹과 같은 엔진, 같은 결과
python -m corsia full --dataset ./dataset --documents dataset/documents.json

# 골든 케이스 검산 — ICAO 공개 예시 28건과 대조
python -m corsia golden --dataset ./dataset

# 전체 파이프라인 실행
python -m corsia run --dataset ./dataset --method BLOCK_OFF_ON

# 검증엔진 실행 (항공편 데이터만)
python -m corsia verify --dataset ./dataset

# 검증엔진 실행 (EMP/ER/EUCR 문서 포함)
python -m corsia verify --dataset ./dataset --documents dataset/documents.json

# 오류주입본 검증 — 심은 오류가 전부 걸리는지 확인
python -m corsia verify --dataset ./dataset \
    --flights flights_dirty.csv --saf saf_batches_dirty.csv \
    --documents dataset/documents_dirty.json

# 결과를 파일로
python -m corsia run --dataset ./dataset --out result.json

# 오류주입본으로 실행
python -m corsia run --dataset ./dataset --flights flights_dirty.csv

# 단위테스트
python tests/test_engine.py
```

---

## 현재 상태

| 검증 | 결과 |
|---|---|
| 골든 케이스 (ICAO 공개 예시) | **28 / 28 통과** |
| 계산엔진 단위테스트 | **49 / 49 통과** |
| 검증엔진 단위테스트 | **51 / 51 통과** |
| 엑셀 내려받기 단위테스트 (`tests/test_excel.py`) | **21개** (LibreOffice 열기 1개는 `CORSIA_TEST_LIBREOFFICE=1`일 때만) |
| 웹 화면 시험 (`tests/test_web_ui.py`) | **24개** (AI는 모의 응답, 기억 폴더는 임시 폴더) |
| `flights_clean.csv` 총 CO₂ | **1,899,987.0 t** (데이터셋 설계값과 일치) |
| 등록 검증 규칙 | **63건** (형식 18 · 임계값 9 · 일관성 28 · 정성 8) |
| **정상본 오탐** | **0건** → 검증의견 "만족" |
| **오류주입본 미탐** | **0건** → 주입 39종 전부 검출, 검증의견 "불만족" |
| 종합점수 (보조 지표) | 정상본 **100.0** · 오류주입본 **25.8** |

---

## 구조

```
corsia/
├─ constants.py       C-00  상수 (FCF, 밀도, 임계값, %S/%O 표, 중요성 기준)
├─ fuel_use.py        C-01~C-05  연료 사용량 5방법
├─ co2.py             C-06, C-07  CO₂ 환산 · CERT 판정 · 데이터갭 비율
├─ offsetting.py      C-08, C-09  연도별 상쇄의무량 · OGF · EU-ETS 조정
├─ cef_reduction.py   C-10  적격연료 감축량
├─ final_offset.py    C-11, C-12  최종 상쇄의무량 · 면제 판정 · 중요성 기준
├─ io_utils.py        CSV 로딩(UTF-8 → cp949 재시도) · 올린 파일 점검 · 항공기별 시간순 정렬
├─ cli.py             파이프라인 + 명령행 도구 (run · verify · full · golden)
└─ verify/            검증엔진
   ├─ rules.py           규칙 등록 체계 (@rule 데코레이터) · 심각도 · 결과 자료구조
   ├─ format_threshold.py  F 형식 7건 · T 임계값 9건
   ├─ consistency.py       X 일관성 22건
   ├─ qualitative.py       Q 정성 8건 (LLM 페이로드 생성까지만)
   ├─ documents_rules.py   서류 형식·서류 간 일치·상쇄 대상 국가쌍 17건
   ├─ scoring.py           종합점수 (심각도 가중 + 판정별 상한)
   ├─ violation_text.py    규칙 제목 → 화면용 위반 문구
   ├─ display.py           화면용 대응표 (판정 보류 자료 이름 · 위반 상세 수치 이름·단위)
   └─ engine.py            컨텍스트 구성 · 규칙 실행 · 검증의견 수렴 · 종합점수

app.py                  웹 화면 (Streamlit) — 표시만 한다
requirements.txt        웹 화면 의존성 (ASCII만 — 한글 금지)
.streamlit/config.toml  화면 테마를 밝은 모드로 고정 (ASCII만 — 한글 금지)
```

실행 흐름:

```
flights_clean.csv + aircraft.csv + params.csv
  → MTOW 필터 (법 제6조①1 "초과")
  → fuel_use.compute_sequence   항공기별 시간순, 5방법 중 택1
  → co2.annual_co2              상쇄대상편만 집계
  → offsetting.eu_ets_adjustment  EU 역내 구간 차감 (지침 제9조③)
  → offsetting.offsetting_requirement  OR_y
  → cef_reduction.emission_reduction   ER_y (saf_batches.csv)
  → final_offset.final_offsetting_requirement  FOR
  → final_offset.materiality   중요성 임계치 (ICAO 2%/5%)
```

---

## 설계상 중요한 결정 5가지

**1. Decimal을 쓰고 float을 쓰지 않는다.**
배출량은 톤 단위 금액에 준하는 값이라 부동소수점 오차가 누적되면 안 된다. 반올림도 `round()`가 아니라 `ROUND_HALF_UP`을 쓴다. 파이썬 기본 `round()`는 은행가 반올림이라 `round(0.5)`가 0이 된다. 지침이 "가까운 톤단위로 반올림"이라고 한 것은 사사오입을 뜻한다. 단, **최종 상쇄의무량만은 ICAO 부속서 16 3.4.4("rounded up")에 따라 톤 단위 올림**(`ROUND_CEILING`)을 쓴다. 국내 지침은 반올림이지만 ICAO 기준 단일 적용 방침을 따른다.

**2. 상수를 함수 안에 쓰지 않는다.**
3.16, 0.8, 5700 같은 값이 코드 곳곳에 흩어지면 규정이 바뀔 때 어디를 고쳐야 하는지 알 수 없다. 전부 `constants.py`에 모았고 근거 조항을 주석으로 달았다.

**3. 계산엔진은 판정하지 않는다.**
허용되지 않는 연료종류(F-17)를 만나도 예외를 던지고 멈추는 대신, `errors` 목록에 기록하고 그 편만 건너뛴다. 무엇이 문제인지 판정하는 건 검증엔진(5단계)의 몫이다. 계산엔진이 판정까지 하면 오류가 하나만 있어도 전체 계산이 죽는다.

**4. 중요성 기준은 ICAO 기준 하나만 쓴다.**
SARPs App.6 3.5.1에 따라 연간 국제선 배출량 50만 t 초과 2%, 이하 5%를 적용한다. X-12가 재계산값과 보고값의 차이를 이 임계치와 비교한다. v0.2.0까지는 환경부 검증지침의 국내 기준(2.0%/2.5%/5.0%)과 병기했으나 ICAO 기준만 적용하기로 확정되어 걷어냈다.

**5. 미확정 사항은 경고로 흘려보낸다.**
2021~2023 구간처럼 국내 지침에 없는 부분은 계산은 하되 `warning` 필드에 사유를 담아 반환한다. 조용히 넘어가면 나중에 왜 그 값이 나왔는지 추적할 수 없다.

---

## 함정이 있었던 곳

구현하면서 실제로 걸렸던 지점들이다. 코드를 고칠 때 주의.

| 위치 | 함정 |
|---|---|
| `fuel_use.method_a` | 급유가 없는 편은 `T`를 **블록오프 연료량**으로 대체해야 한다. 급유 후 탱크량과 블록오프 연료량은 다른 값이다 (ETM 3.3.5.4.2) |
| `fuel_use.fuel_uplift_group` | 배분 대상에 **급유편 자신이 포함**된다. `F_N = U_N`은 구간이 1편일 때의 결과일 뿐이다 |
| `offsetting.ogf` | `(OE_y − OE_B) / OE_y`이며 결과는 **퍼센트**다. `OE_y / OE_B`(배수)가 아니다 |
| `cef_reduction` | `FCF`가 합계 **밖**에서 한 번만 곱해진다. 연료 계열이 섞이면 계산이 정의되지 않아 `MixedFuelClassError`를 던진다 |
| `co2.is_obligor_aircraft` | MTOW는 "초과"다. 정확히 5,700kg은 **제외**된다 |
| `co2.cert_eligible` | 5만톤은 "이상"이면 CERT 불가다. 정확히 50,000t은 별표2 의무 |

---

## 검증엔진 설계

**계산엔진을 안에서 재사용한다.** 영 제8조제2항제3호가 "이행의무자가 산정한 배출량과 검증기관이 같은 별표에 따라 산정한 배출량이 일치하지 않는 경우 보완을 요청할 수 있다"고 명령한다. 즉 검증기관은 **독립적으로 다시 계산해서 대조**해야 한다. `X-12`가 그 규칙이고, 대조 허용오차로 중요성 임계치를 쓴다.

**규칙 하나 = 함수 하나.** `@rule(id, layer, severity, title, source)` 데코레이터로 등록한다. 함수는 위반 사례 리스트를 반환하고, 빈 리스트면 통과다. 근거 조항(`source`)이 없으면 테스트가 실패한다.

**자료가 없으면 통과가 아니라 SKIPPED.** 문서를 안 넘기면 문서 규칙은 `SKIPPED`가 되고 검증의견에 영향을 주지 않는다. 자료 부족을 통과로 처리하면 "검증했다"는 거짓 신호가 된다.

**심각도 → 검증의견 수렴** (SARPs App.6 3.10)

```
차단 1건 이상      → 불만족 (검증 범위 제한·데이터 신뢰 불가)
치명 1건 이상      → 불만족
중요·경미만 있음   → 의견부 만족 (위반 내용 명시)
위반 없음          → 만족
```

`정보` 등급(T-05 면제 판정 등)은 `NOTICE`로 표시되고 판정에 영향을 주지 않는다.

**조건부 규칙이 있다.** `Q-06`(데이터갭 초과 사유 서술)은 갭이 5%를 넘었을 때만 요구된다. 조건 없이 걸면 정상 보고서가 전부 걸린다.

---

## 검증엔진에서 잡았던 오탐 2건

규칙을 처음 구현했을 때 **정상본에서 걸린 것들**이다. 규칙 설계 문제였고 전부 수정했다.

| 규칙 | 무엇이 문제였나 | 어떻게 고쳤나 |
|---|---|---|
| `X-22` | 이상치를 **기종 평균** 기준으로 비교 → EU 역내 단거리편(5t)이 B77W 장거리 중앙값(95t)에 걸려 106건 오탐 | **노선별 중앙값** 기준으로 변경, 표본 5편 미만 노선은 판정 제외 |
| `Q-06` | 데이터갭이 없는데도 "갭 초과 사유" 서술을 요구 | 갭 5% 초과 시에만 요구하는 **조건부 규칙**으로 변경 |

둘 다 "걸려야 할 걸 걸었나"만 재고 있었으면 발견하지 못했을 문제다. 오탐 측정이 필요한 이유가 여기 있다.

---

## 아직 안 한 것

| 항목 | 이유 |
|---|---|
| CERT 배출량 계산 | ICAO 배포 도구이므로 재구현하지 않는다. 판정 로직까지만 구현하고 값은 외부 입력 (calc_spec §2 C-07) |
| Q 계층 실제 판정 | LLM 페이로드 생성까지만 구현. 실제 호출은 7단계 |
| 검증 규칙 2건 | `verification_rules_v1.md` 78건 중 12건 범위 제외, 대상 66건 중 64건 반영(코드 63개). 미반영 2건(T-03, T-06)은 2024년 시나리오에 해당 없음 |
| 국가쌍 집계 | 데이터셋이 공항쌍 기준이라 현재 불필요. 필요하면 공항쌍을 집계해 만든다 |
| 인수·합병 시 기준배출량 합산 | ETM 4.3.2. 시나리오에 해당 사례가 없어 미구현 |

---

## 웹 화면 (10단계)

설계는 `9_web_design/web_design_v1.md`에 있다. 화면은 **표시만** 하고 계산·검증·종합점수는 전부 엔진이 한다. 그래서 `streamlit run app.py`와 `python -m corsia full`의 결과가 항상 같다.

```
데이터 선택(샘플) 또는 올리기
  → corsia.cli.run_pipeline    계산
  → corsia.verify.verify       검증 + 종합점수
  → 화면: 요약 상자 → 위반 목록 → 정성 검증 → 계산 상세·원본 데이터
  → 결과 JSON 내려받기
```

**종합점수** (`verify/scoring.py`) — 심각도별 배점(치명 5 · 중요 2 · 경미 1 · 정보 0)을 주고 잃은 배점 비율로 환산한 뒤, 판정별 상한(만족 100 / 의견부 만족 89 / 불만족 59)을 전체 점수에만 적용한다. SKIPPED는 분모에서 빼고, 위반 건수는 점수에 넣지 않는다(X-19가 1,102건까지 나와 한 규칙이 점수를 지배하기 때문). **상한 구간은 ICAO 기준이 아니라 팀이 정한 보조 기준이다.**

**위반 문구** (`verify/violation_text.py`) — 규칙 제목은 "미해결 데이터 공백 없음"처럼 요건 문장이라 위반 목록에 그대로 쓰면 문제가 없다는 뜻으로 읽힌다. 그래서 화면 표시용 위반 문장("미해결 데이터 공백 있음")을 따로 두고, 규칙 제목(`title`)은 문서와 일치하도록 그대로 둔다. 규칙을 새로 추가하면 이 표에도 넣어야 한다 — 빠지면 `check_all.py`가 잡는다.

**한글 줄바꿈** — 화면 전체(본문·사이드바)에 `word-break: keep-all !important; overflow-wrap: anywhere;`를 준다. `!important`가 없으면 Streamlit이 문단(`p`)에 거는 `word-break: break-word`가 이겨서 문단만 단어 중간에서 끊긴다. 표 칸에는 `overflow-wrap: normal`을 준다(`anywhere`는 표 칸을 글자 한 개 폭까지 줄인다).

**좁은 창** — 위반 목록은 목록 폭 900px 이하에서 근거 조항 칸을 비율로 바꾸고, 620px 이하에서는 한 줄을 세 줄로 쌓는다(규칙 번호+감점 / 위반 내용 / 근거 조항+건수). 실제 Firefox로 1366~800px 7개 폭을 재서 감점 41줄이 모두 보이고 한글이 단어 중간에서 끊기지 않는 것을 확인했다.

**올린 파일** — CSV는 UTF-8(BOM 포함)로 읽다가 실패하면 cp949(한국어 엑셀 기본 저장)로 다시 읽는다. 필수 열이 빠졌거나 빈 파일이면 엔진에 넣기 전에 `io_utils.check_csv`가 사람이 읽는 문구로 알려 준다. 필수 열은 열을 하나씩 빼서 엔진이 멈추는 열을 찾아 정했다(비행 기록 6개, 적격연료 4개). 결과 JSON의 `dataset`에는 전체 경로 대신 폴더 이름만 남긴다.

**외부 도구가 여는 파일에는 한글 금지** — `requirements.txt`(pip), `.streamlit/config.toml`(Streamlit)처럼 우리 코드가 아니라 외부 도구가 직접 읽는 파일은 ASCII만 쓴다. 한국어 윈도우에서는 이 도구들이 파일을 cp949로 읽어 UTF-8 한글이 있으면 실패한다. `check_all.py`가 이를 점검한다.

**결과 내려받기** — `결과 내려받기 (JSON)`과 `결과 내려받기 (엑셀)`이 같은 결과 객체에서 만들어진다. 엑셀 시트는 요약 · 위반 목록 · 점수 근거 · 계산 상세, 그리고 AI 정성 검증을 실행했을 때만 정성 검증이다(`corsia/excel_export.py`). 올린 파일의 글자가 `=`·`+`·`-`·`@`로 시작해도 수식으로 저장되지 않도록 모든 글자 칸을 문자열로 저장한다. 엑셀에는 PC 경로를 넣지 않는다.

**AI 정성 검증** — 정성 검증 구역에서 「AI 정성 검증 실행」 버튼을 눌렀을 때만 호출한다(자동 실행 없음). 버튼에 예상 비용과 1회 상한($0.30)이 보인다. 같은 서류는 화면과 `engine/ai_cache/`에 기억해 다시 부르지 않는다(비용 0). 결과는 참고용이며 요약 상자·종합점수·위반 목록은 AI와 무관하게 코드가 계산한 값 그대로다. 키가 없으면 「AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다」 한 줄만 나온다. 시험(`tests/test_web_ui.py`)은 AI를 부르지 않고, 기억 폴더도 임시 폴더로 바꿔 모의 응답이 `engine/ai_cache/`에 섞이지 않게 한다.

**샘플 3종** — 정상본 · 오류주입본 · 서술 부실 예시(가상 서류, `dataset/gen_weak.py`가 정상본에 시험 세트 Q03-D2를 덮어써 만든다). 서술 부실 예시의 코드 판정·점수는 정상본과 같다(코드는 서술이 비어 있는지만 보므로). 정상본의 서술도 AI는 일부 항목에서 「보완 필요」로 볼 수 있으므로, 화면·문서에 「정상본의 서술이 충분하다」는 뜻으로 쓰지 않는다.

---

## 파라미터 바꾸기

`dataset/params.csv`만 고치면 된다. 코드는 건드리지 않는다.

| 키 | 지금 값 | 비고 |
|---|---|---|
| `SGF_2024` | 0.15405257 | ICAO **2024년 공식값** (이행연도 2024) |
| `OE_B_2024` | 952000 | 2019년 배출량의 85% |
| `MATERIALITY_STANDARD` | ICAO | SARPs App.6 3.5.1 (참고용 표시값, 엔진은 항상 ICAO 기준 사용) |
| `REPORTING_LEVEL` | AERODROME_PAIR | 지침 별표4 5-a 택1 |
