"""
ICAO 원본 엑셀(템플릿 6종)에서 항목마다 「칸의 실제 모양」을 읽어 template_cells_v1.json으로 저장한다 (8단계).

판정 기준 3: 입력유형은 ICAO 템플릿 원본 칸의 실제 모양으로 정한다 — 선택 목록이 있으면 선택, 수식으로
자동 계산되면 자동계산, 숫자를 직접 넣으면 수치. 이 모양은 AI가 아니라 코드가 읽는다.

쓰는 법 (엔진 폴더가 아니라 프로젝트 폴더에서):
    python 8_classifier/tools/extract_template_cells.py <ICAO 템플릿 엑셀이 있는 폴더>
결과 파일은 8_classifier/template_cells_v1.json 에 저장되고 분류기는 이 파일만 읽는다
(그래서 PC에 원본 템플릿이 없어도 분류기는 돈다).
- 선택 목록: 원본 엑셀의 XML에서 **일반 데이터 검증과 확장(x14) 데이터 검증을 모두** 직접 읽는다.
  (확장 검증은 openpyxl이 못 읽고, 다른 시트의 목록을 가리키는 선택 목록이 대개 이 형태다. v1은 이것을 놓쳐 OoM 점검표의
  Status 열 선택 목록을 못 찾았다 → v2에서 고침.) 카탈로그의 「3_드롭다운_원본」 시트는 더 이상 쓰지 않는다
- 수식: 칸 값이 '='로 시작하는지
- 수치 힌트: 데이터 검증이 숫자(decimal·whole)인지, 표시 형식이 숫자 형식인지
- 칸 크기: 병합 셀의 너비·높이 (큰 칸은 서술 힌트)
- 'row N' 항목(점검표 문항)은 **자기 줄**의 입력칸(연두색 CCFFCC)을 먼저 읽고(own_row_input), 없으면 다음 문항 줄 앞까지 아래 줄에서 찾는다.
  자기 줄 입력칸이 있는 줄 항목은 그 칸 중 하나라도 선택 목록이면 선택(예: OoM의 Status 열), 표 열 범위는 절반 이상일 때만
"""
import glob
import json
import re
import sys
import warnings
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries

warnings.filterwarnings('ignore')
ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / '1_document_map' / 'field_master.xlsx'
OUT = ROOT / '8_classifier' / 'template_cells_v1.json'
TEMPLATE_FILE_KEY = {'1.1 EMP': '1_1-EMP', '1.2 ER': '1_2-ER', '1.3 CEF': '1_3-CEF',
                     '1.4 OoM': '1_4-OoM', '1.5 EUCR': '1_5-EUCR', '1.6 VR': '1_6-VR'}
MAX_CELLS = 80


def cells_of(coord):
    """'C8' / 'C134:C150' -> 칸 이름 목록(최대 MAX_CELLS)"""
    c1, r1, c2, r2 = range_boundaries(coord)
    out = []
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            out.append(f'{get_column_letter(c)}{r}')
            if len(out) >= MAX_CELLS:
                return out
    return out


def overlaps(cells, ranges_text, min_fraction=0.5):
    """항목 칸의 절반 이상이 그 범위 안에 있을 때만 겹친다고 본다
    (표 열 범위 한가운데 선택 칸 하나가 끼어 있어도 그 열 전체가 선택 칸은 아니다)."""
    cs = set(cells)
    hit = set()
    for tok in re.split(r'\s+', str(ranges_text or '').strip()):
        if not tok:
            continue
        try:
            hit |= cs & set(cells_of(tok))
        except Exception:
            continue
    return bool(cs) and len(hit) / len(cs) >= min_fraction


def read_validations(path):
    """원본 엑셀 XML에서 목록 데이터 검증을 모두 읽는다(일반 + 확장 x14).
    반환: {시트 이름: [(칸 범위 문자열, 목록 설명)]}. 목록이 다른 시트 칸을 가리키면 그 칸의 글을 읽어 옵션으로 적는다."""
    z = zipfile.ZipFile(path)
    wbx = z.read('xl/workbook.xml').decode('utf-8', 'ignore')
    rels = z.read('xl/_rels/workbook.xml.rels').decode('utf-8', 'ignore')
    rid_target = dict(re.findall(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels))
    rid_target.update({a: b for b, a in re.findall(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    sheets = {}
    for name, rid in re.findall(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', wbx):
        t = rid_target.get(rid, '')
        sheets[name] = 'xl/' + t.lstrip('/').replace('xl/', '')
    wbv = openpyxl.load_workbook(path, data_only=True)
    out = {}
    for name, part in sheets.items():
        xml = z.read(part).decode('utf-8', 'ignore')
        found = []
        for m in re.finditer(r'<(?:x14:)?dataValidation\b([^>]*)>(.*?)</(?:x14:)?dataValidation>', xml, re.S):
            attrs, body = m.group(1), m.group(2)
            if 'type="list"' not in attrs:
                continue
            f1 = re.search(r'<(?:x14:)?formula1>\s*(?:<xm:f>)?(.*?)(?:</xm:f>)?\s*</(?:x14:)?formula1>', body, re.S)
            sq = re.search(r'<(?:xm:)?sqref>(.*?)</(?:xm:)?sqref>', body, re.S) or re.search(r'sqref="([^"]+)"', attrs)
            if not (f1 and sq):
                continue
            formula = re.sub(r'<[^>]+>', '', f1.group(1)).strip()
            opts = formula
            ref = re.match(r"^'?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$", formula)
            if ref and ref.group(1) in wbv.sheetnames:
                w2 = wbv[ref.group(1)]
                rng = f'{ref.group(2)}{ref.group(3)}' + (f':{ref.group(4)}{ref.group(5)}' if ref.group(4) else '')
                vals = [str(c.value) for row in w2[rng] for c in (row if isinstance(row, tuple) else (row,)) if c.value not in (None, '')]
                opts = ', '.join(vals)
            found.append((sq.group(1).strip(), opts))
        out[name] = found
    return out


def is_input_fill(cell):
    f = cell.fill
    rgb = getattr(getattr(f, 'fgColor', None), 'rgb', None)
    return isinstance(rgb, str) and rgb.upper().endswith('CCFFCC')


def main(folder):
    wbm = openpyxl.load_workbook(MASTER, data_only=True)
    rows = list(wbm['1_field_master'].iter_rows(values_only=True))
    H = list(rows[0])
    items = [dict(zip(H, r)) for r in rows[1:] if r[0]]
    dd = {}
    books = {}
    for t, key in TEMPLATE_FILE_KEY.items():
        hits = glob.glob(str(Path(folder) / f'*{key}*.xlsx'))
        if not hits:
            raise SystemExit(f'[멈춤] {t} 템플릿 엑셀을 {folder} 에서 찾지 못했습니다.')
        books[t] = openpyxl.load_workbook(hits[0])        # 수식을 보려고 data_only=False
        for sh, lst in read_validations(hits[0]).items():
            dd[(t, sh)] = lst
    row_items = {}
    for it in items:
        if str(it['셀좌표'] or '').startswith('row'):
            row_items.setdefault((it['템플릿'], it['시트']), []).append(int(str(it['셀좌표']).split()[1]))
    out, miss = {}, 0
    for it in items:
        t, sh, coord = it['템플릿'], it['시트'], str(it['셀좌표'] or '')
        wb = books.get(t)
        if wb is None or sh not in wb.sheetnames:
            miss += 1
            continue
        ws = wb[sh]
        if coord.startswith('row'):
            r = int(coord.split()[1])
            later = [x for x in sorted(row_items[(t, sh)]) if x > r]
            end = min((later[0] - 1) if later else r + 25, r + 25, ws.max_row)   # 다음 문항 줄 앞까지(최대 25줄)
            window = [c for rr in range(r, end + 1) for c in ws[rr]]
            cells = [c.coordinate for c in window if is_input_fill(c)][:MAX_CELLS]
            if not cells:
                cells = [c.coordinate for c in window if isinstance(c.value, str) and c.value.startswith('=')][:MAX_CELLS]
            if not cells:
                cells = [c.coordinate for c in ws[r] if c.value not in (None, '')][-3:]
        elif re.match(r'^[A-Z]+\d+(:[A-Z]+\d+)?$', coord):
            cells = cells_of(coord)
        else:
            miss += 1
            continue
        if not cells:
            miss += 1
            continue
        own_row = False
        if coord.startswith('row'):
            own_row = any(is_input_fill(c) for c in ws[int(coord.split()[1])])
        frac = 0.0001 if (coord.startswith('row') and own_row) else 0.5      # 자기 줄 입력칸이 있는 줄 항목은 하나라도 겹치면
        dropdown_opts = [o for rg, o in dd.get((t, sh), []) if overlaps(cells, rg, frac)]
        dv_list = False
        dv_num = False
        for dv in ws.data_validations.dataValidation:
            if any(overlaps(cells, str(rg)) for rg in dv.sqref.ranges) if hasattr(dv.sqref, 'ranges') else overlaps(cells, str(dv.sqref)):
                if dv.type == 'list':
                    dv_list = True
                if dv.type in ('decimal', 'whole'):
                    dv_num = True
        first = ws[cells[0]]
        formula = any(isinstance(ws[c].value, str) and ws[c].value.startswith('=') for c in cells)
        mw = mh = 1
        for mr in ws.merged_cells.ranges:
            if first.coordinate in mr:
                mw, mh = mr.max_col - mr.min_col + 1, mr.max_row - mr.min_row + 1
                break
        # 입력칸과 같은 줄의 왼쪽 칸 글(행 머리글). 입력칸 자체에는 단위 표시가 없어도 행 머리글(예: 「Total number of …」)이 값의 성격을 말해 줌
        labels, seen = [], set()
        for cname in cells[:30]:
            rr = ws[cname].row
            for c in ws[rr]:
                if c.column >= ws[cname].column or not isinstance(c.value, str):
                    continue
                t = re.sub(r'\s+', ' ', c.value).strip()
                if len(t) >= 6 and t not in seen:
                    seen.add(t)
                    labels.append(t)
        per_row = []                                       # 입력칸 줄마다 왼쪽에서 가장 먼저 나오는 글(줄마다 다른 머리글이 있는 목록형인지 보려고)
        for cname in cells[:30]:
            first_txt = None
            for c in ws[ws[cname].row]:
                if c.column < ws[cname].column and isinstance(c.value, str) and len(c.value.strip()) >= 6:
                    first_txt = re.sub(r'\s+', ' ', c.value).strip()
                    break
            per_row.append(first_txt)
        row_label_unique = len(per_row) >= 2 and all(per_row) and len(set(per_row)) == len(per_row)
        cols = {re.match(r'[A-Z]+', c).group(0) for c in cells}
        out[it['ID']] = {'row_labels': ' | '.join(labels)[:300], 'col_stack': len(cols) == 1 and len(cells) >= 2, 'row_label_unique': bool(row_label_unique), 'first_row_label': (per_row[0] or '')[:200] if per_row else '',
                         'dropdown': bool(dropdown_opts) or dv_list, 'options': (dropdown_opts[0] if dropdown_opts else '')[:80],
                         'formula': formula, 'dv_numeric': dv_num, 'numfmt': first.number_format,
                         'merged_w': mw, 'merged_h': mh, 'cells': len(cells),
                         'own_row_input': own_row if coord.startswith('row') else None}
    OUT.write_text(json.dumps({'_설명': '칸의 실제 모양(8단계 입력유형 판정용). extract_template_cells.py가 만든다.',
                               'items': out}, ensure_ascii=False, indent=0), encoding='utf-8')
    n_dd = sum(1 for v in out.values() if v['dropdown']); n_fx = sum(1 for v in out.values() if v['formula'])
    print(f'항목 {len(items)}개 중 칸 모양을 읽은 것 {len(out)}개 (못 읽은 것 {miss}개) · 선택 목록 {n_dd}개 · 수식 {n_fx}개')
    print(f'저장: {OUT}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('사용법: python 8_classifier/tools/extract_template_cells.py <템플릿 엑셀 폴더>')
    main(sys.argv[1])
