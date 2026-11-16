"""AI 답(JSON)을 속성별로 정리하고 형식을 점검한다 (8단계).

점검에서 걸려도 값을 고치지 않고 표시(flags)만 한다. 채점은 AI가 쓴 값 그대로 한다."""
import re

from . import catalog as C

NO_RULE = C.NO_RULE
DATE_KR = re.compile(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일')
BASIS_PART = [
    re.compile(r'^지침 별지 제\d+호서식 [\d.]+[.\s]?[a-z]?\d*\)?'),
    re.compile(r'^지침 별표 \d+ [\d.]+[.\s]?[a-z]?\d*\)?'),
    re.compile(r'^지침 제\d+조(제\d+항)?(\s?제\d+호)?$'),
    re.compile(r'^(법|시행령|시행규칙) 제\d+조(제\d+항)?(\s?제\d+호)?$'),
    re.compile(r'^Annex16 Vol\.IV (App\.\d+|Part II)( Ch\.\d+)? ?§?[\d.]*'),
    re.compile(r'^ETM 부록1 1\.\d [A-Za-z]+(?: [A-Za-z0-9.]+\)?)?$'),
    re.compile(r'^ETM §?\d+(?:\.\d+)*(?:~\d+)?(?: 표 ?\d+-\d+)?$'),                # 예: ETM §3.3.4.4 표 3-11 (문항별 추가 인용)      # 「ETM 부록1 1.6 VR s2)」 — 항목번호 자리에 「-」 같은 것이 오면 형식 오류
]


def norm_space(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()


def norm_date(v):
    t = norm_space(v)
    t = DATE_KR.sub(r'\1/\2', t)
    return t


def split_parts(v):
    t = norm_space(v)
    if t in ('', '-', '없음'):
        return []
    return [norm_space(p) for p in t.split(' / ') if norm_space(p)]


# 같은 날짜의 다른 표기(2026-11-16 회의: 이행완수연도 = 이행기간 마지막 연도의 다음다음 해)
DEADLINE_ALIASES = {'이행기간 종료 다음다음 해 4/30': '이행완수연도 4/30'}


def norm_value(attr, v):
    """비교·저장용 정규화"""
    t = norm_space(v)
    if attr == '시점·기한':
        t = norm_date(t)
        return DEADLINE_ALIASES.get(t, t)
    if attr == '연결필드':
        return ' / '.join(split_parts(t)) or '-'
    if attr == '근거':
        return ' / '.join(split_parts(t))
    return t


def check_value(item_id, attr, value):
    """형식 점검. 문제가 있으면 짧은 문구 목록을 돌려준다(없으면 빈 목록)."""
    flags = []
    v = norm_value(attr, value)
    if v == '':
        return ['빈 값']
    if v == NO_RULE:
        return flags
    codes = C.load_codes()
    if attr in ('주체', '단계', '의무성', '입력유형'):
        if v not in codes[attr]:
            flags.append(f'{attr}: 코드값 목록에 없음({v})')
    elif attr == '시점·기한':
        allowed = {norm_value('시점·기한', r.get('시점·기한')) for r in C.template_rules().values() if isinstance(r, dict)}
        allowed |= {norm_value('시점·기한', r.get('시점·기한_이행')) for r in C.template_rules().values() if isinstance(r, dict)}
        if v not in allowed:
            flags.append(f'시점·기한: 템플릿 기한표에 없는 값({v})')
    elif attr == '근거':
        for part in split_parts(v):
            if not any(p.match(part) for p in BASIS_PART):
                flags.append(f'근거: 알 수 없는 형식({part[:30]})')
    elif attr == '연결필드':
        cand = {C.link_label(c) for c, _ in C.candidates(item_id)}
        for part in split_parts(v):
            if not re.fullmatch(r'.+![\w.\-]+', part):
                flags.append(f'연결필드: 형식 오류({part})')
            elif part not in cand:
                flags.append(f'연결필드: 후보 목록 밖({part})')
    return flags
