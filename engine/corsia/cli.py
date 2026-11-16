"""
전체 파이프라인 + 명령행 도구

    python -m corsia run    --dataset ./dataset --method BLOCK_OFF_ON
    python -m corsia verify --dataset ./dataset --documents dataset/documents.json
    python -m corsia full   --dataset ./dataset --documents dataset/documents.json
    python -m corsia golden --dataset ./dataset
"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

# 윈도우 한글 콘솔(cp949)에서 표현 못 하는 문자가 있어도 멈추지 않도록
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

from . import fuel_use, co2, offsetting, cef_reduction, final_offset
from .constants import r, to_dec
from .io_utils import load_csv, load_params, group_by_aircraft


def _dec_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError


def run_pipeline(dataset_dir, method='BLOCK_OFF_ON', flights_file='flights_clean.csv'):
    d = Path(dataset_dir)
    flights = load_csv(d / flights_file)
    params = load_params(d / 'params.csv')
    saf = load_csv(d / 'saf_batches.csv')
    aircraft = {a['registration']: a for a in load_csv(d / 'aircraft.csv')}

    year = int(params['reporting_year'])

    # ── MTOW 필터 (법 제6조①1 "초과") ──────────────────────────────
    eligible_regs = {reg for reg, a in aircraft.items()
                     if co2.is_obligor_aircraft(a['mtow_kg'])}
    excluded = set(aircraft) - eligible_regs

    # ── C-01~C-05 · 항공기별 시간순 계산 ───────────────────────────
    ordered, fuels = [], []
    for reg, legs in sorted(group_by_aircraft(flights).items()):
        if reg not in eligible_regs:
            continue
        vals = fuel_use.compute_sequence(legs, method)
        ordered.extend(legs)
        fuels.extend(vals)

    # ── C-06 · CO2 ────────────────────────────────────────────────
    agg = co2.annual_co2(ordered, fuels)
    gap_pct = co2.data_gap_ratio(ordered, fuels)

    # ── X-23 · EU-ETS 조정 ────────────────────────────────────────
    co2_each = []
    for f, fu in zip(ordered, fuels):
        try:
            co2_each.append(None if fu is None else co2.co2_from_fuel(fu, f['fuel_type']))
        except ValueError:
            co2_each.append(None)   # F-17 위반 편은 건너뛴다 (기록은 agg['errors'])
    eu_adj, eu_n = offsetting.eu_ets_adjustment(ordered, co2_each)
    # 상쇄의무량의 기준(OE_y)은 상쇄 대상 노선 배출량. 보고 총량과 다르다.
    oe_y = agg['offsetting_co2_t'] - eu_adj

    # ── C-07 · CERT 판정 ──────────────────────────────────────────
    # SARPs 2.2.1.3.1·지침 별표1 4a: 상쇄 대상 국제선 배출량 기준
    method_req = co2.monitoring_method_required(agg['offsetting_co2_t'], year)

    # ── C-08 / C-09 · 상쇄의무량 ──────────────────────────────────
    ogf_val = offsetting.ogf(oe_y, params.get(f'OE_B_{year}'))
    or_res = offsetting.offsetting_requirement(
        oe_y, year, params[f'SGF_{year}'], ogf_value=ogf_val)

    # ── C-10 · 적격연료 감축량 ────────────────────────────────────
    used_ids = {f['saf_batch_id'] for f in ordered if f.get('saf_batch_id')}
    batches = [b for b in saf if b['saf_batch_id'] in used_ids
               and b.get('is_error_case') != 'Y']
    saf_issues = cef_reduction.validate_batches(batches)
    er = cef_reduction.emission_reduction(batches)

    # ── C-11 / C-12 · 최종 상쇄의무량 ─────────────────────────────
    fo = final_offset.final_offsetting_requirement([or_res['OR_y_t']], [er['ER_y_t']])
    exempt = final_offset.exemption_check(prev_period_total_or=fo['FOR_t'])
    mat = final_offset.materiality(agg['total_co2_t'])   # 중요성은 보고 총 배출량 기준

    return {
        'meta': {'dataset': d.resolve().name, 'flights_file': flights_file,   # 전체 경로 대신 폴더 이름만
                 'method': method, 'reporting_year': year,
                 'aircraft_total': len(aircraft),
                 'aircraft_excluded_by_mtow': sorted(excluded)},
        'C06_emissions': {**agg, 'data_gap_pct': gap_pct,
                          'gap_over_limit': gap_pct > Decimal('5.0')},
        'X23_eu_ets': {'adjusted_t': eu_adj, 'flights': eu_n},
        'OE_y_t': oe_y,
        'C07_method_requirement': method_req,
        'C09_OGF': ogf_val,
        'C08_offsetting': or_res,
        'C10_cef': {'ER_y_t': er['ER_y_t'], 'batches': len(batches),
                    'issues': saf_issues},
        'C11_final': fo,
        'C12_exemption': exempt,
        'materiality': mat,
    }


def run_golden(dataset_dir):
    """golden_cases.csv 전건을 검산한다."""
    rows = load_csv(Path(dataset_dir) / 'golden_cases.csv')
    parsed = {}
    for row in rows:
        kv = {}
        for part in row['inputs'].split(';'):
            k, _, v = part.partition('=')
            kv[k.strip()] = v.strip()
        parsed[row['case_id']] = (row, kv)

    results = []

    def check(cid, got):
        row, _ = parsed[cid]
        exp = Decimal(row['expected_output_t'])
        ok = got is not None and Decimal(str(got)) == exp
        results.append({'case_id': cid, 'method': row['method'],
                        'expected': exp, 'got': got, 'pass': ok})

    # 방법 A - 시퀀스로 계산 (T는 급유 없으면 블록오프로 대체)
    seq_a = [
        {'uplift_tonnes': '89.3', 'tank_after_uplift_t': '94.6', 'tank_block_off_t': '94.5'},
        {'uplift_tonnes': '43.3', 'tank_after_uplift_t': '50.3', 'tank_block_off_t': '50.3'},
        {'uplift_tonnes': '26.9', 'tank_after_uplift_t': '32.7', 'tank_block_off_t': '32.7'},
        {'uplift_tonnes': '', 'tank_after_uplift_t': '9.6', 'tank_block_off_t': '9.5'},
        {'uplift_tonnes': '71.7', 'tank_after_uplift_t': '75.1', 'tank_block_off_t': '75.0'},
        {'uplift_tonnes': '', 'tank_after_uplift_t': '4.5', 'tank_block_off_t': '4.5'},
    ]
    for i, v in enumerate(fuel_use.compute_sequence(seq_a, 'METHOD_A')[:5], start=1):
        check(f'GA-{i}', v)

    # 방법 B
    b_rows = [('5.5', '8.5', '89.3'), ('8.5', '5.8', '43.3'), ('5.8', '9.7', '26.9'),
              ('9.7', '4.0', ''), ('4.0', '4.5', '71.7')]
    for i, (rp, rn, u) in enumerate(b_rows, start=1):
        check(f'GB-{i}', fuel_use.method_b(rp, rn, u or 0))

    # Block-off / Block-on
    c_rows = [('94.5', '8.5'), ('51.8', '5.8'), ('32.7', '9.7'), ('9.5', '4.0'), ('75.0', '4.5')]
    for i, (t, rr) in enumerate(c_rows, start=1):
        check(f'GC-{i}', fuel_use.block_off_on(t, rr))

    # Fuel Uplift - 배분 구간: [1], [2], [3,4], [5]
    check('GD-1', fuel_use.fuel_uplift_group('89.3', ['11.8'])[0])
    check('GD-2', fuel_use.fuel_uplift_group('43.3', ['6.5'])[0])
    g34 = fuel_use.fuel_uplift_group('26.9', ['3.1', '0.9'])
    check('GD-3', g34[0]); check('GD-4', g34[1])
    check('GD-5', fuel_use.fuel_uplift_group('71.7', ['9.5'])[0])

    # Block Hour
    for i, bh in enumerate(['11.8', '6.5', '3.1', '0.9', '9.5'], start=1):
        check(f'GE-{i}', fuel_use.block_hour_alloc('7.270', bh))

    # 상쇄의무량
    check('GO-1', offsetting.offsetting_requirement(5_000_000, 2023, '0.0')['OR_y_t'])
    check('GO-2', offsetting.offsetting_requirement(7_000_000, 2026, '0.150')['OR_y_t'])
    check('GO-3', offsetting.offsetting_requirement(
        11_000_000, 2034, '0.3108', ogf_value='0.5363')['OR_y_t'])

    passed = sum(1 for x in results if x['pass'])
    return {'total': len(results), 'passed': passed,
            'failed': len(results) - passed, 'results': results}


def _cmd_verify(a):
    from .verify import verify
    extra = {}
    if a.documents:
        extra = json.loads(Path(a.documents).read_text(encoding='utf-8'))
    res = verify(a.dataset, flights_file=a.flights, saf_file=a.saf,
                 method=a.method, **extra)
    c, s = res['computed'], res['summary']
    print('=' * 72)
    print(f"검증 대상 : {res['meta']['flights_file']}  "
          f"({res['meta']['reporting_year']}년, {res['meta']['compliance_period']})")
    print(f"모니터링  : {res['meta']['method']}")
    print('-' * 72)
    print(f"총 CO2        : {c['total_co2_t']:>15} t")
    print(f"EU-ETS 차감   : {c['eu_ets_adjusted_t']:>15} t")
    print(f"상쇄대상 배출 : {c['OE_y_t']:>15} t")
    print(f"상쇄의무량    : {c['OR_y_t']:>15} t")
    print(f"적격연료 감축 : {c['ER_y_t']:>15} t")
    print(f"최종 상쇄의무 : {c['FOR_t']:>15} t")
    print(f"데이터 갭     : {c['data_gap_pct']:>15} %")
    print('-' * 72)
    for f in res['findings']:
        if not a.all and f['status'] == 'PASS':
            continue
        print(f"[{f['status']:7s}] {f['rule_id']:6s} {f['severity']:4s} "
              f"{f['count']:>5}건  {(f.get('violation') or f['title'])[:38]}")
        if f['status'] == 'FAIL' and f['samples']:
            print(f"           └ {f['samples'][0]}")
    print('-' * 72)
    print(f"규칙 {s['total_rules']}건 중  PASS {s.get('PASS', 0)} / "
          f"FAIL {s.get('FAIL', 0)} / SKIPPED {s.get('SKIPPED', 0)} / "
          f"NOTICE {s.get('NOTICE', 0)}")
    print(f"검증의견 : {res['opinion']['result']}")
    print(f"사유     : {res['opinion']['reason']}")
    _print_score(res.get('score'))
    print('=' * 72)
    if a.out:
        Path(a.out).write_text(
            json.dumps(res, ensure_ascii=False, indent=2, default=_dec_default),
            encoding='utf-8')
        print(f'저장: {a.out}')
    return 0


def _print_score(sc):
    """종합점수는 보조 지표다. 판정 아래에 둔다."""
    if not sc or sc.get('overall') is None:
        return
    line = f"종합점수 : {sc['overall']}/100  (보조 지표)"
    if sc.get('capped'):
        line += f"  ← {sc['cap_reason']}"
    print(line)
    print('           ' + ' · '.join(
        f"{k} {v['score']}" for k, v in sc['by_layer'].items() if v['score'] is not None)
        + '   (계층별은 상한 보정 없음)')
    if sc.get('deductions'):
        top = sc['deductions'][:3]
        print('           감점 상위: ' + ', '.join(
            f"{d['rule_id']} −{d['points']}" for d in top)
            + (f" 외 {len(sc['deductions']) - len(top)}건" if len(sc['deductions']) > 3 else ''))


def _cmd_full(a):
    """계산 → 검증 → 종합점수를 한 번에. 웹 화면과 같은 엔진·같은 결과."""
    from .verify import verify
    extra = {}
    if a.documents:
        extra = json.loads(Path(a.documents).read_text(encoding='utf-8'))
    calc = run_pipeline(a.dataset, a.method, a.flights)
    res = verify(a.dataset, flights_file=a.flights, saf_file=a.saf,
                 method=a.method, **extra)
    c = res['computed']
    print('=' * 72)
    print(f"CORSIA 통합 실행  ({res['meta']['reporting_year']}년 · {a.flights})")
    print('=' * 72)
    print(f"최종 판정     : {res['opinion']['result']}")
    sc = res.get('score') or {}
    if sc.get('overall') is not None:
        print(f"종합점수      : {sc['overall']}/100 (보조 지표)"
              + (f"  ← {sc['cap_reason']}" if sc.get('capped') else ''))
    print(f"최종 상쇄의무 : {c['FOR_t']:,} t"
          + ('   ※ 검증을 통과하지 못한 데이터로 계산한 값' 
             if res['opinion']['result'] == '불만족' else ''))
    print('-' * 72)
    print(f"총 CO2 {c['total_co2_t']} t · 상쇄대상 {c['OE_y_t']} t · "
          f"데이터 갭 {c['data_gap_pct']}%")
    fails = [f for f in res['findings'] if f['status'] == 'FAIL']
    print(f"위반 {len(fails)}건 "
          f"(치명 {sum(1 for f in fails if f['severity'] == '치명')} · "
          f"중요 {sum(1 for f in fails if f['severity'] == '중요')} · "
          f"경미 {sum(1 for f in fails if f['severity'] == '경미')})")
    for f in fails[:10]:
        print(f"  [{f['severity']}] {f['rule_id']:6s} {f.get('violation') or f['title']}")
        print(f"           근거 {f['source']}")
    if len(fails) > 10:
        print(f"  ... 외 {len(fails) - 10}건")
    _print_score(sc)
    print('=' * 72)
    if a.out:
        Path(a.out).write_text(
            json.dumps({'calc': calc, 'verification': res},
                       ensure_ascii=False, indent=2, default=_dec_default),
            encoding='utf-8')
        print(f'저장: {a.out}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='corsia', description='CORSIA 계산엔진')
    sub = p.add_subparsers(dest='cmd', required=True)

    pr = sub.add_parser('run', help='전체 파이프라인 실행')
    pr.add_argument('--dataset', default='./dataset')
    pr.add_argument('--method', default='BLOCK_OFF_ON', choices=fuel_use.METHODS)
    pr.add_argument('--flights', default='flights_clean.csv')
    pr.add_argument('--out', default=None, help='결과 JSON 저장 경로')

    pg = sub.add_parser('golden', help='골든 케이스 검산')
    pg.add_argument('--dataset', default='./dataset')

    pv = sub.add_parser('verify', help='검증엔진 실행')
    pv.add_argument('--dataset', default='./dataset')
    pv.add_argument('--method', default='BLOCK_OFF_ON', choices=fuel_use.METHODS)
    pv.add_argument('--flights', default='flights_clean.csv')
    pv.add_argument('--saf', default='saf_batches.csv')
    pv.add_argument('--documents', default=None,
                    help='EMP/ER/EUCR 문서 JSON (없으면 문서 규칙은 SKIPPED)')
    pv.add_argument('--out', default=None)
    pv.add_argument('--all', action='store_true', help='PASS 규칙도 전부 출력')

    pf = sub.add_parser('full', help='계산 + 검증 + 종합점수 통합 실행')
    pf.add_argument('--dataset', default='./dataset')
    pf.add_argument('--method', default='BLOCK_OFF_ON', choices=fuel_use.METHODS)
    pf.add_argument('--flights', default='flights_clean.csv')
    pf.add_argument('--saf', default='saf_batches.csv')
    pf.add_argument('--documents', default=None)
    pf.add_argument('--out', default=None)

    a = p.parse_args(argv)

    if a.cmd == 'run':
        res = run_pipeline(a.dataset, a.method, a.flights)
        text = json.dumps(res, ensure_ascii=False, indent=2, default=_dec_default)
        if a.out:
            Path(a.out).write_text(text, encoding='utf-8')
            print(f'저장: {a.out}')
        print(text)
    elif a.cmd == 'verify':
        return _cmd_verify(a)
    elif a.cmd == 'full':
        return _cmd_full(a)
    else:
        res = run_golden(a.dataset)
        for x in res['results']:
            mark = 'PASS' if x['pass'] else 'FAIL'
            print(f"[{mark}] {x['case_id']:6s} {x['method']:14s} "
                  f"기대 {x['expected']}  결과 {x['got']}")
        print(f"\n{res['passed']}/{res['total']} 통과")
        return 0 if res['failed'] == 0 else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
