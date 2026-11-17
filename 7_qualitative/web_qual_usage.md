# 웹 연결용 정성 검증 사용법 (채팅 A 전달용, 2026-11-17)

AI 참고 의견(서술 8개를 충분 / 보완 필요 / 판단 불가 + 이유 + 근거 조항으로 판정)을 웹에서 부르는 방법입니다. 코드는 `engine/corsia/ai/web_qual.py`에 있고 **웹은 이 파일의 함수만 부르면 됩니다.** 판정·종합점수·위반 목록은 이 모듈과 무관하게 검증엔진(코드)이 계산하며 이 모듈은 엔진을 부르지도 값을 바꾸지도 않습니다. **AI 결과를 점수나 상단 요약 상자에 넣지 마세요.**

## 1. 부르는 법

```python
from corsia.ai import web_qual

docs_for_ai = json.loads(docs_text) if docs_text else {}      # 서류 JSON 전체({'documents': {...}})나 그 안쪽 둘 다 됨
gap = c['data_gap_pct']                                        # 엔진이 계산한 데이터갭 비율(Q-06 조건 판단용). 안 주면 서류의 ER.data_gap_pct

# ① 실행 전: 예상 비용(호출 없음, 예외 없음)
est = web_qual.estimate_qualitative(docs_for_ai, data_gap_pct=gap)
#   est['key_set']        키가 있는지(없으면 버튼 대신 안내 한 줄)
#   est['n_calls']        실제로 부를 횟수(보통 7회, 데이터갭 5% 이하면 Q-06은 건너뜀)
#   est['n_cached']       기억에서 가져올 수(그만큼 비용 0)
#   est['estimated_usd']  예상 비용(약 0.10), est['max_usd'] = 0.30 (1회 실행 상한)
#   est['input_hash']     같은 서류를 알아보는 열쇠 → st.session_state 기억의 key로 쓰세요
#   est['skipped']        [{rule_id, state, message}] AI를 안 부르는 규칙과 이유

# ② 버튼을 눌렀을 때만: 항목이 끝나는 대로 하나씩 받기(진행 표시)
for ev in web_qual.iter_qualitative(docs_for_ai, data_gap_pct=gap):
    if ev['type'] == 'plan':   ...   # {'n_total': 8, 'n_calls': 7, 'n_cached': 0, 'estimated_usd': 0.098, 'max_usd': 0.3}
    if ev['type'] == 'item':   ...   # {'item': {...}, 'done': 3, 'total': 8}  ← 끝난 순서(규칙 번호 순서 아님)
    if ev['type'] == 'done':   result = ev['result']      # qualitative_result_v1 (항목은 Q-01~Q-08 순서)

# (한 번에 받기) result = web_qual.run_qualitative(docs_for_ai, data_gap_pct=gap, on_item=lambda item, done, total: ...)
```

- **호출은 규칙마다 1회, 동시에 4개씩** 보냅니다(7회 호출은 2묶음이라 팀장 PC 실측 약 19초, 순차라면 약 60초. `max_workers=7`로 올리면 1묶음으로 약 10초가 되지만 API 속도 제한에 걸릴 위험이 있음). 항목 하나가 끝나면 바로 `item` 사건이 나오므로 진행 막대와 항목 카드를 하나씩 채울 수 있습니다.
- **같은 서류는 다시 부르지 않습니다.** 서버 디스크(`engine/ai_cache/`)에 규칙별로 기억하므로 두 번째 실행은 비용 0입니다. 웹 쪽 세션 기억은 `est['input_hash']`를 key로 쓰면 됩니다(서류나 설정이 바뀌면 값이 바뀜).
- 웹 화면 흐름 예:

```python
est = web_qual.estimate_qualitative(docs_for_ai, data_gap_pct=gap)
store = st.session_state.setdefault('qual_results', {})
if not est['key_set']:
    st.caption(web_qual.NO_KEY_TEXT)                          # 「AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다」 한 줄, 지금 화면 그대로
elif est['input_hash'] in store:
    result = store[est['input_hash']]                         # 같은 서류 재실행: AI를 부르지 않음
elif st.button(f"AI 정성 검증 실행 (예상 약 ${est['estimated_usd']:.2f}, 최대 ${est['max_usd']:.2f})"):
    bar = st.progress(0.0)
    for ev in web_qual.iter_qualitative(docs_for_ai, data_gap_pct=gap):
        if ev['type'] == 'item':
            bar.progress(ev['done'] / ev['total'])            # 항목 카드를 여기서 하나씩 그려도 됨
        elif ev['type'] == 'done':
            result = ev['result']
    if result['status'] == 'ok':
        store[est['input_hash']] = result
```

## 2. 결과 구조 (`qualitative_result_v1`) — JSON 내려받기에 그대로 넣을 수 있음

아래는 모의 응답으로 만든 예시를 줄인 것입니다(값은 예시). 키는 항상 이 모양입니다.

```json
{
 "schema": "qualitative_result_v1",
 "status": "ok",
 "status_message": "",
 "ai_reference_only": true,
 "badge": "AI 참고용 · 최종 판정 아님",
 "disclaimer": "AI 참고 의견입니다. 검증 판정·종합점수·위반 목록은 코드가 계산하며 이 결과와 무관합니다. 서술이 사실인지, 다른 서류와 값이 맞는지는 판정하지 않습니다.",
 "created": "2026-11-17T21:30:00",
 "settings": {
  "model": "claude-sonnet-5-5",
  "model_alias": "sonnet",
  "thinking": "adaptive",
  "effort": "low",
  "mode": "together",
  "max_tokens": 4000,
  "rubric_version": "v1.8"
 },
 "input_hash": "3f9a1c0be27d4e51",
 "monitoring_method": {
  "value": "BLOCK_OFF_ON",
  "source": "EMP.monitoring_method"
 },
 "cost": {
  "estimated_usd": 0.0982,
  "actual_usd": 0.0865,
  "max_usd": 0.3,
  "n_calls": 7,
  "n_cached": 0,
  "tokens": {
   "input": 23870,
   "output": 3920
  }
 },
 "timing": {
  "seconds": 19.4,
  "workers": 4
 },
 "summary": {
  "n_rules": 8,
  "n_items": 8,
  "verdicts": {
   "충분": 6,
   "보완 필요": 1,
   "판단 불가": 0
  },
  "n_failed": 0,
  "fake_citations": 0,
  "sources": {
   "api": 7,
   "cache": 0,
   "mock": 0
  }
 },
 "items": [
  {
   "rule_id": "Q-01",
   "document": "EMP",
   "item": "data_management",
   "question": "원천자료에서 보고서까지 자료 흐름의 각 단계와 담당부서가 식별 가능하게 기술되었는가",
   "cited_clause": "지침 별표1 5a",
   "status": "ok",
   "message": "",
   "verdict": "보완 필요",
   "reason": "자료 흐름이 보고서 작성 단계 없이 끝난다.",
   "missing_points": [
    "보고서 작성 단계와 담당부서를 적는다"
   ],
   "checks": [
    {
     "id": "C1",
     "kind": "필수",
     "text": "출발점: 원천자료가 무엇인지(시스템·서류 이름)",
     "status": "부족",
     "ai_status": "부족",
     "evidence": "운항관리시스템(OCS)에서 항공편별 블록오프·블록온 연료량을 자동 수집하…",
     "field": "EMP.data_management",
     "note": "지목할 수 없음",
     "flag": null,
     "citation_problem": false,
     "fake_citation": false
    },
    {
     "id": "C2",
     "kind": "필수",
     "text": "끝점까지 이어짐: 원천자료부터 배출량보고서 작성까지 단계가 순서대로 이어짐",
     "status": "없음",
     "ai_status": "없음",
     "evidence": "…",
     "field": null,
     "note": "지목할 수 없음",
     "flag": null,
     "citation_problem": false,
     "fake_citation": false
    }
   ],
   "flags": [],
   "fake_citations": 0,
   "clause_matches": true,
   "ai_cited_clause": "지침 별표1 5a",
   "source": "api",
   "cached": false,
   "cost_usd": 0.0124,
   "tokens": {
    "input": 3410,
    "output": 560
   },
   "seconds": 9.1,
   "truncated": false
  },
  {
   "rule_id": "Q-06",
   "document": "ER",
   "item": "data_gap_explanation",
   "question": "데이터갭 5% 초과 사유·원인·대체방법 설명이 타당한가",
   "cited_clause": "지침 별표4 6-b2",
   "status": "skipped_condition",
   "message": "데이터갭 0.0% ≤ 5% (조건 미발생)",
   "verdict": null,
   "reason": "데이터갭 0.0% ≤ 5% (조건 미발생)",
   "missing_points": [],
   "checks": [],
   "flags": [],
   "fake_citations": 0,
   "clause_matches": null,
   "ai_cited_clause": null,
   "source": null,
   "cached": false,
   "cost_usd": 0.0,
   "tokens": {
    "input": 0,
    "output": 0
   },
   "seconds": 0.0
  },
  "… (Q-02~Q-08 같은 모양, 총 8개, Q-01~Q-08 순서)"
 ]
}
```

- **`item['source']`** (모든 항목, 판정 근거의 출처): `"api"` = 이번에 실제 AI를 호출함, `"cache"` = 같은 입력의 이전 실제 결과를 기억에서 가져옴(비용 0), `"mock"` = 모의 응답(시험용, 진짜 화면에는 나오면 안 됨), `null` = AI를 부르지 않은 항목(조건 미발생·서술 없음·방법 값 없음). `result['summary']['sources']`가 이 수를 집계합니다. 모의 응답은 디스크 기억에 저장되지 않고 진짜 실행에서는 모의 출처 기억을 무시합니다.
- `result['ai_reference_only']`는 항상 `true`, `result['badge']`가 「AI 참고용 · 최종 판정 아님」입니다. 내려받는 JSON에는 `result` 전체를 `"qualitative"` 같은 키 아래 넣으면 됩니다(AI를 실행하지 않았으면 키를 빼거나 `null`).
- 항목별 `verdict`는 **코드가 계산한 최종 판정**입니다(AI가 쓴 판정이 아님). 확인 항목(`checks`)의 `status`는 인용 확인을 거친 뒤의 상태입니다.
- **가짜 인용 표시**: 확인 항목에서 AI가 낸 인용이 서류 글에 없으면 코드가 그 항목을 「판단 불가」로 바꾸고 `fake_citation: true`, `flag`에 이유를 적습니다. 항목 카드에 `fake_citations`(건수)가 0보다 크면 「인용 확인 필요」 같은 작은 표시를 붙여 주세요.
- `cited_clause`는 규칙이 정한 근거 조항(코드 값), `ai_cited_clause`는 AI가 적은 값이고 `clause_matches`가 둘이 같은지(맞으면 true, 아니면 false, 비교 못 하면 null)입니다. 화면에는 `cited_clause`를 쓰세요.

## 3. 상태값 (예외 대신 이것으로 옵니다)

**항목 상태 `item['status']`**

| 값 | 뜻 | 화면 처리 |
|---|---|---|
| `ok` | 판정 받음 | 배지(충분 · 보완 필요 · 판단 불가), 이유, 근거 조항, `missing_points`, 확인 항목 |
| `skipped_condition` | Q-06: 데이터갭 5% 이하라 조건 미발생 | 「해당 없음(조건 미발생)」, `message` |
| `no_text` | 서술이 비어 있어 AI를 안 부름 | 「서술 없음」(비어 있음은 검증엔진의 정성 규칙이 따로 알림) |
| `method_missing` | 서류에 모니터링 방법 값이 없어 Q-02·Q-04를 판정 안 함 | 판정 「판단 불가」, `message` |
| `network_error` / `api_error` / `parse_error` / `budget_exceeded` | 호출 실패·해석 실패·상한 초과 | 「판정 못 받음」 + `message`. 다시 실행하면 받은 항목은 기억에서 가져오고 못 받은 항목만 다시 부름 |

**전체 상태 `result['status']`**: `ok`(모두 끝남) · `partial`(일부 항목만 받음) · `no_key`(키 없음) · `no_text`(판정할 서술 없음) · `budget_exceeded`(예상 비용이 상한 초과, 호출 안 함) · `network_error` · `api_error`(키·크레딧·설정 문제, `detail`에 키를 가린 원문) · `parse_error` · `config_error`(기준표 파일 못 읽음). 화면에 한 줄 안내가 필요하면 `result['status_message']`(또는 `web_qual.status_message(status)`)를 쓰세요. **결제·충전 화면으로 안내하지 마세요**(크레딧 부족이면 팀장에게 알림).

## 4. 화면 문구 상수

- 배지: `web_qual.BADGE_TEXT` = 「AI 참고용 · 최종 판정 아님」(유지)
- 키 없음: `web_qual.NO_KEY_TEXT` = 「AI 정성 검증은 API 키가 있는 컴퓨터에서 실행됩니다」 → 지금 화면 그대로 + 이 한 줄. **키가 없으면 기억(캐시)이 있어도 같은 동작**입니다(`status = 'no_key'`).

## 5. 엑셀 「정성 검증」 시트

```python
rows = web_qual.excel_rows(result)      # 키 없음·미실행이면 []. 열: 규칙, 서류, 항목, AI 참고 판정, 이유, 근거 조항, 보완할 점, 확인 항목별 상태, 인용 문제, 출처(AI 호출 / 기억 / 모의(시험용)), 캐시
```

AI를 실행했을 때(`result['status'] in ('ok', 'partial')`)만 이 시트를 만드세요. 시트 위에 「AI 참고용 · 최종 판정 아님」을 적어 주세요.

## 6. 시연용 「서술 부실 예시」 샘플 만들기

시험 세트(`7_qualitative/qual_testset_v1.json`)의 사례 입력을 정상 서류에 덮어쓰면 됩니다. 노선 결정 절차가 부실한 `Q03-D2`(정답표: 보완 필요)가 가장 단순합니다(다른 칸을 함께 주지 않는 사례).

```python
from run_web_qual import apply_testset_case          # engine 폴더에서
weak = apply_testset_case(json.load(open('dataset/documents.json', encoding='utf-8')), 'Q03-D2')
json.dump(weak, open('dataset/documents_weak.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
```

서류에 「가상 서류」 표시를 넣는 방식은 기존 샘플과 같게 해 주세요. **팀장 PC 실제 호출 결과(2026-11-17)**: 정상 서류에서도 **Q-01이 보완 필요**로 나옵니다(자료 흐름이 보고서 작성 단계 없이 끝나 C2·C3가 부족 — 기준표의 「부족」 예시와 같아서 판정이 맞음). 따라서 **정상본은 Q-01만, 「서술 부실 예시」(Q03-D2를 덮어쓴 샘플)는 Q-01과 Q-03이 보완 필요로 나오는 것이 정상**입니다(Q-03은 정답표와 같음). 나머지 규칙은 충분입니다. 시연 설명에 「정상 서류는 모두 충분」이라고 쓰지 마세요.

## 7. 지켜야 할 것

1. **자동 실행 금지.** 버튼을 눌렀을 때만 호출합니다(비용·지연).
2. 키는 윈도우 환경 변수 `ANTHROPIC_API_KEY`에서만 읽습니다(웹 코드에서 읽거나 출력하지 않음).
3. 결과에는 PC 경로·키가 들어 있지 않습니다. 새로 만드는 파일에도 넣지 마세요.
4. 서류 JSON에서 모니터링 방법 값은 `documents.EMP.monitoring_method`를 우선 쓰고 없으면 `documents.ER.monitoring_method`입니다(모듈이 알아서 읽음). 둘 다 없으면 Q-02·Q-04만 「판단 불가」(`method_missing`)로 두고 나머지는 그대로 판정합니다.
5. 1회 실행 비용 상한은 **$0.30**입니다(실측 호출 1건 약 $0.013, 7~8건이면 약 $0.10). 예상 비용이 상한을 넘으면 호출하지 않고 `budget_exceeded`.
6. `.gitignore`에 `engine/ai_cache/` 한 줄이 들어가야 합니다(기억 폴더, 서류 일부가 들어 있어 GitHub에 올리지 않음). 이 폴더는 코드가 필요할 때 만듭니다.
7. `anthropic` 패키지가 없는 PC는 `status = 'api_error'`로 안내만 나옵니다(오류로 멈추지 않음). 필요한 PC에서 `pip install anthropic`.
