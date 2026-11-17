"""
화면용 HTML 조각 — 정성 검증 「AI 참고 의견」 카드

app.py가 부르는 순수 함수만 있다(streamlit을 쓰지 않아서 단독으로 시험할 수 있다).
입력은 web_qual.iter_qualitative / run_qualitative가 돌려주는 qualitative_result_v1 (항목은 item 사전)이다.

원칙
  - AI가 쓴 글(이유·인용·메모)과 서류 글은 전부 이스케이프한다. HTML이 섞여 와도 화면에 그대로 글자로 보인다.
  - 항목별 source 필드(api / cache / mock)가 있으면 쓰고, 없으면 기존 cached 값으로 「기억에서」를 판단한다. 없어도 동작한다.
  - 색만으로 구분하지 않는다. 판정 배지에는 글자(충분 / 보완 필요 / 판단 불가)가 들어 있다.
  - 이 조각은 상단 요약 상자·종합점수·위반 목록과 무관하다. AI 결과는 참고용 영역에만 그린다.
"""
import html

# 판정 배지 색 (글자색, 배경색). 위반 목록의 심각도 색과 구분되도록 배지는 배경을 채운 알약 모양으로 그린다.
VERDICT_STYLE = {
    '충분': ('#2E7D52', 'rgba(46,125,82,.13)'),
    '보완 필요': ('#B45F06', 'rgba(199,119,0,.16)'),
    '판단 불가': ('#6B7280', 'rgba(107,114,128,.17)'),
}
NEUTRAL = ('#6B7280', 'rgba(107,114,128,.17)')
FAILED = ('#B42318', 'rgba(214,69,69,.13)')

# 항목 상태 → 배지 글자 (판정이 없는 상태)
STATUS_LABEL = {
    'skipped_condition': '해당 없음(조건 미발생)',
    'no_text': '서술 없음',
    'budget_exceeded': '판정 못 받음',
    'network_error': '판정 못 받음',
    'api_error': '판정 못 받음',
    'parse_error': '판정 못 받음',
}
CHECK_STATUS_COLOR = {'있음': '#2E7D52', '부족': '#B45F06', '없음': '#B42318'}


def esc(v):
    return html.escape('' if v is None else str(v))


def item_source(it):
    """항목 결과의 출처: 'api' | 'cache' | 'mock' | None.
    source 필드가 있으면 그것을, 없으면 cached 값을 쓴다(없어도 동작)."""
    s = it.get('source')
    if s in ('api', 'cache', 'mock'):
        return s
    return 'cache' if it.get('cached') else None


def item_badge(it):
    """(배지 글자, (글자색, 배경색)). 판정이 있으면 판정, 없으면 상태."""
    verdict = it.get('verdict')
    if verdict in VERDICT_STYLE:
        return verdict, VERDICT_STYLE[verdict]
    st = it.get('status')
    label = STATUS_LABEL.get(st)
    if label:
        return label, (FAILED if label == '판정 못 받음' else NEUTRAL)
    return '판정 없음', NEUTRAL


def _pill(text, color, bg, cls='vbadge'):
    return f'<span class="{cls}" style="color:{color};background:{bg}">{esc(text)}</span>'


def _small_badge(text, warn=False):
    return f'<span class="sbadge{" warn" if warn else ""}">{esc(text)}</span>'


def _checks_html(it):
    checks = it.get('checks') or []
    if not checks:
        return ''
    counts = {}
    for c in checks:
        counts[c.get('status')] = counts.get(c.get('status'), 0) + 1
    summary = ' · '.join(f'{k} {v}' for k, v in counts.items() if k)
    rows = ''
    for c in checks:
        col = CHECK_STATUS_COLOR.get(c.get('status'), '#6B7280')
        ev = c.get('evidence')
        ev_html = f'<div class="qev">「{esc(ev)}」</div>' if ev and ev != '…' else ''
        note = f'<div class="qnote">{esc(c["note"])}</div>' if c.get('note') else ''
        flag = ''
        if c.get('flag'):
            flag = f'<div class="qflag">{"인용 확인 필요" if c.get("citation_problem") or c.get("fake_citation") else "참고"}: {esc(c["flag"])}</div>'
        rows += (f'<tr><td class="qid">{esc(c.get("id"))}</td><td>{esc(c.get("kind"))}</td>'
                 f'<td>{esc(c.get("text"))}{ev_html}{note}{flag}</td>'
                 f'<td style="color:{col};font-weight:600;white-space:nowrap">{esc(c.get("status"))}</td></tr>')
    return (f'<details class="qchk"><summary>확인 항목 {len(checks)}개 ({esc(summary)})</summary>'
            f'<div class="kwrap"><table class="ktable qtable"><tr><th>번호</th><th>구분</th><th>확인할 내용</th><th>상태</th></tr>'
            f'{rows}</table></div></details>')


def item_card_html(it):
    """항목 하나를 카드로. 판정 배지(색) · 이유 · 근거 조항 · 보완할 점 · 확인 항목."""
    label, (color, bg) = item_badge(it)
    src = item_source(it)
    badges = _pill(label, color, bg)
    if src == 'cache':
        badges += _small_badge('기억에서')
    elif src == 'mock':
        badges += _small_badge('시험용 모의 응답', warn=True)
    if it.get('fake_citations') or any(c.get('citation_problem') for c in (it.get('checks') or [])):
        badges += _small_badge('인용 확인 필요', warn=True)
    clause = it.get('cited_clause')
    reason = it.get('reason') or it.get('message') or ''
    miss = it.get('missing_points') or []
    miss_html = ''
    if miss:
        miss_html = ('<div class="qmiss"><span class="qmisslbl">보완할 점</span><ul>'
                     + ''.join(f'<li>{esc(m)}</li>' for m in miss) + '</ul></div>')
    detail = ''
    if it.get('detail') and it.get('status') in ('network_error', 'api_error', 'parse_error'):
        detail = f'<details class="qchk"><summary>기술적인 원인</summary><div class="qnote">{esc(it["detail"])}</div></details>'
    return (f'<div class="qcard" style="border-left-color:{color}">'
            f'<div class="qtop"><span class="rid" style="color:{color}">{esc(it.get("rule_id"))}</span>{badges}'
            f'<span class="qsrc">{("근거 " + esc(clause)) if clause else ""}</span></div>'
            f'<div class="qq">{esc(it.get("question"))}</div>'
            f'<div class="qreason">{esc(reason)}</div>{miss_html}{_checks_html(it)}{detail}</div>')


def summary_html(result):
    """결과 한 줄 요약: 판정 개수 · 비용 · 시간. (점수가 아니다 — 개수만 센다.)"""
    s = result.get('summary') or {}
    v = s.get('verdicts') or {}
    cost = result.get('cost') or {}
    timing = result.get('timing') or {}
    n_na = sum(1 for i in result.get('items', []) if i.get('status') == 'skipped_condition')
    parts = [f'{k} {v.get(k, 0)}' for k in ('충분', '보완 필요', '판단 불가')]
    if n_na:
        parts.append(f'해당 없음 {n_na}')
    if s.get('n_failed'):
        parts.append(f'판정 못 받음 {s["n_failed"]}')
    line2 = (f'호출 {cost.get("n_calls", 0)}회 · 기억에서 {cost.get("n_cached", 0)}회 · '
             f'실제 비용 ${float(cost.get("actual_usd") or 0):.4f}'
             + (f' · {float(timing.get("seconds") or 0):.1f}초' if timing else ''))
    return (f'<div class="qsum"><span class="qsumhead">AI 참고 판정</span> {esc(" · ".join(parts))}'
            f'<div class="qsumsub">{esc(line2)}</div></div>')


def items_html(result, with_summary=True):
    """결과 전체: (요약 줄) + 카드들(Q-01~Q-08 순서) + 안내 문구."""
    out = summary_html(result) if with_summary else ''
    out += ''.join(item_card_html(it) for it in result.get('items', []))
    if result.get('disclaimer'):
        out += f'<div class="qdisc">{esc(result["disclaimer"])}</div>'
    return out


def partial_items_html(items_by_rule):
    """실행 중 끝난 항목만 먼저 그릴 때(끝난 순서 그대로, 요약 없음)."""
    return ''.join(item_card_html(it) for it in items_by_rule.values())
