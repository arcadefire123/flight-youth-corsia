"""소제목 줄 후보 목록(heading_candidates_v1.xlsx)을 만든다 (8단계, 합의 안건 12용).

  python 8_classifier/tools/make_heading_candidates.py

소제목 줄: 입력 칸이 없는 제목 줄(예: 「d) Blender information」, OoM의 「a6) Data gaps」). 코드가 100% 가려낼 수는 없어서
(입력이 다음 줄에 있는 일반 항목과 구분이 안 됨) 후보를 뽑아 팀이 한 번 표시한다.
후보 조건: 점검표 줄 항목(셀좌표 row N) + 자기 줄에 입력칸(연두색)이 없음 + 원문 설명·세부라벨이 없음 + 「Template Information」 시트가 아님.
팀이 표시한 뒤 소제목으로 확정된 ID를 8_classifier/template_rules_v1.json 의 「소제목_줄」.ids 에 넣고 정책(exclude / blank_input)을 고른다.
"""
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'engine'))
from corsia.classify import catalog as C  # noqa: E402
from corsia.classify import rules as R  # noqa: E402

OUT = ROOT / '8_classifier' / 'heading_candidates_v1.xlsx'
OUT2 = ROOT / '8_classifier' / 'heading_candidates_round2_v1.xlsx'
HEAD = PatternFill('solid', fgColor='D9E1F2')
YELLOW = PatternFill('solid', fgColor='FFF2CC')


def candidates():
    ctx = C.context()
    cells = C.template_cells()
    items = ctx['items']
    out = []
    for it in items:
        c = cells.get(it['ID'], {})
        if not str(it.get('셀좌표') or '').startswith('row') or c.get('own_row_input') is not False:
            continue
        if it.get('원문 설명') or it.get('세부라벨') or it.get('시트') == 'Template Information':
            continue
        no = str(it.get('항목번호') or '').strip()
        m = re.fullmatch(r'([a-z])(\d*)\)', no)
        children = bool(m) and any(re.fullmatch(m.group(1) + m.group(2) + r'\d+\)', str(x.get('항목번호') or '').strip())
                                   and x['템플릿'] == it['템플릿'] and x['시트'] == it['시트'] for x in items)
        nxt = next((x for x in items[ctx['pos'][it['ID']] + 1:] if x['템플릿'] == it['템플릿'] and x['시트'] == it['시트']), None)
        out.append({'it': it, 'children': children, 'next': nxt})
    return out


def candidates_round2():
    """2차 후보(2026-11-16): 1차는 「원문 설명·세부라벨이 없는 줄」만 봐서 F0009처럼 원문 설명이 있는 제목 줄이 빠졌다.
    2차는 원문 설명이 있는 줄 항목 중 자기 줄에 입력칸이 없고, 아래에 따로 있는 입력 항목이 3개 이상(표의 열 항목)이거나
    번호 하위 항목(a)→a1))이 있는 것. 이미 소제목으로 확정했거나 소제목이 아니라고 정한 항목은 뺀다."""
    ctx = C.context()
    cells = C.template_cells()
    items = ctx['items']
    done = set((C.template_rules().get('소제목_줄') or {}).get('ids') or []) | set('F0061 F0227 F0256 F0278 F0291 F0302 F0674 F0675'.split())

    def first_row(c):
        m = re.search(r'(\d+)', str(c or ''))
        return int(m.group(1)) if m else None
    rows = {}
    for it in items:
        r = first_row(it.get('셀좌표'))
        if r:
            rows.setdefault((it['템플릿'], it['시트']), []).append((r, it))
    out = []
    for it in items:
        c = cells.get(it['ID'], {})
        if not str(it.get('셀좌표') or '').startswith('row') or c.get('own_row_input') is not False or it['ID'] in done:
            continue
        if not (it.get('원문 설명') or it.get('세부라벨')) or it.get('시트') == 'Template Information':
            continue
        r = first_row(it['셀좌표'])
        later = sorted({x for x, j in rows[(it['템플릿'], it['시트'])] if x > r and str(j['셀좌표']).startswith('row')})
        end = min((later[0] - 1) if later else r + 25, r + 25)
        below = [j for x, j in rows[(it['템플릿'], it['시트'])] if r <= x <= end and j['ID'] != it['ID'] and not str(j['셀좌표']).startswith('row')]
        no = str(it.get('항목번호') or '').strip()
        m = re.fullmatch(r'([a-z])(\d*)\)', no)
        numbered = bool(m) and any(re.fullmatch(m.group(1) + m.group(2) + r'\d+\)', str(x.get('항목번호') or '').strip())
                                   and x['템플릿'] == it['템플릿'] and x['시트'] == it['시트'] for x in items)
        if len(below) >= 3 or numbered:
            out.append({'it': it, 'below': below, 'numbered': numbered})
    return sorted(out, key=lambda x: (-len(x['below']), x['it']['ID']))


def main_round2():
    cand = candidates_round2()
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = '0_안내'
    for k, v in [('무엇', '1차 후보 목록은 「원문 설명이 없는 줄」만 봐서 F0009처럼 원문 설명이 있는 제목 줄이 빠졌습니다. 2차 후보는 원문 설명이 있는 줄 중 자기 줄에 입력칸이 없고 아래에 입력 항목이 3개 이상(표의 열 항목)이거나 번호 하위 항목이 있는 것입니다.'),
                 ('주의', '이 기준만으로는 소제목을 가릴 수 없습니다. 팀 기준 「아래 입력 칸이 카탈로그에 따로 항목으로 있으면 소제목」을 입력 항목이 1개인 줄까지 그대로 적용하면 원문 설명이 있는 줄 약 140개가 걸려 사실상 모든 입력 문항이 소제목이 됩니다(F0009처럼 입력이 1개인 줄 포함). 그래서 2차는 입력이 여러 개인 줄만 모았습니다. 소제목인지 팀이 정해 주세요.'),
                 ('반영', '소제목으로 확정된 ID를 template_rules_v1.json 의 「소제목_줄」.ids 에 더하고 python classify_items.py --refresh-rules <결과 폴더>')]:
        ws0.append([k, v])
    ws0.column_dimensions['A'].width = 14
    ws0.column_dimensions['B'].width = 120
    for r in ws0.iter_rows():
        r[0].font = Font(bold=True)
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    ws = wb.create_sheet('2차 후보')
    heads = ['ID', '템플릿', '시트', '항목번호', '항목명', '아래 입력 항목 수', '번호 하위 항목(a)→a1)) 있음', '아래 입력 항목(앞 3개)', '소제목 여부(고름)', '메모']
    ws.append(heads)
    for c in range(1, len(heads) + 1):
        ws.cell(row=1, column=c).font = Font(bold=True)
        ws.cell(row=1, column=c).fill = HEAD
    for x in cand:
        it = x['it']
        ws.append([it['ID'], it['템플릿'], it['시트'], it.get('항목번호') or '', str(it.get('항목명') or '')[:80], len(x['below']), '예' if x['numbered'] else '아니오',
                   ' / '.join(f"{j['ID']} {str(j.get('항목명') or j.get('세부라벨') or '')[:26]}" for j in x['below'][:3]), '', ''])
        ws.cell(row=ws.max_row, column=9).fill = YELLOW
    for col, w in zip('ABCDEFGHIJ', (8, 9, 24, 9, 44, 10, 14, 70, 16, 24)):
        ws.column_dimensions[col].width = w
    dv = DataValidation(type='list', formula1='"소제목,일반 항목,판단 불가"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'I2:I{max(ws.max_row, 2)}')
    ws.freeze_panes = 'E2'
    wb.save(OUT2)
    print(f'2차 후보 {len(cand)}개 저장: {OUT2}')
    print('  템플릿별: ' + ', '.join(f'{t} {sum(1 for x in cand if x["it"]["템플릿"] == t)}' for t in ('1.1 EMP', '1.2 ER', '1.3 CEF', '1.4 OoM', '1.5 EUCR', '1.6 VR')))


def main():
    if '--round2' in sys.argv:
        return main_round2()
    cand = candidates()
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = '0_안내'
    for k, v in [('무엇', '입력 칸이 없는 「소제목 줄」 후보입니다. 팀이 후보마다 「소제목 / 일반 항목 / 판단 불가」를 고릅니다(합의 안건 12).'),
                 ('후보 조건', '점검표 줄 항목 + 자기 줄에 입력칸(연두색)이 없음 + 원문 설명·세부라벨 없음 + Template Information 시트 제외. 코드로 100% 가릴 수 없어(입력이 다음 줄에 있는 일반 항목도 걸림) 사람이 확인합니다.'),
                 ('보는 법', '「다음 항목」 열의 항목이 이 줄의 하위 입력 칸이면 소제목, 이 줄의 입력이 다음 줄에 있고 다음 항목이 별개의 질문이면 일반 항목입니다. ICAO 서식의 셀좌표(F열)를 열어 확인하세요.'),
                 ('정책(합의 안건 12)', '① 소제목 줄은 속성을 모두 비우고 「제외 권장」으로 표시(Claude 제안, Template Information·목차 줄과 같은 처리) ② F0495 선례대로 입력유형만 비움 ③ 그대로 채움'),
                 ('반영', '소제목으로 확정된 ID를 template_rules_v1.json 의 「소제목_줄」.ids 에 넣고 「정책」을 exclude 또는 blank_input 으로 바꾼 뒤 python classify_items.py --refresh-rules <결과 폴더> (비용 0)')]:
        ws0.append([k, v])
    ws0.column_dimensions['A'].width = 18
    ws0.column_dimensions['B'].width = 120
    for r in ws0.iter_rows():
        r[0].font = Font(bold=True)
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    ws = wb.create_sheet('후보')
    heads = ['ID', '템플릿', '시트', '항목번호', '항목명', '셀좌표', '하위 항목(a)→a1)) 있음', '다음 항목', '현재 규칙 입력유형', '소제목 여부(고름)', '메모']
    ws.append(heads)
    for c in range(1, len(heads) + 1):
        ws.cell(row=1, column=c).font = Font(bold=True)
        ws.cell(row=1, column=c).fill = HEAD
    for x in cand:
        it, nxt = x['it'], x['next']
        ws.append([it['ID'], it['템플릿'], it['시트'], it.get('항목번호') or '', str(it.get('항목명') or '')[:90], it['셀좌표'], '예' if x['children'] else '아니오',
                   (f"{nxt['ID']} {str(nxt.get('항목번호') or '')} {str(nxt.get('항목명') or '')[:50]}" if nxt else ''), R._input_type(it)[0], '', ''])
        ws.cell(row=ws.max_row, column=10).fill = YELLOW
    for col, w in zip('ABCDEFGHIJK', (8, 9, 24, 9, 44, 9, 14, 50, 12, 16, 24)):
        ws.column_dimensions[col].width = w
    dv = DataValidation(type='list', formula1='"소제목,일반 항목,판단 불가"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'J2:J{max(ws.max_row, 2)}')
    ws.freeze_panes = 'E2'
    wb.save(OUT)
    print(f'후보 {len(cand)}개 저장: {OUT}')
    print('  템플릿별: ' + ', '.join(f'{t} {sum(1 for x in cand if x["it"]["템플릿"] == t)}' for t in ('1.1 EMP', '1.2 ER', '1.3 CEF', '1.4 OoM', '1.5 EUCR', '1.6 VR')))


if __name__ == '__main__':
    main()
