"""법령·고시 4종(법률·시행령·시행규칙·지침)을 조·항·별표 항목 단위 조각으로 나눈다 (7단계 RAG).

조각 ID는 판정 기준 1의 근거 표기와 같다(예: 「법 제8조제4항」, 「시행규칙 제7조」, 「지침 별표 4 3.a)」).
그래서 팀이 질문-정답의 「근거 조항」을 같은 표기로 쓰면 그대로 비교할 수 있다.
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAG_DIR = ROOT / '7_rag'
CHUNKS_PATH = RAG_DIR / 'corpus_chunks_v1.jsonl'
DOCS = {'법': '국제항공 탄소 배출량 관리에 관한 법률', '시행령': '국제항공 탄소 배출량 관리에 관한 법률 시행령',
        '시행규칙': '국제항공 탄소 배출량 관리에 관한 법률 시행규칙', '지침': '국제항공 탄소 배출량 관리에 관한 지침(국토교통부고시 제2025-458호)'}
MAX_ARTICLE_CHARS = 900          # 이보다 긴 조는 항 단위로 나눈다
WINDOW_CHARS = 900               # 항목 구조가 없는 별표(산식·표)는 이 길이로 나눈다
ART = re.compile(r'(?m)^\s*제(\d+)조(?:의(\d+))?\(([^)\n]{1,40})\)')
ANNEX_PAGE = re.compile(r'■[^\n]*\[별표\s*(\d+)\]')
PARA = re.compile(r'(?m)(?:^|\s)([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])')
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
SEC = re.compile(r'^(\d{1,2})\.\s*(\S.*)$')
ITEM = re.compile(r'^ {0,8}([a-z]\d{0,2})\)\s+(\S.*)$')      # a) b4) — 들여쓰기는 쪽마다 달라 글자+숫자로만 본다


def flat(text):
    return re.sub(r'\s+', ' ', text).strip()


def clean(raw):
    """쪽 머리글·바닥글처럼 여러 번 반복되는 짧은 줄을 지운다(조·항·별표 표지는 남긴다)."""
    raw = raw.replace('\r', '')
    lines = raw.split('\n')
    freq = Counter(l.strip() for l in lines if l.strip())
    out = []
    for l in lines:
        s = l.strip()
        if s and freq[s] >= 4 and len(s) < 120 and not ART.match(l) and '[별표' not in s:
            continue
        if re.match(r'^\s*법제처\s+\d+\s+국가법령정보센터', l) or re.match(r'^\s*[-－]\s*\d+\s*[-－]\s*$', l):
            continue
        out.append(l.replace('\f', ''))
    return '\n'.join(out)


def cid(doc, n, sub, para=None):
    base = f'{doc} 제{n}조' + (f'의{sub}' if sub else '')
    return base + (f'제{para}항' if para else '')


def chunk_articles(text, doc):
    """제N조(제목) 단위. 부칙은 따로 둔다. 목차 줄(본문 없음)은 버린다."""
    marks = [(m.start(), m) for m in ART.finditer(text)]
    best = {}
    for i, (pos, m) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        cut = re.search(r'(?m)^\s*부칙', body)            # 부칙이 이 조 뒤에 붙지 않게
        if cut:
            body = body[:cut.start()]
        body = flat(body)
        if len(body) < 25:
            continue
        n, sub, title = m.group(1), m.group(2), m.group(3)
        key = (n, sub)
        if key in best and len(best[key][1]) >= len(body):
            continue
        best[key] = (title, body)
    chunks = []
    for (n, sub), (title, body) in sorted(best.items(), key=lambda kv: (int(kv[0][0]), int(kv[0][1] or 0))):
        head = f'제{n}조' + (f'의{sub}' if sub else '') + f'({title})'
        if len(body) <= MAX_ARTICLE_CHARS or not PARA.search(body):
            chunks.append({'id': cid(doc, n, sub), 'doc': doc, 'unit': head, 'kind': '조', 'title': title, 'text': f'{head} {body}'})
            continue
        parts = re.split(r'(?=(?:^|\s)[' + CIRCLED + '])', body)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if p[0] in CIRCLED:
                para = CIRCLED.index(p[0]) + 1
                chunks.append({'id': cid(doc, n, sub, para), 'doc': doc, 'unit': head, 'kind': '항', 'title': title, 'text': f'{head} {p}'})
            else:                                          # ① 앞의 머리말은 첫 항에 붙인다
                if chunks and chunks[-1]['unit'] == head:
                    chunks[-1]['text'] = f'{head} {p} ' + chunks[-1]['text'].split(')', 1)[1].strip()
    return chunks


def _windows(text, size):
    out, buf = [], ''
    for line in text.split('\n'):
        if buf and len(buf) + len(line) > size:
            out.append(buf)
            buf = ''
        buf += (' ' if buf else '') + line.strip()
    if buf.strip():
        out.append(buf)
    return out


def chunk_annexes(text):
    """[별표 N] 쪽들을 N별로 모아, 항목 구조(1. / a) / a1))가 있으면 항목 단위, 없으면 길이 단위로 나눈다."""
    starts = [(m.start(), m.group(1)) for m in ANNEX_PAGE.finditer(text)]
    pages = {}
    for i, (pos, n) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        seg = text[pos:end]
        seg = ANNEX_PAGE.sub('', seg, count=1)
        pages.setdefault(n, []).append(seg)
    chunks = []
    for n in sorted(pages, key=int):
        lines = [l for seg in pages[n] for l in seg.split('\n') if l.strip()]
        title = flat(lines[0]) if lines else ''
        body = lines[1:]
        structured = sum(1 for l in body if SEC.match(l.strip()) or ITEM.match(l)) >= 5
        if not structured:
            for k, w in enumerate(_windows('\n'.join(body), WINDOW_CHARS), 1):
                chunks.append({'id': f'지침 별표 {n} ({k})', 'doc': '지침', 'unit': f'별표 {n}', 'kind': '별표구간', 'title': title,
                               'text': f'[별표 {n}] {title} — {flat(w)}'})
            continue
        sec_no, sec_title, cur, last_sec = '', '', None, 0
        def flush():
            nonlocal cur
            if cur and len(flat(cur['text'])) >= 8:
                cur['text'] = flat(cur['text'])
                chunks.append(cur)
            cur = None
        for l in body:
            ms = SEC.match(l.strip()) if not l.startswith(' ') else None
            if ms and int(ms.group(1)) != last_sec + 1:    # 본문 속 번호 목록은 새 절로 보지 않는다(절 번호는 1, 2, 3 …로만 이어짐)
                ms = None
            mi = ITEM.match(l)
            mu = None
            if ms:
                last_sec = int(ms.group(1))
                flush(); sec_no, sec_title = ms.group(1), flat(ms.group(2))
                cur = {'id': f'지침 별표 {n} {sec_no}.', 'doc': '지침', 'unit': f'별표 {n}', 'kind': '별표항목', 'title': title,
                       'text': f'[별표 {n}] {sec_no}. {sec_title}'}
            elif mi or mu:
                m = mi or mu
                flush()
                cur = {'id': f'지침 별표 {n} {sec_no}.{m.group(1)})' if sec_no else f'지침 별표 {n} {m.group(1)})', 'doc': '지침', 'unit': f'별표 {n}',
                       'kind': '별표항목', 'title': title,
                       'text': f'[별표 {n}] {sec_no}. {sec_title} > {m.group(1)}) {flat(m.group(2))}' if sec_no else f'[별표 {n}] {m.group(1)}) {flat(m.group(2))}'}
            elif cur is not None:
                cur['text'] += ' ' + l.strip()
        flush()
    return chunks


def build(texts):
    """texts: {'법': 원문글, '시행령': …, '시행규칙': …, '지침': …} → 조각 목록(문서 순서)"""
    chunks = []
    for doc in ('법', '시행령', '시행규칙'):
        chunks += chunk_articles(clean(texts[doc]), doc)
    g = clean(texts['지침'])
    first_annex = ANNEX_PAGE.search(g)
    body, annexes = (g[:first_annex.start()], g[first_annex.start():]) if first_annex else (g, '')
    chunks += chunk_articles(body, '지침')
    if annexes:
        chunks += chunk_annexes(annexes)
    seen, out = set(), []
    for c in chunks:                                  # ID가 겹치면 뒤에 붙여 구분
        base, k = c['id'], 2
        while c['id'] in seen:
            c['id'] = f'{base}#{k}'
            k += 1
        seen.add(c['id'])
        out.append(c)
    return out


def save(chunks, path=CHUNKS_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in chunks) + '\n', encoding='utf-8')


def load(path=CHUNKS_PATH):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]


def tokens_est(text):
    ko = len(re.findall(r'[가-힣]', text))
    en = len(re.findall(r'[A-Za-z]', text))
    return int(ko * 0.95 + en * 0.27 + (len(text) - ko - en) * 0.3)


def full_context(chunks):
    """통째 넣기 변형용: 모든 조각을 [ID] 원문 형태로 문서 순서대로."""
    return '\n'.join(f"[{c['id']}] {c['text']}" for c in chunks)


# ---- 인용 표기 해석 ----
CITE_ART = re.compile(r'^(법|시행령|시행규칙|지침)\s*제(\d+)조(?:의(\d+))?(?:제(\d+)항)?')
CITE_ANNEX = re.compile(r'^지침\s*별표\s*(\d+)\s*(?:(\d+)\.)?')


def unit_of(cite):
    """인용 문자열의 조 단위 키: ('법','8') / ('지침','별표4'). 해석 못 하면 None"""
    c = flat(str(cite))
    m = CITE_ANNEX.match(c)
    if m:
        return ('지침', f'별표{m.group(1)}')
    m = CITE_ART.match(c)
    if m:
        return (m.group(1), m.group(2) + (f'의{m.group(3)}' if m.group(3) else ''))
    return None


def split_cites(v):
    t = flat(str(v or ''))
    return [flat(p) for p in re.split(r'\s*/\s*', t) if flat(p)] if t not in ('', '-') else []
