"""
CORSIA 계산·검증 도구 — 웹 화면 (10단계)

    pip install -r requirements.txt     (처음 한 번)
    streamlit run app.py               (engine 폴더에서 실행해야 .streamlit/config.toml이 적용된다)
    streamlit run app.py --server.headless true   (기본 브라우저가 아닌 브라우저로 열 때: localhost:8501을 직접 연다)

설계: 9_web_design/web_design_v1.md
  · 맨 위 한 상자에 최종 판정 · 종합점수 · 최종 상쇄의무량 (§3.1)
  · 위반 목록은 표 + 클릭 시 상세 펼침, 근거 조항은 접지 않아도 보임 (§3.2)
  · 규칙 번호는 줄바꿈 금지, 심각도 색 범례, 좁은 창에서는 한 줄을 세 줄로 쌓기 (§3.3)
  · 정성 검증은 「AI 참고용」 (7단계 연동 전)

화면은 표시만 한다. 계산·검증·종합점수는 전부 엔진(corsia)이 한다.
CLI(`python -m corsia full`)와 같은 엔진을 호출하므로 결과가 항상 같다.
"""
import hashlib
import html
import json
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from corsia.cli import run_pipeline                                   # noqa: E402
from corsia.io_utils import InputError, check_csv, CSV_ENCODINGS      # noqa: E402
from corsia.verify import verify                                      # noqa: E402
from corsia.verify.display import (format_sample, input_label,        # noqa: E402
                                   from_documents_json, josa_i_ga)

DATASET = ROOT / 'dataset'

SEV_COLOR = {'치명': '#D64545', '중요': '#C77700', '경미': '#6B7280', '정보': '#6B7280'}
SEV_ORDER = {'치명': 0, '차단': 0, '중요': 1, '경미': 2, '정보': 3}
GREY = '#6B7280'

SAMPLES = {
    '정상본 (한빛항공 2024)': ('flights_clean.csv', 'saf_batches.csv', 'documents.json'),
    '오류주입본 (한빛항공 2024)': ('flights_dirty.csv', 'saf_batches_dirty.csv', 'documents_dirty.json'),
}

st.set_page_config(page_title='CORSIA 계산·검증 도구', layout='wide')

st.markdown("""
<style>
/* 한글이 단어 중간에서 끊기지 않게 (본문·사이드바 전체). 칸보다 긴 한 단어만 예외로 끊는다.
   word-break에 !important: Streamlit이 마크다운 문단(p)에 word-break:break-word를 더 구체적인
   선택자로 걸어 keep-all이 덮어써졌다 (실측: 문단 12곳, 1280px 「들어갑│니다」). */
html, body, .stApp, .stApp *,
[data-testid="stSidebar"], [data-testid="stSidebar"] * {
  word-break: keep-all !important; overflow-wrap: anywhere;
}
.block-container {max-width: 1100px; padding-top: 2.2rem;}
.sumbox {border:1px solid rgba(128,128,128,.35); border-radius:12px; padding:18px 22px; margin-bottom:14px;}
.sumgrid {display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:18px;}
.lbl {font-size:12px; opacity:.7; margin-bottom:4px;}
.big {font-size:30px; font-weight:600; line-height:1.15;}
.sub {font-size:12px; opacity:.65; margin-top:5px;}
.skipnote {margin-top:8px; padding:7px 10px; border-radius:7px; font-size:12px; line-height:1.55;
           background:rgba(59,110,200,.08); border:1px solid rgba(59,110,200,.35);}
.warnline {margin-top:14px; padding:9px 13px; border-radius:7px;
           background:rgba(214,69,69,.10); border:1px solid rgba(214,69,69,.35); font-size:13px;}
.lyr {display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:16px;
      border-top:1px solid rgba(128,128,128,.28); margin-top:16px; padding-top:14px;}
.bar {height:5px; border-radius:3px; background:rgba(128,128,128,.22); overflow:hidden;}
.bar > span {display:block; height:5px;}

/* ── 위반 목록 ── */
.vwrap {border:1px solid rgba(128,128,128,.32); border-radius:10px; overflow:hidden;
        container-type:inline-size; container-name:vlist;}
.vhead, .vrow {display:grid; gap:10px; align-items:start;
               grid-template-columns:78px minmax(0,1fr) 230px 62px 58px;
               grid-template-areas:"id main src cnt pts";}
.vhead {font-size:11px; opacity:.6; padding:6px 12px;}
.vrow {padding:11px 12px;}
.c-id {grid-area:id;} .c-main {grid-area:main;} .c-src {grid-area:src;}
.c-cnt {grid-area:cnt;} .c-pts {grid-area:pts;}
.nlbl {display:none;}
/* 목록 폭 900px 이하: 근거 조항 칸을 고정(230px)에서 비율로 → 위반 내용 칸이 넓어진다 */
@container vlist (max-width: 900px) {
  .vhead, .vrow {grid-template-columns:78px minmax(0,1fr) minmax(0,.6fr) 56px 52px;}
  .vdet {padding-left:14px;}
}
/* 목록 폭 620px 이하: 가로 스크롤 대신 한 줄을 세 줄로 쌓는다
   1줄 규칙 번호 + 감점 / 2줄 위반 내용 / 3줄 근거 조항 + 건수(작은 글씨) */
@container vlist (max-width: 620px) {
  .vhead {display:none;}
  .vrow {grid-template-columns:minmax(0,1fr) auto; row-gap:5px;
         grid-template-areas:"id pts" "main main" "src cnt";}
  .c-src, .c-cnt {font-size:11.5px; opacity:.7;}
  .nlbl {display:inline;}
  .vdet {padding-left:14px;}
}
/* 컨테이너 쿼리를 모르는 옛 브라우저용: 창 폭 기준 근사 */
@supports not (container-type: inline-size) {
  @media (max-width: 1280px) {
    .vhead, .vrow {grid-template-columns:78px minmax(0,1fr) minmax(0,.6fr) 56px 52px;}
    .vdet {padding-left:14px;}
  }
  @media (max-width: 960px) {
    .vhead {display:none;}
    .vrow {grid-template-columns:minmax(0,1fr) auto; row-gap:5px;
           grid-template-areas:"id pts" "main main" "src cnt";}
    .nlbl {display:inline;}
  }
}
.vitem {border-top:1px solid rgba(128,128,128,.22);
        background:rgba(128,128,128,.055); border-left:3px solid #6B7280;}
.vitem > summary {list-style:none; cursor:pointer;}
.vitem > summary::-webkit-details-marker {display:none;}
.rid {display:inline-block; white-space:nowrap; font-size:11px; font-weight:600;
      padding:3px 8px; border-radius:6px; border:1px solid currentColor;}
.vmain {font-size:13.5px;}
.vsrc {font-size:11.5px; opacity:.7;}
.vnum {font-size:12.5px; text-align:right; opacity:.85; white-space:nowrap;}
.vdet {padding:2px 14px 14px 100px; font-size:12.5px; opacity:.88; line-height:1.75;}
.legend {display:flex; flex-wrap:wrap; gap:6px 16px; font-size:12px; opacity:.8; margin:8px 2px 10px;}
.dot {display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px;}

/* ── 정성 검증 ── */
.aibadge {display:inline-block; font-size:11px; padding:2px 9px; border-radius:6px;
          background:rgba(199,119,0,.14); border:1px solid rgba(199,119,0,.45);}
.qrow {display:flex; gap:10px; align-items:baseline; padding:5px 0; font-size:13px;}
.qrow .rid {flex-shrink:0;}
.qsrc {font-size:11.5px; opacity:.65;}
.qhead {font-size:12.5px; opacity:.75; margin:10px 0 2px;}

/* ── 계산 상세 · 원본 데이터 표: 첫 칸 줄바꿈 금지, 좁으면 가로 스크롤 ── */
.kwrap {overflow-x:auto;}
.ktable {border-collapse:collapse; width:100%; font-size:13px;}
.ktable th, .ktable td {padding:6px 10px; border-bottom:1px solid rgba(128,128,128,.22); text-align:left;}
.ktable th {font-size:11.5px; opacity:.65; font-weight:500;}
.ktable td:first-child, .ktable th:first-child {white-space:nowrap;}
/* 표 칸에서는 anywhere를 끈다: anywhere는 칸의 최소 폭을 글자 한 개까지 줄여 「연│료」처럼 끊는다.
   대신 표가 칸보다 넓으면 .kwrap이 가로 스크롤로 넘긴다 */
.ktable th, .ktable td {overflow-wrap:normal; word-break:keep-all;}
.ktable td.num {text-align:right; white-space:nowrap;}
</style>
""", unsafe_allow_html=True)


# ── 도우미 ──────────────────────────────────────────────────────────
def esc(v):
    return html.escape(str(v))


def dec(v, nd=1):
    if v is None:
        return '—'
    if isinstance(v, (int, Decimal, float)):
        q = Decimal(str(v)).quantize(Decimal(1).scaleb(-nd)) if nd else Decimal(str(v))
        return f'{q:,}'
    return str(v)


def json_ready(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError


def badge(rule_id, color):
    return f'<span class="rid" style="color:{color}">{esc(rule_id)}</span>'


def fail_with(message, detail=''):
    """사람이 읽는 오류 + 접힌 기술 원인. 이후 화면은 그리지 않는다."""
    st.error(message)
    if detail:
        with st.expander('기술적인 원인'):
            st.code(detail, language=None)
    st.stop()


def decode_text(raw, what):
    for enc in CSV_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    fail_with(f'{what}의 글자 인코딩을 읽을 수 없습니다. UTF-8로 다시 저장해 올리세요.',
              f'시도한 인코딩: {", ".join(CSV_ENCODINGS)}')


def kv_table(header, rows, num_cols=()):
    h = ''.join(f'<th>{esc(x)}</th>' for x in header)
    body = ''
    for r in rows:
        body += '<tr>' + ''.join(
            f'<td class="{"num" if i in num_cols else ""}">{esc(x)}</td>' for i, x in enumerate(r)) + '</tr>'
    return f'<div class="kwrap"><table class="ktable"><tr>{h}</tr>{body}</table></div>'


@st.cache_data(show_spinner=False)
def analyse(dataset_dir, flights, saf, docs_text):
    docs = json.loads(docs_text) if docs_text else {}
    calc = run_pipeline(dataset_dir, 'BLOCK_OFF_ON', flights)
    res = verify(dataset_dir, flights_file=flights, saf_file=saf, **docs)
    return calc, res


# ── 사이드바 · 입력 ─────────────────────────────────────────────────
cli_cmd = None
with st.sidebar:
    st.markdown('### 데이터')
    mode = st.radio('입력 방식', ['샘플 데이터', '파일 올리기'], label_visibility='collapsed')

    if mode == '샘플 데이터':
        sample = st.selectbox('샘플', list(SAMPLES))
        flights_file, saf_file, docs_name = SAMPLES[sample]
        dataset_dir = str(DATASET)
        docs_text = (DATASET / docs_name).read_text(encoding='utf-8')
        cli_cmd = 'python -m corsia full --dataset ./dataset'
        if flights_file != 'flights_clean.csv':
            cli_cmd += f' --flights {flights_file} --saf {saf_file}'
        cli_cmd += f' --documents dataset/{docs_name}'
    else:
        up_f = st.file_uploader('비행 기록 CSV', type='csv')
        up_s = st.file_uploader('적격연료 배치 CSV (선택)', type='csv')
        up_d = st.file_uploader('서류 JSON — EMP·ER·EUCR 등 (선택)', type='json')
        st.caption('기단 목록·산정 파라미터·참여국 목록은 샘플(한빛항공)의 것을 씁니다. '
                   '적격연료 CSV를 올리지 않으면 적격연료 감축량은 0으로 계산합니다.')
        if up_f is None:
            st.info('비행 기록 CSV를 올리면 실행됩니다.')
            st.stop()

        raw_f = up_f.getvalue()
        raw_s = up_s.getvalue() if up_s is not None else b''
        raw_d = up_d.getvalue() if up_d is not None else b''
        key = hashlib.sha1(raw_f + b'|' + raw_s + b'|' + raw_d).hexdigest()[:12]
        tmp = Path(tempfile.gettempdir()) / f'corsia_upload_{key}'
        tmp.mkdir(exist_ok=True)
        for p in DATASET.glob('*.*'):
            if p.suffix in ('.csv', '.json') and not p.name.startswith(('flights_', 'saf_batches')):
                shutil.copy(p, tmp / p.name)
        (tmp / 'flights_uploaded.csv').write_bytes(raw_f)
        if up_s is not None:
            (tmp / 'saf_batches.csv').write_bytes(raw_s)
        else:   # 적격연료 없음 → 헤더만 있는 파일 (계산·검증 모두 감축량 0)
            hdr = (DATASET / 'saf_batches.csv').read_text(encoding='utf-8-sig').splitlines()[0]
            (tmp / 'saf_batches.csv').write_text(hdr + '\n', encoding='utf-8')
        flights_file, saf_file, dataset_dir = 'flights_uploaded.csv', 'saf_batches.csv', str(tmp)

        try:
            check_csv(tmp / flights_file, 'flights', '비행 기록 CSV')
            if up_s is not None:
                check_csv(tmp / saf_file, 'saf', '적격연료 배치 CSV')
        except InputError as e:
            fail_with(e.message, e.detail)

        docs_text = None
        if up_d is not None:
            docs_text = decode_text(raw_d, '서류 JSON')
            try:
                json.loads(docs_text)
            except json.JSONDecodeError as e:
                fail_with('서류 JSON의 형식이 올바르지 않습니다. 괄호·쉼표·따옴표를 확인하세요.',
                          f'{e.msg} (줄 {e.lineno}, 칸 {e.colno})')

    st.caption('검증 기준은 ICAO(SARPs 부속서 16 제4권 부록 6)를 적용합니다.')

try:
    calc, res = analyse(dataset_dir, flights_file, saf_file, docs_text)
except InputError as e:
    fail_with(e.message, e.detail)
except KeyError as e:
    col = str(e.args[0]) if e.args else '?'
    fail_with(f'필수 열 {col}이 없습니다. 올린 파일의 열 이름을 확인하세요.',
              f'KeyError: {e!r}')
except ValueError as e:
    if 'OE_y' in str(e):
        fail_with('상쇄 대상 배출량이 0으로 계산되어 더 진행할 수 없습니다. 비행 기록의 등록부호가 '
                  '기단 목록에 있는지, 연료량 열(tank_block_off_t · tank_block_on_t)이 채워져 있는지 확인하세요.',
                  f'ValueError: {e}')
    fail_with('데이터의 값을 읽는 중 문제가 생겼습니다. 숫자 칸에 글자가 들어 있지 않은지 확인하세요.',
              f'ValueError: {e}')
except Exception as e:     # noqa: BLE001
    fail_with('실행 중 예상하지 못한 문제가 생겼습니다. 아래 기술적인 원인을 팀에 알려 주세요.',
              f'{type(e).__name__}: {e}')

c, sc = res['computed'], res['score']
opinion = res['opinion']['result']
findings = res['findings']
fails = sorted([f for f in findings if f['status'] == 'FAIL'],
               key=lambda f: (SEV_ORDER.get(f['severity'], 9), f['rule_id']))
skipped = [f for f in findings if f['status'] == 'SKIPPED']
notices = [f for f in findings if f['status'] == 'NOTICE']

# 제목: 승인된 모니터링 계획(EMP)의 법인명 → 없으면 일반 문구
docs_obj = (json.loads(docs_text) if docs_text else {}).get('documents', {}) or {}
operator = ((docs_obj.get('EMP') or {}).get('operator_name')
            or (docs_obj.get('ER') or {}).get('operator_name') or '올린 데이터')
st.markdown(f"#### {esc(operator)} · {res['meta']['reporting_year']}년 배출량보고서")
st.caption(f"검토 담당자 보기 · {res['meta']['flights_file']} · "
           f"이행기간 {res['meta']['compliance_period']} · 모니터링 {res['meta']['method']}")

# ── 1. 결과 요약 상자 ───────────────────────────────────────────────
op_color = {'만족': '#2E7D52', '의견부 만족': '#C77700', '불만족': '#D64545'}[opinion]
n_crit = sum(1 for f in fails if f['severity'] == '치명')

score_sub = (f"원점수 {dec(sc['raw'])} · {sc['cap_reason']}" if sc['capped']
             else f"원점수 {dec(sc['raw'])} · 상한 {int(sc['cap'])} 미적용")

# 판정 보류 안내 (판정은 바꾸지 않고 안내만)
skip_html = ''
if skipped:
    missing = []
    for f in skipped:
        for m in f.get('missing_inputs') or []:
            if m not in missing:
                missing.append(m)
    hints = []
    if any(from_documents_json(m) for m in missing):
        hints.append('서류 JSON을 함께 올리면 검증됩니다')
    others = [input_label(m) for m in missing if not from_documents_json(m)]
    if others:
        hints.append('필요한 자료: ' + ', '.join(others))
    skip_html = (f'<div class="skipnote">일부 규칙 {len(skipped)}개는 자료가 없어 검증하지 못했습니다'
                 + (f' ({esc(" · ".join(hints))})' if hints else '') + '.</div>')

warn = ''
if opinion == '불만족':
    warn = ('<div class="warnline">검증을 통과하지 못한 데이터로 계산한 값입니다. '
            '보완 후 다시 계산해야 합니다.</div>')

layer_html = ''
for name, v in sc['by_layer'].items():
    if v['score'] is None:
        continue
    pct = float(v['score'])
    color = '#D64545' if pct < 50 else ('#C77700' if pct < 90 else '#2E7D52')
    layer_html += (
        f'<div><div class="lbl">{name} '
        f'<span style="opacity:.75">{v["failed"]}/{v["rules"]} 위반</span></div>'
        f'<div class="bar"><span style="width:{pct}%;background:{color}"></span></div>'
        f'<div style="font-size:19px;font-weight:600;margin-top:5px">{dec(v["score"])}</div></div>')

st.markdown(f"""
<div class="sumbox">
  <div class="sumgrid">
    <div>
      <div class="lbl">최종 판정 <span style="opacity:.75">ICAO 3종</span></div>
      <div class="big" style="color:{op_color}">{opinion}</div>
      <div class="sub">{'치명 위반 ' + str(n_crit) + '건 · ' if n_crit else ''}SARPs App.6 3.10</div>
      {skip_html}
    </div>
    <div>
      <div class="lbl">종합점수 <span style="opacity:.75">보조 지표</span></div>
      <div class="big">{dec(sc['overall'])}<span style="font-size:15px;opacity:.6">/100</span></div>
      <div class="sub">{score_sub}</div>
    </div>
    <div>
      <div class="lbl">최종 상쇄의무량</div>
      <div class="big">{c['FOR_t']:,}<span style="font-size:15px;opacity:.6"> t</span></div>
      <div class="sub">톤 단위 올림 · SARPs 3.4.4</div>
    </div>
  </div>
  {warn}
  <div class="lyr">{layer_html}</div>
  <div class="sub" style="margin-top:10px">계층별 점수는 상한 보정 없음 · 판정 구간(100/89/59)은 팀 자체 보조 기준으로 ICAO 기준이 아닙니다.</div>
</div>
""", unsafe_allow_html=True)

# ── 2. 위반 목록 ────────────────────────────────────────────────────
ded = {d['rule_id']: d for d in sc['deductions']}

st.markdown(f"**위반 목록 {len(fails)}건**")
st.markdown(
    '<div class="legend">'
    + ''.join(f'<span><span class="dot" style="background:{SEV_COLOR[s]}"></span>{s} '
              f'{sum(1 for f in fails if f["severity"] == s)}건</span>'
              for s in ('치명', '중요', '경미'))
    + '<span style="margin-left:auto">줄을 누르면 상세가 열립니다</span></div>',
    unsafe_allow_html=True)

if not fails:
    st.success('위반 없음 — 판정한 규칙을 모두 통과했습니다.')
else:
    rows = ['<div class="vwrap">',
            '<div class="vhead"><div>규칙</div><div>위반 내용</div><div>근거 조항</div>'
            '<div style="text-align:right">건수</div><div style="text-align:right">감점</div></div>']
    for f in fails:
        col = SEV_COLOR.get(f['severity'], GREY)
        d = ded.get(f['rule_id'], {})
        pts = f"−{d['points']}" if d else '—'
        lines = ''.join(f'<div>{esc(format_sample(s))}</div>' for s in (f.get('samples') or []))
        if not lines:
            lines = '<div>상세 예시 없음</div>'
        more = f['count'] - len(f.get('samples') or [])
        if more > 0:
            lines += f'<div style="opacity:.65">외 {more:,}건</div>'
        rows.append(f"""
<details class="vitem" style="border-left-color:{col}">
  <summary>
    <div class="vrow">
      <div class="c-id">{badge(f['rule_id'], col)}</div>
      <div class="c-main vmain">{esc(f['violation'])}</div>
      <div class="c-src vsrc">{esc(f['source'])}</div>
      <div class="c-cnt vnum"><span class="nlbl">건수 </span>{f['count']:,}</div>
      <div class="c-pts vnum" style="color:{col}"><span class="nlbl">감점 </span>{pts}</div>
    </div>
  </summary>
  <div class="vdet">{lines}
    <div style="opacity:.7;margin-top:6px">{esc(d.get('basis', ''))} → {pts}점 · 규칙 제목 「{esc(f['title'])}」</div>
  </div>
</details>""")
    rows.append('</div>')
    st.markdown('\n'.join(rows), unsafe_allow_html=True)

if skipped:
    st.caption('판정 보류 ' + str(len(skipped)) + '건 — '
               + ', '.join(f"{f['rule_id']} ({f['note']})" for f in skipped))
if notices:
    st.caption('안내 ' + ', '.join(f"{f['rule_id']} {f['violation']}" for f in notices))

# ── 3. 정성 검증 (AI 참고용) ────────────────────────────────────────
qual = [f for f in findings if f['layer'] == '정성']
q_checked = [f for f in qual if f['status'] in ('PASS', 'FAIL')]
q_fail = [f for f in qual if f['status'] == 'FAIL']
q_skip = [f for f in qual if f['status'] == 'SKIPPED']

st.markdown('<div style="margin-top:22px"></div>', unsafe_allow_html=True)
st.markdown(f"**정성 검증 {len(qual)}건** &nbsp; "
            f'<span class="aibadge">AI 참고용 · 최종 판정 아님</span>', unsafe_allow_html=True)
# 「」로 묶은 세 선택지는 한 줄에 함께 보이게 둔다 (읽기 쉽게)
st.caption('현재는 서술 작성 여부만 확인합니다. 7단계에서 AI를 연결하면 이 자리에 '
           '<span style="white-space:nowrap">「충분 / 보완 필요 / 판단 불가」</span>와 '
           '이유·근거 조항이 들어갑니다.', unsafe_allow_html=True)


def q_rows(items, color_of):
    return ''.join(
        f'<div class="qrow">{badge(f["rule_id"], color_of(f))}'
        f'<span>{esc(f["violation"] if f["status"] == "FAIL" else f["title"])} '
        f'<span class="qsrc">{esc(f["source"])}</span></span></div>' for f in items)


def skip_reason(items):
    labels = []
    for f in items:
        for m in f.get('missing_inputs') or []:
            lb = input_label(m)
            if lb not in labels:
                labels.append(lb)
    return ', '.join(labels) or '필요한 자료'


q_html = ''
if qual and len(q_skip) == len(qual):
    _r = skip_reason(q_skip)
    q_html += (f'<div class="qhead">{esc(_r)}{josa_i_ga(_r)} 없어 서술 항목 {len(qual)}건을 '
               f'확인하지 못했습니다.</div>')
else:
    if q_checked:
        ok_n = len(q_checked) - len(q_fail)
        q_html += (f'<div class="qhead">확인함 {len(q_checked)}건 — 작성됨 {ok_n}건'
                   + (f' · 비어 있거나 부족 {len(q_fail)}건' if q_fail else '') + '</div>')
        if q_fail:
            q_html += q_rows(q_fail, lambda f: SEV_COLOR.get(f['severity'], GREY))
        elif not q_skip:
            q_html += '<div class="qrow">서술 항목이 모두 작성되어 있습니다.</div>'
    if q_skip:
        q_html += (f'<div class="qhead">확인 못 함 {len(q_skip)}건 — '
                   f'{esc(skip_reason(q_skip))} 없음</div>')
        q_html += q_rows(q_skip, lambda f: GREY)
st.markdown(q_html, unsafe_allow_html=True)

# ── 4·5. 계산 상세 / 원본 데이터 ────────────────────────────────────
st.markdown('<div style="margin-top:14px"></div>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    with st.expander('계산 상세 C06~C12'):
        mat = c['materiality']
        st.markdown(kv_table(['항목', '값'], [
            ['총 CO₂ (C06)', f"{dec(c['total_co2_t'])} t"],
            ['상쇄 대상 배출량', f"{dec(c['OE_y_t'])} t"],
            ['EU-ETS 차감 (X-23)', f"{dec(c['eu_ets_adjusted_t'])} t"],
            ['상쇄의무량 (C08)', f"{dec(c['OR_y_t'], 2)} t"],
            ['개별 성장계수 (C09)', str(c['OGF'])],
            ['적격연료 감축 (C10)', f"{dec(c['ER_y_t'], 2)} t"],
            ['최종 상쇄의무량 (C11)', f"{c['FOR_t']:,} t"],
            ['데이터 갭', f"{c['data_gap_pct']} %"],
            ['중요성 임계', f"{dec(mat['limit_t'])} t ({mat['applied_pct'] * 100:.0f}%)"],
        ], num_cols=(1,)), unsafe_allow_html=True)
with col2:
    with st.expander('원본 데이터 · 노선별 내역'):
        rs = c.get('route_stats') or []
        st.markdown(kv_table(['노선', '연료', '편수', '연료(t)', '편당(t)'], [
            [f"{r['dep']}→{r['arr']}", r['fuel_type'], f"{r['flights']:,}",
             dec(r['total_fuel_t']), dec(r['avg_fuel_per_flight'], 2)] for r in rs
        ], num_cols=(2, 3, 4)), unsafe_allow_html=True)

# ── 내려받기 ────────────────────────────────────────────────────────
st.download_button(
    '결과 내려받기 (JSON)',
    data=json.dumps({'calc': calc, 'verification': res},
                    ensure_ascii=False, indent=2, default=json_ready),
    file_name=f"corsia_result_{res['meta']['reporting_year']}.json",
    mime='application/json')
if cli_cmd:
    st.caption('같은 결과를 명령행에서도 얻을 수 있습니다 (engine 폴더에서):')
    st.code(cli_cmd, language=None)
