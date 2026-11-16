"""
검증엔진 본체
- 계산엔진을 안에서 재사용한다 (영 제8조제2항제3호가 독립 재계산을 명령)
- 규칙을 전부 돌린 뒤 ICAO 부속서 16 제4권 부록 6 3.10의 검증의견 3종으로 수렴시킨다
"""
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .. import fuel_use, co2, offsetting, cef_reduction, final_offset
from ..constants import (OPINION_SATISFACTORY, OPINION_WITH_COMMENTS,
                         OPINION_UNSATISFACTORY, r, to_dec)
from ..io_utils import load_csv, load_params, group_by_aircraft
from .rules import (get_registry, Finding, PASS, FAIL, SKIPPED, NOTICE, MissingInput,
                    CRITICAL, MAJOR, MINOR, BLOCKING, INFO)
from .violation_text import violation_text
from .scoring import compute_score
from .display import input_label

# 규칙 모듈을 import해야 REGISTRY가 채워진다
from . import format_threshold  # noqa: F401
from . import consistency       # noqa: F401
from . import qualitative       # noqa: F401
from . import documents_rules   # noqa: F401


def build_context(dataset_dir, flights_file='flights_clean.csv',
                  saf_file='saf_batches.csv', method='BLOCK_OFF_ON',
                  documents=None, emission_units=None, external_fuel_data=None):
    """검증에 필요한 모든 것을 한 번에 계산해 ctx로 만든다."""
    d = Path(dataset_dir)
    flights = load_csv(d / flights_file)
    params = load_params(d / 'params.csv')
    saf = load_csv(d / saf_file)
    aircraft = {a['registration']: a for a in load_csv(d / 'aircraft.csv')}
    year = int(params['reporting_year'])

    # ── 계산엔진 재사용 ────────────────────────────────────────────
    eligible = {reg for reg, a in aircraft.items()
                if co2.is_obligor_aircraft(a['mtow_kg'])}
    ordered, fuels = [], []
    for reg, legs in sorted(group_by_aircraft(flights).items()):
        vals = (fuel_use.compute_sequence(legs, method) if reg in eligible
                else [None] * len(legs))
        ordered.extend(legs)
        fuels.extend(vals)

    agg = co2.annual_co2(ordered, fuels)
    gap_pct = co2.data_gap_ratio(ordered, fuels)

    co2_each = []
    for f, fu in zip(ordered, fuels):
        try:
            co2_each.append(None if fu is None else co2.co2_from_fuel(fu, f['fuel_type']))
        except ValueError:
            co2_each.append(None)
    eu_adj, eu_n = offsetting.eu_ets_adjustment(ordered, co2_each)
    # 상쇄의무량의 기준(OE_y)은 상쇄 대상 노선 배출량. 보고 총량과 다르다.
    oe_y = agg['offsetting_co2_t'] - eu_adj

    ogf_val = offsetting.ogf(oe_y, params.get(f'OE_B_{year}'))
    or_res = offsetting.offsetting_requirement(
        oe_y, year, params[f'SGF_{year}'], ogf_value=ogf_val)

    used_ids = {f['saf_batch_id'] for f in ordered if f.get('saf_batch_id')}
    batches = [b for b in saf if b['saf_batch_id'] in used_ids]
    try:
        er = cef_reduction.emission_reduction(batches)
    except cef_reduction.MixedFuelClassError:
        er = cef_reduction.emission_reduction(batches, split_by_class=True)
    fo = final_offset.final_offsetting_requirement([or_res['OR_y_t']], [er['ER_y_t']])
    mat = final_offset.materiality(agg['total_co2_t'])   # 중요성은 보고 총 배출량 기준

    # ── 노선별 통계 (T-07, T-08, X-13, X-14) ──────────────────────
    stats = defaultdict(lambda: {'fuel': Decimal('0'), 'n': 0})
    for f, fu in zip(ordered, fuels):
        if fu is None or not co2.is_reportable(f):
            continue
        if f.get('dep_state') == f.get('arr_state'):
            continue   # 국제선이 아닌 편은 노선 통계에서 제외
        k = (f['dep_aerodrome'], f['arr_aerodrome'], (f.get('fuel_type') or '').upper())
        stats[k]['fuel'] += fu
        stats[k]['n'] += 1
    route_stats = []
    for (dep, arr, ft), v in sorted(stats.items()):
        route_stats.append({
            'dep': dep, 'arr': arr, 'fuel_type': ft, 'flights': v['n'],
            'total_fuel_t': r(v['fuel'], 1),
            'avg_fuel_per_flight': r(v['fuel'] / v['n'], 2),
        })

    # X-16 · 해당 연도 CORSIA 참여국 목록 (ICAO 「CORSIA States for Chapter 3 State Pairs」)
    corsia_states = None
    sp = d / f'corsia_states_{year}.csv'
    if sp.exists():
        corsia_states = {r['state']: r['participating'].strip().upper() != 'N'
                         for r in load_csv(sp)}

    period_start = 2021 + ((year - 2021) // 3) * 3
    return {
        'dataset': str(d), 'flights_file': flights_file, 'method': method,
        'year': year, 'period_start_year': period_start,
        'period_end_year': period_start + 2,
        'flights': ordered, 'fuel_by_flight': fuels, 'co2_by_flight': co2_each,
        'aircraft': aircraft, 'params': params,
        'saf_batches': saf, 'saf_used_ids': used_ids,
        'total_fuel_t': agg['total_fuel_t'], 'total_co2_t': agg['total_co2_t'],
        'total_flights': agg['counted_flights'],
        'offsetting_co2_t': agg['offsetting_co2_t'], 'offsetting_flights': agg['offsetting_flights'],
        'calc_errors': agg.get('errors', []),
        'data_gap_pct': gap_pct,
        'eu_ets_adjusted_t': eu_adj, 'eu_ets_flights': eu_n,
        'OE_y_t': oe_y, 'OGF': ogf_val,
        'offsetting': or_res, 'cef': er, 'final_offset': fo, 'materiality': mat,
        'route_stats': route_stats,
        'documents': documents, 'emission_units': emission_units,
        'external_fuel_data': external_fuel_data,
        'afbr_values': None,
        'corsia_states': corsia_states,
    }


def run_rules(ctx):
    findings = []
    for rl in get_registry():
        missing = [k for k in rl.needs if not ctx.get(k)]
        if missing:
            findings.append(_skipped(rl, missing))
            continue
        try:
            hits = rl.func(ctx) or []
        except MissingInput as e:
            findings.append(_skipped(rl, [str(e)]))
            continue
        except Exception as e:
            findings.append(Finding(
                rl.id, rl.layer, rl.severity, rl.title, rl.source, SKIPPED,
                note=f'규칙 실행 중 예외: {type(e).__name__}: {e}'))
            continue
        if not hits:
            status = PASS
        else:
            status = NOTICE if rl.severity == INFO else FAIL
        findings.append(Finding(
            rl.id, rl.layer, rl.severity, rl.title, rl.source,
            status, count=len(hits),
            samples=[_stringify(h) for h in hits[:rl.max_samples]]))
    # 화면 표시용 위반 문구. 규칙 제목(요건 문장)은 그대로 두고 표시만 바꾼다.
    for f in findings:
        f.violation = violation_text(f.rule_id, f.title)
    return findings


def _skipped(rl, missing):
    """판정 보류. 사유는 사람이 읽는 이름으로, 코드 이름은 missing_inputs에 따로 둔다."""
    f = Finding(rl.id, rl.layer, rl.severity, rl.title, rl.source, SKIPPED,
                note='판정에 필요한 자료 없음: ' + ', '.join(input_label(m) for m in missing))
    f.missing_inputs = list(missing)
    return f


def _stringify(d):
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


def resolve_opinion(findings):
    """SARPs App.6 3.10 - 만족 / 의견부 만족 / 불만족

    - 불만족: 중대한 허위진술·부적합(치명)이 있거나, 검증 범위가 너무 제한적이거나
              데이터를 충분히 신뢰할 수 없는 경우(차단)
    - 의견부 만족: 중대하지 않은 허위진술·부적합(중요·경미)만 있는 경우. 그 내용을 명시한다
    - 만족: 위반 사항 없음
    """
    failed = [f for f in findings if f.status == FAIL]
    blocking = [f for f in failed if f.severity == BLOCKING]
    if blocking:
        return OPINION_UNSATISFACTORY, ('검증 범위가 제한적이거나 데이터를 충분히 신뢰할 수 없음: '
                                        + ', '.join(f.rule_id for f in blocking))
    crit = [f for f in failed if f.severity == CRITICAL]
    if crit:
        return OPINION_UNSATISFACTORY, f'중대한 위반 {len(crit)}건: ' + ', '.join(
            f.rule_id for f in crit)
    minor = [f for f in failed if f.severity in (MAJOR, MINOR)]
    if minor:
        return OPINION_WITH_COMMENTS, f'중대하지 않은 위반 {len(minor)}건 명시: ' + ', '.join(
            f.rule_id for f in minor)
    return OPINION_SATISFACTORY, '위반 사항 없음'


def verify(dataset_dir, **kwargs):
    ctx = build_context(dataset_dir, **kwargs)
    findings = run_rules(ctx)
    opinion, reason = resolve_opinion(findings)
    score = compute_score(findings, opinion)

    by_status = defaultdict(int)
    for f in findings:
        by_status[f.status] += 1
    by_layer = defaultdict(lambda: defaultdict(int))
    for f in findings:
        by_layer[f.layer][f.status] += 1

    return {
        'meta': {'dataset': Path(ctx['dataset']).name, 'flights_file': ctx['flights_file'],
                 'method': ctx['method'], 'reporting_year': ctx['year'],
                 'compliance_period': f"{ctx['period_start_year']}-{ctx['period_end_year']}"},
        'computed': {
            'total_fuel_t': ctx['total_fuel_t'], 'total_co2_t': ctx['total_co2_t'],
            'offsetting_co2_t': ctx['offsetting_co2_t'],
            'eu_ets_adjusted_t': ctx['eu_ets_adjusted_t'],
            'OE_y_t': ctx['OE_y_t'], 'OGF': ctx['OGF'],
            'OR_y_t': ctx['offsetting']['OR_y_t'], 'ER_y_t': ctx['cef']['ER_y_t'],
            'FOR_t': ctx['final_offset']['FOR_t'],
            'data_gap_pct': ctx['data_gap_pct'],
            'materiality': ctx['materiality'],
            'calc_errors': ctx['calc_errors'],
            'route_stats': ctx['route_stats'],
        },
        'summary': {'total_rules': len(findings), **dict(by_status),
                    'by_layer': {k: dict(v) for k, v in by_layer.items()}},
        'opinion': {'result': opinion, 'reason': reason},
        'score': score,
        'findings': [f.as_dict() for f in findings],
    }
