"""
결과 내려받기 — JSON과 엑셀

  result_payload(calc, res, qual=None)  JSON 내려받기에 넣을 구조
  to_json_bytes(payload)                JSON 바이트 (한글 그대로, Decimal은 숫자로)
  build_workbook(calc, res, qual=None, operator=..., data_note=...)   엑셀(.xlsx) 바이트

엑셀 시트: 요약 / 위반 목록 / 점수 근거 / 계산 상세 / 정성 검증(AI를 실행했을 때만)

원칙
  - 엑셀과 JSON은 같은 결과 객체(calc, res)에서 만든다. 값이 서로 다를 수 없다.
  - 엑셀의 정성 검증 시트는 AI 참고 의견이다. 요약 시트의 판정·종합점수·상쇄의무량에는 영향을 주지 않는다.
  - 글자 칸은 전부 「문자열」로 저장한다. 올린 파일의 값(법인명·항공편 번호 등)이 =, +, -, @로 시작해도
    엑셀이 수식으로 실행하지 않는다(openpyxl은 =로 시작하는 글자를 수식으로 저장하므로 일부러 막는다).
  - 파일에 PC 전체 경로(사용자 이름)를 넣지 않는다.
  - 수식·병합 칸·데이터 검증 같은 기능은 쓰지 않는다(엑셀의 「복구」 메시지 방지).

openpyxl이 필요하다(아나콘다에 기본 포함). 없으면 build_workbook이 ImportError를 내고 화면은 안내 문구로 대신한다(JSON은 그대로 동작).
"""
import io
import json
from datetime import datetime
from decimal import Decimal

try:                                   # openpyxl이 없어도 JSON 내려받기는 동작해야 하므로 모듈 불러오기는 막지 않는다
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    HAVE_OPENPYXL = True
except ImportError:                    # pragma: no cover
    HAVE_OPENPYXL = False

from . import __version__
from .verify.display import format_sample, skip_summary

SHEETS = ('요약', '위반 목록', '점수 근거', '계산 상세', '정성 검증')
FONT_NAME = 'Malgun Gothic'
MAX_TEXT = 32000                      # 엑셀 한 칸 최대 32,767자
SEV_RGB = {'치명': 'D64545', '중요': 'C77700', '경미': '6B7280', '정보': '6B7280'}
SEV_ORDER = {'치명': 0, '차단': 0, '중요': 1, '경미': 2, '정보': 3}
VERDICT_RGB = {'충분': '2E7D52', '보완 필요': 'C77700', '판단 불가': '6B7280'}

if HAVE_OPENPYXL:
    _THIN = Side(style='thin', color='D1D5DB')
    _BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
    _HEAD_FILL = PatternFill('solid', fgColor='F3F4F6')
    _NOTE_FILL = PatternFill('solid', fgColor='FEF3C7')
    _AI_FILL = PatternFill('solid', fgColor='FDF2E3')


# ───────────────────────── JSON ─────────────────────────
def result_payload(calc, res, qual=None):
    """JSON 내려받기 구조. AI 정성 검증을 실행하지 않았으면 qualitative 키를 넣지 않는다."""
    payload = {'calc': calc, 'verification': res}
    if qual:
        payload['qualitative'] = qual
    return payload


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f'JSON으로 만들 수 없는 값: {type(o).__name__}')


def to_json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode('utf-8')


# ───────────────────────── 칸 쓰기 ─────────────────────────
def _clean(s):
    s = ILLEGAL_CHARACTERS_RE.sub('', str(s))
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + '…'


def _is_number(v):
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _put(ws, row, col, value, *, fmt=None, bold=False, color=None, fill=None, size=None,
         align='left', wrap=True, border=True, italic=False, keep_left=False):
    """칸 하나를 쓴다. 숫자는 숫자로, 나머지는 반드시 문자열로(수식 주입 방지)."""
    cell = ws.cell(row=row, column=col)
    if value is None:
        cell.value = None
    elif isinstance(value, bool):
        cell.value = '예' if value else '아니오'
        cell.data_type = 's'
    elif _is_number(value):
        f = float(value)
        if f != f or f in (float('inf'), float('-inf')):
            cell.value, cell.data_type = '—', 's'
        else:
            cell.value = int(f) if (f == int(f) and abs(f) < 1e15 and fmt in ('#,##0', '0')) else f
            if fmt:
                cell.number_format = fmt
            align = 'right' if (align == 'left' and not keep_left) else align
    else:
        cell.value = _clean(value)
        cell.data_type = 's'                      # =, +, -, @로 시작해도 수식으로 읽히지 않게
    cell.font = Font(name=FONT_NAME, bold=bold, italic=italic, color=color, size=size or 10)
    cell.alignment = Alignment(horizontal=align, vertical='top', wrap_text=wrap)
    if fill:
        cell.fill = fill
    if border:
        cell.border = _BORDER
    return cell


def _header(ws, row, labels, start_col=1):
    for i, lb in enumerate(labels):
        _put(ws, row, start_col + i, lb, bold=True, fill=_HEAD_FILL)


def _title(ws, row, text, size=13):
    _put(ws, row, 1, text, bold=True, size=size, border=False, wrap=False)


def _widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _minus_points(v):
    return v if _is_number(v) else None


# ───────────────────────── 시트 ─────────────────────────
def _fails_sorted(res):
    return sorted([f for f in res['findings'] if f['status'] == 'FAIL'],
                  key=lambda f: (SEV_ORDER.get(f['severity'], 9), f['rule_id']))


def _sheet_summary(ws, calc, res, qual, operator, data_note, created):
    c, sc, meta = res['computed'], res['score'], res['meta']
    fails = _fails_sorted(res)
    skipped = [f for f in res['findings'] if f['status'] == 'SKIPPED']
    opinion = res['opinion']['result']
    sev_count = {s: sum(1 for f in fails if f['severity'] == s) for s in ('치명', '중요', '경미')}

    _title(ws, 1, 'CORSIA 검증 결과 요약', size=14)
    _put(ws, 2, 1, f'{operator or "올린 데이터"} · {meta["reporting_year"]}년 배출량보고서',
         border=False, wrap=False, color='4B5563')

    if sc['capped']:
        score_note = f'원점수 {sc["raw"]} · {sc["cap_reason"]}'
    else:
        score_note = f'원점수 {sc["raw"]} · 상한 {int(sc["cap"])} 미적용'
    score_note += ' · 판정 구간(100/89/59)은 팀 자체 보조 기준이며 ICAO 기준이 아닙니다'

    rows = [
        ('데이터 구분', data_note or '', None),
        ('법인명', operator or '올린 데이터', None),
        ('보고연도', meta['reporting_year'], '0'),
        ('이행기간', meta['compliance_period'], None),
        ('모니터링 방법', meta['method'], None),
        ('비행 기록 파일', meta['flights_file'], None),
        (None, None, None),
        ('최종 판정 (ICAO 3종)', opinion, None),
        ('종합점수 (보조 지표)', sc['overall'], '0.0'),
        ('종합점수 설명', score_note, None),
        ('최종 상쇄의무량 (t)', c['FOR_t'], '#,##0'),
    ]
    if opinion == '불만족':
        rows.append(('상쇄의무량 안내', '검증을 통과하지 못한 데이터로 계산한 값입니다. 보완 후 다시 계산해야 합니다.', None))
    rows += [
        ('위반 건수 (규칙 수)', len(fails), '0'),
        ('위반 심각도', ' · '.join(f'{k} {v}' for k, v in sev_count.items()), None),
        ('판정 보류 규칙 수', len(skipped), '0'),
    ]
    note = skip_summary(skipped)
    if note:
        rows.append(('판정 보류 안내', note, None))
    rows += [
        (None, None, None),
        ('AI 정성 검증', ('실행함 (참고용 · 최종 판정 아님) — 「정성 검증」 시트 참고'
                        if qual and qual.get('status') in ('ok', 'partial') else '실행하지 않음'), None),
        ('생성 일시', created.strftime('%Y-%m-%d %H:%M'), None),
        ('엔진 버전', __version__, None),
    ]
    r = 4
    for label, value, fmt in rows:
        if label is None:
            r += 1
            continue
        is_main = label in ('최종 판정 (ICAO 3종)', '종합점수 (보조 지표)', '최종 상쇄의무량 (t)')
        _put(ws, r, 1, label, bold=True, fill=_HEAD_FILL)
        fill = _NOTE_FILL if label in ('데이터 구분', '상쇄의무량 안내') else None
        _put(ws, r, 2, value, fmt=fmt, bold=is_main, fill=fill, align='left', size=11 if is_main else None,
             keep_left=True)
        r += 1
    _widths(ws, [26, 78])


def _sheet_violations(ws, res):
    ded = {d['rule_id']: d for d in res['score']['deductions']}
    _title(ws, 1, f'위반 목록 {len(_fails_sorted(res))}건 (심각도 순)')
    _header(ws, 3, ['번호', '규칙', '심각도', '위반 내용', '근거 조항', '건수', '감점(점)', '상세'])
    r = 4
    fails = _fails_sorted(res)
    if not fails:
        _put(ws, r, 1, '위반 없음 — 판정한 규칙을 모두 통과했습니다.', border=False, wrap=False)
    for i, f in enumerate(fails, start=1):
        d = ded.get(f['rule_id'])
        samples = f.get('samples') or []
        lines = [format_sample(s) for s in samples]
        more = f['count'] - len(samples)
        if more > 0:
            lines.append(f'외 {more:,}건')
        _put(ws, r, 1, i, fmt='0', align='center')
        _put(ws, r, 2, f['rule_id'], bold=True, color=SEV_RGB.get(f['severity']), align='center')
        _put(ws, r, 3, f['severity'], color=SEV_RGB.get(f['severity']), bold=True, align='center')
        _put(ws, r, 4, f['violation'])
        _put(ws, r, 5, f['source'])
        _put(ws, r, 6, f['count'], fmt='#,##0')
        _put(ws, r, 7, _minus_points(d['points']) if d else None, fmt='"−"0.0', color=SEV_RGB.get(f['severity']))
        _put(ws, r, 8, '\n'.join(lines))
        r += 1
    _widths(ws, [7, 9, 9, 46, 34, 9, 11, 70])
    if fails:
        ws.freeze_panes = 'A4'
        ws.auto_filter.ref = f'A3:H{r - 1}'


def _sheet_score(ws, res):
    sc = res['score']
    skipped = [f for f in res['findings'] if f['status'] == 'SKIPPED']
    w = sc['weights']
    _title(ws, 1, '점수 근거')
    _put(ws, 2, 1, '종합점수는 보조 지표이며 판정(ICAO 3종)을 바꾸지 않습니다. 상한 구간은 팀 자체 보조 기준입니다.',
         border=False, wrap=False, color='4B5563')

    r = 4
    _title(ws, r, '1. 산출 방식', size=11)
    r += 1
    block = [
        ('방식', sc['method'], None),
        ('배점', ' · '.join(f'{k} {int(v)}' for k, v in w.items()), None),
        ('총 배점 (판정 보류 규칙 제외)', sc['weight_total'], '0'),
        ('잃은 배점', sc['weight_lost'], '0'),
        ('원점수', sc['raw'], '0.0'),
        ('최종 판정', res['opinion']['result'], None),
        ('판정별 상한', sc['cap'], '0'),
        ('상한 적용', bool(sc['capped']), None),
        ('전체 종합점수', sc['overall'], '0.0'),
        ('상한 사유', sc['cap_reason'] or '', None),
        ('상한 기준', sc['cap_basis'], None),
        ('판정 보류로 제외한 규칙 수', len(skipped), '0'),
        ('위반 건수는 점수에 반영하지 않음', '규칙 단위로 1회만 계산 (건수는 위반 목록에만 표시)', None),
    ]
    for label, value, fmt in block:
        _put(ws, r, 1, label, bold=True, fill=_HEAD_FILL)
        _put(ws, r, 2, value, fmt=fmt, keep_left=True)
        r += 1

    r += 1
    _title(ws, r, '2. 계층별 점수 (상한 보정 없음)', size=11)
    r += 1
    _header(ws, r, ['계층', '산식', '규칙 수', '위반 수', '보류 수', '총 배점', '잃은 배점', '점수'])
    r += 1
    for name, v in sc['by_layer'].items():
        _put(ws, r, 1, name, bold=True)
        _put(ws, r, 2, v['formula'])
        _put(ws, r, 3, v['rules'], fmt='0')
        _put(ws, r, 4, v['failed'], fmt='0')
        _put(ws, r, 5, v['skipped'], fmt='0')
        _put(ws, r, 6, v['weight_total'], fmt='0')
        _put(ws, r, 7, v['weight_lost'], fmt='0')
        _put(ws, r, 8, v['score'], fmt='0.0', bold=True)
        r += 1

    r += 1
    _title(ws, r, '3. 규칙별 감점', size=11)
    r += 1
    _header(ws, r, ['규칙', '산출 근거', '계층', '심각도', '배점', '감점(점)'])
    r += 1
    if not sc['deductions']:
        _put(ws, r, 1, '감점 없음', border=False, wrap=False)
    for d in sc['deductions']:
        _put(ws, r, 1, d['rule_id'], bold=True, color=SEV_RGB.get(d['severity']))
        _put(ws, r, 2, d['basis'])
        _put(ws, r, 3, d['layer'])
        _put(ws, r, 4, d['severity'], color=SEV_RGB.get(d['severity']), bold=True)
        _put(ws, r, 5, d['weight'], fmt='0')
        _put(ws, r, 6, d['points'], fmt='"−"0.0', color=SEV_RGB.get(d['severity']))
        r += 1
    _widths(ws, [34, 46, 10, 10, 10, 12, 12, 10])


def calc_detail_rows(res):
    """엑셀 「계산 상세」 행: (항목, 값, 단위, 서식). 값은 JSON의 verification.computed와 같은 값이다."""
    c = res['computed']
    mat = c['materiality']
    return [
        ('총 CO₂ (C06)', c['total_co2_t'], 't', '#,##0.0'),
        ('상쇄 대상 배출량', c['OE_y_t'], 't', '#,##0.0'),
        ('EU-ETS 차감 (X-23)', c['eu_ets_adjusted_t'], 't', '#,##0.0'),
        ('상쇄의무량 (C08)', c['OR_y_t'], 't', '#,##0.00'),
        ('개별 성장계수 (C09)', c['OGF'], '', '0.0000'),
        ('적격연료 감축 (C10)', c['ER_y_t'], 't', '#,##0.00'),
        ('최종 상쇄의무량 (C11)', c['FOR_t'], 't', '#,##0'),
        ('데이터 갭', c['data_gap_pct'], '%', '0.0'),
        ('중요성 임계', mat['limit_t'], 't', '#,##0.0'),
        ('중요성 적용률', Decimal(str(mat['applied_pct'])) * 100, '%', '0'),
    ]


def _sheet_calc(ws, res):
    _title(ws, 1, '계산 상세 (C06~C12)')
    _header(ws, 3, ['항목', '값', '단위'])
    r = 4
    for label, value, unit, fmt in calc_detail_rows(res):
        _put(ws, r, 1, label, bold=True, fill=_HEAD_FILL)
        _put(ws, r, 2, value, fmt=fmt)
        _put(ws, r, 3, unit, align='center')
        r += 1
    _put(ws, r + 1, 1, f'기준: {res["computed"]["materiality"]["standard"]} · 최종 상쇄의무량은 톤 단위 올림(SARPs 3.4.4)',
         border=False, wrap=False, color='4B5563')
    _widths(ws, [30, 20, 8])


def _sheet_qual(ws, qual, rows):
    st = qual.get('settings') or {}
    cost = qual.get('cost') or {}
    _title(ws, 1, '정성 검증 — AI 참고 의견')
    _put(ws, 2, 1, qual.get('badge') or 'AI 참고용 · 최종 판정 아님', bold=True, fill=_AI_FILL, border=False, wrap=False)
    disc = [x.strip() for x in (qual.get('disclaimer') or '').replace('. ', '.\n').split('\n') if x.strip()]
    info = disc + [
        f'상태 {qual.get("status")}',
        f'모델 {st.get("model", "")} · {st.get("thinking", "")} · {st.get("effort", "")} · 기준표 {st.get("rubric_version", "")}',
        f'호출 {cost.get("n_calls", 0)}회 · 기억에서 {cost.get("n_cached", 0)}회 · '
        f'실제 비용 ${float(cost.get("actual_usd") or 0):.4f}',
        f'실행 시각 {qual.get("created", "")}',
    ]
    if qual.get('status') == 'partial':
        info.insert(1, '일부 항목은 AI 판정을 받지 못했습니다 (항목별 판정 칸 참고)')
    for i, line in enumerate(info):
        _put(ws, 3 + i, 1, line, border=False, wrap=False, color='4B5563')
    hr = 3 + len(info) + 1
    cols = list(rows[0].keys()) if rows else ['규칙', '서류', '항목', 'AI 참고 판정', '이유', '근거 조항', '보완할 점',
                                              '확인 항목별 상태', '인용 문제', '캐시']
    _header(ws, hr, cols)
    for i, row in enumerate(rows, start=hr + 1):
        for j, name in enumerate(cols, start=1):
            v = row.get(name, '')
            verdict_color = VERDICT_RGB.get(v) if name == 'AI 참고 판정' else None
            _put(ws, i, j, v, bold=bool(verdict_color), color=verdict_color,
                 align='center' if name in ('규칙', '캐시', '인용 문제') else 'left')
    width_of = {'규칙': 8, '서류': 9, '항목': 22, 'AI 참고 판정': 14, '이유': 60, '근거 조항': 22,
                '보완할 점': 48, '확인 항목별 상태': 34, '인용 문제': 10, '캐시': 7}
    _widths(ws, [width_of.get(n, 18) for n in cols])
    if rows:
        ws.freeze_panes = ws.cell(row=hr + 1, column=1)


def _qual_rows(qual):
    try:
        from .ai import web_qual
        return web_qual.excel_rows(qual)
    except Exception:                                  # AI 모듈이 없어도 엑셀은 만든다 (정성 검증 시트만 빠짐)
        return None


# ───────────────────────── 엑셀 만들기 ─────────────────────────
def build_workbook(calc, res, qual=None, *, operator='', data_note='', created=None):
    """결과 엑셀(.xlsx) 바이트를 돌려준다.

    calc, res : run_pipeline / verify 결과 (JSON 내려받기와 같은 객체)
    qual      : AI 정성 검증 결과(qualitative_result_v1). 실행하지 않았으면 None → 「정성 검증」 시트를 만들지 않는다
    operator  : 법인명 (화면 제목과 같은 값)
    data_note : 「가상 데이터」 등 데이터 구분 표기 (요약 시트에 들어간다)
    """
    if not HAVE_OPENPYXL:
        raise ImportError('엑셀 내려받기에는 openpyxl이 필요합니다 (pip install openpyxl)')
    created = created or datetime.now()
    wb = Workbook()
    ws = wb.active
    ws.title = SHEETS[0]
    _sheet_summary(ws, calc, res, qual, operator, data_note, created)
    _sheet_violations(wb.create_sheet(SHEETS[1]), res)
    _sheet_score(wb.create_sheet(SHEETS[2]), res)
    _sheet_calc(wb.create_sheet(SHEETS[3]), res)
    if qual and qual.get('status') in ('ok', 'partial'):
        rows = _qual_rows(qual)
        if rows is not None:
            _sheet_qual(wb.create_sheet(SHEETS[4]), qual, rows)
    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.page_setup.orientation = 'landscape'          # 인쇄: 가로 방향, 한 페이지 너비에 맞춤
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    wb.properties.creator = 'CORSIA tool'
    wb.properties.title = 'CORSIA result'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
