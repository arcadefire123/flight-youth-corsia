"""조각 검색: 글자 조각(2·3글자) TF-IDF 코사인 + 「제N조」·「별표 N」 직접 언급 가산 (7단계 RAG, 외부 패키지 없음)."""
import math
import re
from collections import Counter

from . import corpus as K

REF_ART = re.compile(r'(법|시행령|시행규칙|지침)?\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제\s*(\d+)\s*항)?')
REF_ANNEX = re.compile(r'별표\s*(\d+)')
BOOST_DOC = 0.5          # 「법 제8조」처럼 문서까지 말했을 때
BOOST_ANY = 0.2          # 「제8조」처럼 문서를 안 말했을 때(모든 문서의 제8조)
BOOST_ANNEX = 0.12       # 별표 번호만 말했을 때: 그 별표 안에서는 글 유사도가 고르게 한다


def _key(chunk):
    """조각 ID에서 (문서, 조 번호(+의), 항 번호) — 조·항 조각만. 별표 조각은 None"""
    m = K.CITE_ART.match(chunk['id'])
    if not m or chunk['kind'] not in ('조', '항'):
        return None
    return m.group(1), m.group(2) + (f'의{m.group(3)}' if m.group(3) else ''), int(m.group(4)) if m.group(4) else None


def grams(text):
    t = re.sub(r'[^0-9a-z가-힣]+', ' ', str(text or '').lower())
    t = ' '.join(t.split())
    out = []
    for n in (2, 3):
        out += [t[i:i + n] for i in range(max(len(t) - n + 1, 0))]
    return out


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        docs = [Counter(grams(c['text'])) for c in chunks]
        df = Counter()
        for d in docs:
            df.update(d.keys())
        n = len(docs)
        self.idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self.vecs, self.norms = [], []
        for d in docs:
            v = {g: (1 + math.log(c)) * self.idf[g] for g, c in d.items()}
            self.vecs.append(v)
            self.norms.append(math.sqrt(sum(x * x for x in v.values())) or 1.0)

    def _qvec(self, query):
        q = Counter(g for g in grams(query) if g in self.idf)
        v = {g: (1 + math.log(c)) * self.idf[g] for g, c in q.items()}
        return v, math.sqrt(sum(x * x for x in v.values())) or 1.0

    def search(self, query, k=5):
        """(조각, 점수, 사유) 목록(점수 높은 순). 사유: 'tfidf' 또는 '직접 언급'"""
        qv, qn = self._qvec(query)
        boosts = {}
        for m in REF_ART.finditer(query):
            doc, art = m.group(1), m.group(2) + (f'의{m.group(3)}' if m.group(3) else '')
            para = int(m.group(4)) if m.group(4) else None
            for i, c in enumerate(self.chunks):
                key = _key(c)
                if key is None or key[1] != art or (doc is not None and key[0] != doc):
                    continue
                if para is not None and key[2] not in (None, para):   # 「제6조제3항」이라고 했으면 그 항(또는 조 전체 조각)만
                    continue
                boosts[i] = max(boosts.get(i, 0), BOOST_DOC if doc else BOOST_ANY)
        for m in REF_ANNEX.finditer(query):
            for i, c in enumerate(self.chunks):
                if c['unit'] == f'별표 {m.group(1)}':
                    boosts[i] = max(boosts.get(i, 0), BOOST_ANNEX)
        scored = []
        for i, c in enumerate(self.chunks):
            b = self.vecs[i]
            small, big = (qv, b) if len(qv) < len(b) else (b, qv)
            dot = sum(x * big.get(g, 0.0) for g, x in small.items())
            s = dot / (qn * self.norms[i])
            if i in boosts:
                s += boosts[i]
            scored.append((s, i))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, i in scored[:k]:
            out.append((self.chunks[i], round(s, 4), '직접 언급' if i in boosts else 'tfidf'))
        return out
