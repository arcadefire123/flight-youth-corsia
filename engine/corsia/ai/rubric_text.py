"""
기준표(7_qualitative/qualitative_rubric_v1.md)에서 AI 지시문에 넣을 내용을 읽어 온다 (7단계 E)

기준표 문서를 그대로 읽으므로, 기준표를 고치면 지시문도 따라 바뀐다(두 곳을 따로 고치지 않음).
읽는 것 (Part 2 규칙별 기준표):
  - 제목, 판정 질문
  - 항목 표(ID, 확인할 것, 구분, 근거)
  - 참고 표(Q-04 방법별 측정 시점, Q-07 의견 선택 조건 등)
  - 표 아래 설명(「부족」의 예, 판단 불가, 결정 사항). 대상 칸·원문 같은 머리 정보는 빼고, 코드가 처리하는
    조건(Q-06)도 뺀다
check_qual_testset.py가 이 결과의 항목 목록·구분이 rubric.py와 같은지 확인한다.
"""
import re
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / '7_qualitative' / 'qualitative_rubric_v1.md'

KIND_MAP = {'필수': '필수', '권장': '권장', '조건부 필수': '조건부'}
META_LABELS = ('대상 칸', '함께 주는 정보', '함께 읽는 칸', '판정 질문', '원문', '조건')


def _clean(text):
    t = text.replace('**', '').replace('`', '')
    t = re.sub(r'\s*[,·]?\s*Part 4 [①-⑳]', '', t)      # 기준표 안의 쪽 참조는 AI에게 뜻이 없음
    t = re.sub(r'\(\s*\)', '', t)
    return re.sub(r'[ \t]+', ' ', t).strip()


def _cells(row):
    return [c.strip() for c in row.strip().strip('|').split('|')]


def rubric_version(text):
    rows = re.findall(r'^\| \**(v[\d.]+[^|*]*?)\** \|', text, re.M)
    return rows[-1].strip() if rows else '?'


def parse_rubric(text):
    """{규칙: {'title', 'question', 'checks': {ID: {'text','kind','basis'}}, 'tables': [...], 'guide': [...]}}"""
    m = re.search(r'^## Part 2.*$', text, re.M)
    if not m:
        raise ValueError('기준표에서 Part 2를 찾지 못함')
    part2 = text[m.end():]
    end = re.search(r'^## Part 3', part2, re.M)
    if end:
        part2 = part2[:end.start()]
    pieces = re.split(r'^### (Q-0\d)\. ', part2, flags=re.M)
    out = {}
    for rid, body in zip(pieces[1::2], pieces[2::2]):
        body = re.split(r'^---\s*$', body, flags=re.M)[0]
        lines = body.splitlines()
        sec = {'title': _clean(lines[0]), 'question': '', 'checks': {}, 'tables': [], 'guide': []}
        table, caption = [], None

        def flush():
            nonlocal table, caption
            if not table:
                return
            head = _cells(table[0])
            if head[:2] == ['ID', '확인할 것']:
                for row in table[2:]:
                    cid, what, kind, basis = (_cells(row) + ['', '', '', ''])[:4]
                    if kind not in KIND_MAP:
                        raise ValueError(f'{rid} {cid}: 모르는 구분 {kind!r}')
                    sec['checks'][cid] = {'text': _clean(what), 'kind': KIND_MAP[kind], 'basis': _clean(basis)}
            else:
                sec['tables'].append(((_clean(caption) + '\n') if caption else '') + _clean('\n'.join(table)))
            table, caption = [], None

        for ln in lines[1:]:
            if ln.startswith('|'):
                table.append(ln)
                continue
            flush()
            st = ln.strip()
            if st.startswith('**') and st.endswith('**'):
                caption = st
            elif st.startswith('- '):
                item = st[2:]
                label = _clean(item.split(':', 1)[0]) if ':' in item else ''
                if label == '판정 질문':
                    sec['question'] = _clean(item.split(':', 1)[1])
                elif label in META_LABELS:
                    continue
                else:
                    sec['guide'].append(_clean(item))
        flush()
        out[rid] = sec
    return out


def load_rubric(path=None):
    p = Path(path) if path else DEFAULT_PATH
    text = p.read_text(encoding='utf-8')
    return {'version': rubric_version(text), 'rules': parse_rubric(text), 'path': str(p)}
