"""질문-정답 작성 양식(7_rag/qa_testset_template_v1.xlsx, 파일 이름은 압축이 깨지지 않게 영문)을 만든다 (7단계 절차 안내).
    python 7_rag/tools/make_qa_template.py
양식의 열은 engine/corsia/rag/qa.py 가 읽는 이름과 같아야 한다(「질문」「정답」「핵심 값」「근거 조항」「문서에 답이 있나」「용도」).
"""
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

OUT = Path(__file__).resolve().parents[1] / 'qa_testset_template_v1.xlsx'
TYPES = ['기한', '제출 방법·서식', '대상·자격', '절차·순서', '정의', '의무·벌칙', '별표 작성방법', '문서에 없음']
HEAD = PatternFill('solid', fgColor='D9E1F2')
YELLOW = PatternFill('solid', fgColor='FFF2CC')
GREY = PatternFill('solid', fgColor='EDEDED')
COLS = ['번호', '질문 유형', '질문', '정답', '핵심 값', '근거 조항', '근거 원문(복사)', '문서에 답이 있나', '용도', '작성자', '교차 확인', '교차 확인자', '메모']
WIDTH = [6, 14, 44, 44, 22, 28, 50, 12, 9, 10, 10, 12, 24]

GUIDE = [
    ('목적', '절차 안내(RAG)가 얼마나 맞게 답하는지 채점할 「질문-정답」 20~30쌍을 만듭니다. 이 양식은 30문항입니다.'),
    ('가장 중요한 규칙', '정답은 법령 원문을 직접 보고 쓰고, AI에게 묻지 않습니다. 정답을 AI가 만들면 AI를 AI의 정답으로 채점하게 되어 점수를 믿을 수 없습니다. (골든셋과 같은 규칙)'),
    ('범위', '법률, 시행령, 시행규칙, 국토교통부 지침(고시 제2025-458호)의 본문과 별표 1~7. 환경부 검증지침과 ICAO 문서는 이번 범위가 아닙니다.'),
    ('질문 쓰는 법', '실무자가 묻는 말투로 씁니다(예: 「…는 언제까지 내야 하나요?」). 질문에 조문 번호(제8조 등)를 쓰지 마세요. 번호를 쓰면 검색이 쉬워져 검색 시험이 되지 않습니다. 한 문항에는 답이 하나만 나오게 씁니다.'),
    ('정답', '원문대로 한두 문장. 기한·수치·대상은 원문 표현 그대로.'),
    ('핵심 값', '정답에 반드시 들어가야 하는 값·표현을 1~3개, 「 / 」로 구분(예: 4월 30일 / 국토교통부장관). 채점은 AI 답에 이 값이 모두 들어 있는지로 합니다. 날짜는 「4월 30일」과 「4/30」을 같게 봅니다. 문장 전체를 넣지 마세요.'),
    ('근거 조항', '판정 기준 1의 표기로: 「법 제8조제4항 / 시행규칙 제7조」, 별표는 「지침 별표 4 3.a)」. 여러 개면 「 / 」. 항·호까지 알면 쓰고 모르면 조까지. 채점은 조(또는 별표) 단위로 맞는지 봅니다.'),
    ('근거 원문(복사)', '정답의 근거가 되는 문장을 원문에서 그대로 복사(150자 안팎). 교차 확인하는 사람이 바로 대조할 수 있게 합니다.'),
    ('문서에 없음 질문', '법령·고시에 정말 없는 내용을 묻는 질문을 3개 이상 넣습니다(가격, 다른 나라 제도, 법령이 정하지 않은 세부 등). 「문서에 답이 있나」를 「없음」으로 하고, 정답에 「제공된 법령·고시에는 없음」, 핵심 값·근거 조항은 비웁니다. AI가 지어내지 않고 「근거 없음」이라고 하는지 보기 위한 문항입니다.'),
    ('용도', '개발용 / 채점용. 개발용(7개)은 검색·지시문을 조정할 때 보고, 채점용(23개)은 조정에 쓰지 않고 정확도 보고에만 씁니다. 번호 4의 배수가 개발용으로 미리 표시되어 있고, 바꿔도 됩니다.'),
    ('질문 유형 권장 분포(30문항)', '기한 6 · 제출 방법·서식 5 · 대상·자격 4 · 절차·순서 4 · 정의 3 · 의무·벌칙 3 · 별표 작성방법 2 · 문서에 없음 3'),
    ('나눠 쓰기', '4명이 번호 구간을 나눠 씁니다(예: 1~8번, 9~15번, 16~23번, 24~30번). 1인당 약 40분.'),
    ('교차 확인', '다른 사람의 문항을 읽고 정답·근거를 원문에서 직접 확인해 「교차 확인」에 동의 / 수정 / 보류를 적습니다(1→2→3→4→1, 20분 안팎). 수정·보류만 짧게 합의합니다.'),
    ('견본', '「2_견본(예시)」 3건은 형식을 보여 주는 예시입니다. 30문항에 넣지 않습니다.'),
]
EXAMPLES = [
    [None, '기한', '배출량보고서와 배출량검증보고서는 매년 언제까지 국토교통부에 내야 하나요?', '매년 4월 30일까지 제출합니다.', '4월 30일',
     '법 제8조제4항 / 시행규칙 제7조', '법 제8조제4항 전단에서 “국토교통부령으로 정하는 기한”이란 매년 4월 30일을 말한다.', '있음', '(예시)', '(예시)', None, None, '시행규칙 제7조'],
    [None, '기한', '이행의무자로 지정·고시되면 모니터링 계획 승인은 언제까지 신청해야 하나요?', '지정·고시된 날부터 3개월 이내에 국토교통부장관에게 신청해야 합니다.', '3개월 이내',
     '법 제6조제3항', '지정ㆍ고시된 날부터 3개월 이내에 국토교통부장관에게 모니터링 계획의 승인을 신청하여야 한다.', '있음', '(예시)', '(예시)', None, None, None],
    [None, '문서에 없음', 'CORSIA 적격연료의 국제 시장 가격은 얼마인가요?', '제공된 법령·고시에는 없음', None, None, None, '없음', '(예시)', '(예시)', None, None, '가격은 법령에 없는 내용'],
]


def main():
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = '0_작성안내'
    ws0.append(['절차 안내 질문-정답 작성 양식 v1 (초안)'])
    ws0['A1'].font = Font(bold=True, size=13)
    ws0.append([])
    for k, v in GUIDE:
        ws0.append([k, v])
    ws0.column_dimensions['A'].width = 26
    ws0.column_dimensions['B'].width = 120
    for r in ws0.iter_rows(min_row=3):
        r[0].font = Font(bold=True)
        r[0].alignment = Alignment(vertical='top', wrap_text=True)
        r[1].alignment = Alignment(vertical='top', wrap_text=True)

    ws = wb.create_sheet('1_질문정답')
    ws.append(COLS)
    for c in range(1, len(COLS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical='top')
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = WIDTH[c - 1]
    for n in range(1, 31):
        ws.append([n, None, None, None, None, None, None, '있음', '개발용' if n % 4 == 0 else '채점용', None, None, None, None])
        for c in range(2, len(COLS) + 1):
            ws.cell(row=n + 1, column=c).fill = YELLOW
        ws.cell(row=n + 1, column=8).fill = GREY
        ws.cell(row=n + 1, column=9).fill = GREY
    ws.freeze_panes = 'D2'
    for r in range(2, 32):
        for c in (3, 4, 6, 7):
            ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical='top')

    ws2 = wb.create_sheet('2_견본(예시)')
    ws2.append(COLS)
    for c in range(1, len(COLS) + 1):
        cell = ws2.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = HEAD
        ws2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = WIDTH[c - 1]
    for i, row in enumerate(EXAMPLES, 1):
        row = list(row)
        row[0] = f'예{i}'
        ws2.append(row)
    for r in ws2.iter_rows(min_row=2):
        for cell in r:
            cell.alignment = Alignment(wrap_text=True, vertical='top')

    ws3 = wb.create_sheet('3_목록값')
    ws3.append(['질문 유형', '문서에 답이 있나', '용도', '교차 확인'])
    lists = [TYPES, ['있음', '없음'], ['개발용', '채점용'], ['동의', '수정', '보류']]
    for i in range(max(len(x) for x in lists)):
        ws3.append([x[i] if i < len(x) else None for x in lists])

    def dv(col, rng_formula):
        d = DataValidation(type='list', formula1=rng_formula, allow_blank=True)
        ws.add_data_validation(d)
        d.add(f'{col}2:{col}31')
    dv('B', f"'3_목록값'!$A$2:$A${1 + len(TYPES)}")
    dv('H', "'3_목록값'!$B$2:$B$3")
    dv('I', "'3_목록값'!$C$2:$C$3")
    dv('K', "'3_목록값'!$D$2:$D$4")
    wb.save(OUT)
    print('저장:', OUT)


if __name__ == '__main__':
    main()
