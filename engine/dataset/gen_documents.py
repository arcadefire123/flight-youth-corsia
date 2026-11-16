"""
문서 단위 샘플 생성 (EMP / ER / EUCR + 말소 배출권)
계산엔진 결과와 정합적인 정상본과, 문서 규칙을 걸리게 하는 오류본을 만든다.
"""
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

# 이 스크립트는 engine/dataset/ 안에 둔다. 데이터는 같은 폴더, 엔진(corsia)은 한 단계 위.
HERE = Path(__file__).resolve().parent
OUT = HERE if (HERE / 'params.csv').exists() else HERE / 'dataset'
ENGINE_ROOT = OUT.parent
sys.path.insert(0, str(ENGINE_ROOT))
from corsia.verify.engine import build_context  # noqa: E402
from corsia.constants import TEMPLATE_VERSION, RECORDS_REQUIRED  # noqa: E402
from corsia.co2 import is_reportable, is_offsetting  # noqa: E402
OPERATOR = '한빛항공 주식회사'


def dec(v):
    return float(v) if isinstance(v, Decimal) else v


def build(ctx):
    # 공항쌍 집계 (지침 별표4 5-a: 국가쌍·공항쌍 중 택1 → 공항쌍 선택)
    agg = defaultdict(lambda: {'fuel': Decimal('0'), 'co2': Decimal('0'), 'n': 0})
    for f, fu, c in zip(ctx['flights'], ctx['fuel_by_flight'], ctx['co2_by_flight']):
        if fu is None or c is None or not is_reportable(f):
            continue
        k = (f['dep_aerodrome'], f['arr_aerodrome'], (f.get('fuel_type') or '').upper())
        agg[k]['offsetting'] = is_offsetting(f)
        agg[k]['fuel'] += fu
        agg[k]['co2'] += c
        agg[k]['n'] += 1

    pairs, tot_co2, tot_n = [], Decimal('0'), 0
    for (dep, arr, ft), v in sorted(agg.items()):
        co2_t = v['co2'].quantize(Decimal('0.1'))
        pairs.append({'dep_aerodrome': dep, 'arr_aerodrome': arr, 'fuel_type': ft,
                      'flights': v['n'], 'fuel_t': float(v['fuel'].quantize(Decimal('0.1'))),
                      'co2_t': float(co2_t), 'offsetting': v['offsetting']})
        tot_co2 += co2_t
        tot_n += v['n']

    forq = ctx['final_offset']['FOR_t']
    yr0 = ctx['year']
    address = {'street': '인천광역시 중구 공항로 272', 'city': '인천', 'postal_code': '22382', 'country': 'KR'}
    aoc = {'number': 'KR-AOC-2015-0123', 'issuing_authority': '국토교통부', 'issue_date': '2015-03-02'}
    eligible = sorted(reg for reg, a in ctx['aircraft'].items() if int(a['mtow_kg']) > 5700)
    fuel_types_used = sorted({(f.get('fuel_type') or '').upper() for f in ctx['flights']
                              if is_reportable(f)})
    used_batches = sorted(ctx['saf_used_ids'])
    er_t = float(ctx['cef']['ER_y_t'])
    verifier = {'body': '한국항공탄소검증원(가상)', 'verifiers': ['김검증(팀장)', '이심사']}
    docs = {
        'EMP': {
            'operator_name': OPERATOR,
            'version': '2.0',
            'approved_date': f"{ctx['year'] - 1}-11-15",   # 이행연도 직전 해에 승인
            'monitoring_method': ctx['method'],
            'operator_address': address,
            'aoc': aoc,
            'aircraft_identification': 'ICAO_DESIGNATOR',
            'icao_designator': 'HBA',
            'fuel_types': ['JET-A1'],
            'version_history': [
                {'version': '1.0', 'date': f"{yr0 - 5}-12-10", 'changes': []},
                {'version': '2.0', 'date': f"{yr0 - 1}-11-15",
                 'changes': [{'category': '연료 모니터링 방법 및 절차',
                              'detail': '방법 A → Block-off / Block-on 전환', 'approved': True}]},
            ],
            'records_retained': list(RECORDS_REQUIRED),
            'data_management':
                '운항관리시스템(OCS)에서 항공편별 블록오프·블록온 연료량을 자동 수집하여 '
                '익일 02:00 배치로 배출량관리DB에 적재한다. 운항본부 운항관리팀이 1차 확인하고, '
                '안전환경실 탄소관리파트가 월 단위로 급유전표와 대조한 뒤 승인한다. '
                '원천자료는 10년간 보관한다.',
            'data_gap_procedure':
                '일 단위 적재 시 필수 항목 결측을 자동 탐지하여 담당자에게 통보하고, '
                '1차 출처(OCS) 재조회 → 2차 출처(급유전표, technical log) 재구성 순으로 복구한다. '
                '복구 불가 항공편은 갭으로 분류하고 ICAO CERT로 추정하며, '
                '갭 비율이 분기 누적 3%를 넘으면 원인분석 보고를 실시하여 연 5% 이하를 유지한다.',
            'route_determination':
                '비행계획서 제7항 항공기 식별과 출·도착 공항의 국가코드를 대조하여 국제운항을 판별한다. '
                '상쇄의무 대상 여부는 ICAO가 공표하는 CORSIA States for Chapter 3 State Pairs 목록과 '
                '대조하여 결정하며, 제18항 STS/HUM·HOSP·MEDEVAC·FFR 표기 항공편은 모니터링에서 제외한다.',
            'measurement_timing':
                'Block-off / Block-on 방법을 적용한다. 블록오프 연료량은 마지막 도어 폐쇄 후 '
                '첫 엔진 시동 직전 FQIS 판독값을, 블록온 잔량은 마지막 엔진 정지 직후 판독값을 사용한다. '
                'ACARS로 자동 전송되며 전송 실패 시 technical log 수기 기록을 대체 사용한다.',
            'risk_assessment':
                'FQIS 오차, ACARS 전송 누락, 국내선·국제선 구분 오류, 밀도 적용 불일치를 '
                '주요 위험으로 식별하였다. 각 위험에 대해 월 단위 샘플 대조, 전송 실패 알림, '
                '노선 마스터 이중 확인, 급유업체 밀도자료 수령 절차를 통제활동으로 운영한다.',
        },
        'ER': {
            'operator_name': OPERATOR,
            'reporting_year': ctx['year'],
            'emp_version': '2.0',
            'template_version': TEMPLATE_VERSION,
            'issue_date': f"{yr0 + 1}-03-20",
            'operator_address': address,
            'aoc': aoc,
            'aircraft_identification': 'ICAO_DESIGNATOR',
            'icao_designator': 'HBA',
            'verification_body': verifier['body'],
            'monitoring_method': ctx['method'],
            'fuel_mass_by_type': {ft: float(ctx['total_fuel_t']) for ft in fuel_types_used},
            'total_fuel_t': float(ctx['total_fuel_t']),
            'offsetting_co2_t': float(sum(Decimal(str(p['co2_t'])) for p in pairs if p['offsetting'])),
            'offsetting_flights': sum(p['flights'] for p in pairs if p['offsetting']),
            'density_method': 'STANDARD',
            'data_gap_pct': float(ctx['data_gap_pct']),
            'aircraft': eligible,
            'cert_info': {'used': False},
            'cef_claim': {'batches': used_batches, 'ER_t': er_t},
            'aerodrome_pairs': pairs,
            'state_pairs': None,
            'total_co2_t': float(tot_co2),
            'total_flights': tot_n,
            'density_consistency_confirmed': True,
            'data_gap_explanation': '',
        },
        'EUCR': {
            'operator_name': OPERATOR,
            'compliance_period': f"{ctx['period_start_year']}-{ctx['period_end_year']}",
            'total_final_offsetting_requirement': forq,
            'total_cancelled_units': forq,
            'operator_address': address,
        },
        # 별지 제3호서식 CORSIA 적격연료 사용신고서
        'CEF_DECL': {
            'operator_name': OPERATOR,
            'batches': [{'saf_batch_id': b['saf_batch_id'], 'mass_tonnes': float(b['mass_tonnes'])}
                        for b in ctx['saf_batches'] if b['saf_batch_id'] in ctx['saf_used_ids']],
            'total_ER_t': er_t,
        },
        # 배출량검증보고서 (영 제8조③ 5개 항목 + X-03 대조용 수치)
        'VR': {
            'template_version': TEMPLATE_VERSION,
            'verifier': verifier,
            'criteria': 'ICAO 부속서 16 제4권 부록 6, 환경기술매뉴얼(Doc 9501) 제4권 3.3, 승인된 모니터링 계획 v2.0',
            'findings_and_actions':
                '표본 점검 결과 중대한 허위진술 및 부적합은 발견되지 않았다. 급유전표와 운항관리시스템 간 '
                '단위 표기 불일치 2건을 발견하여 운영사에 통보하였고, 운영사는 보고서 제출 전 수정을 완료하였다. '
                '미해결 사항은 없다.',
            'verification_opinion':
                '검증팀은 합리적 확신 수준에서 배출량보고서를 검증하였으며, 보고 배출량과 검증기관 재산정값의 '
                '차이가 중요성 임계치(2%) 미만이고 중대한 부적합이 없으므로 "만족(verified as satisfactory)"으로 검증한다.',
            'other': '해당 없음',
            'verified_total_fuel_t': float(ctx['total_fuel_t']),
            'verified_total_flights': tot_n,
        },
        # 상쇄의무이행검증보고서 (영 제10조③ 5개 항목)
        'VR_EUCR': {
            'verifier': verifier,
            'criteria': 'ICAO 부속서 16 제4권 부록 6 3.3.2, 배출권 등록부 말소 기록',
            'findings_and_actions': '말소 배출권의 일련번호·수량을 등록부 기록과 전수 대조하였으며 불일치 사항은 없다.',
            'verification_opinion': '말소 총량이 통보된 최종 상쇄의무량과 일치하므로 "만족"으로 검증한다.',
            'other': '해당 없음',
        },
    }

    # 말소 배출권 3배치. 수량을 고정값으로 두면 상쇄의무량이 바뀔 때 음수가 되거나
    # 오류 주입이 무효가 된다(SGF 교체 때 실제로 발생). 반드시 FOR에 비례해 만든다.
    q1 = forq * 60 // 100
    q2 = forq * 30 // 100
    q3 = forq - q1 - q2            # 합계가 정확히 FOR이 되도록 나머지
    def serial(prefix, start, qty):
        return f'{prefix}{start:07d}', f'{prefix}{start + qty - 1:07d}'
    s1, e1 = serial('ACR', 1, q1)
    s2, e2 = serial('ACR', q1 + 5, q2)           # 번호가 이어지지 않는 두 번째 블록
    s3, e3 = serial('CAR', 1, q3)
    yr = ctx['period_end_year'] + 1
    units = [
        {'program': 'ACR', 'serial_start': s1, 'serial_end': e1,
         'quantity': q1, 'cancel_date': f"{yr}-01-20"},
        {'program': 'ACR', 'serial_start': s2, 'serial_end': e2,
         'quantity': q2, 'cancel_date': f"{yr}-01-20"},
        {'program': 'CAR', 'serial_start': s3, 'serial_end': e3,
         'quantity': q3, 'cancel_date': f"{yr}-01-25"},
    ]
    assert sum(u['quantity'] for u in units) == forq and all(u['quantity'] > 0 for u in units)
    return docs, units


def make_dirty(docs, units):
    d = json.loads(json.dumps(docs))
    u = json.loads(json.dumps(units))
    log = []

    d['ER']['operator_name'] = '한빛에어 주식회사'
    log.append(['ER.operator_name', 'X-01', 'EMP와 법인명 불일치'])

    d['ER']['state_pairs'] = [{'dep_state': 'KR', 'arr_state': 'US',
                               'flights': 100, 'co2_t': 1000.0}]
    log.append(['ER.state_pairs', 'X-09', '국가쌍·공항쌍을 둘 다 작성'])

    d['ER']['aerodrome_pairs'][0]['co2_t'] += 5000.0
    log.append(['ER.aerodrome_pairs[0]', 'X-10', '노선 합계가 총계와 불일치'])

    d['ER']['total_flights'] += 7
    log.append(['ER.total_flights', 'X-11', '편수 합계 불일치'])

    d['ER']['total_co2_t'] = float(d['ER']['total_co2_t']) * 1.08
    log.append(['ER.total_co2_t', 'X-12', '보고 배출량이 재계산값과 8% 차이 (중요성 초과)'])

    d['ER']['density_consistency_confirmed'] = False
    log.append(['ER.density_consistency_confirmed', 'X-17', '밀도 일관성 확인란 부(否)'])

    d['EUCR']['total_final_offsetting_requirement'] = \
        int(d['EUCR']['total_final_offsetting_requirement']) - 12000
    log.append(['EUCR.total_final_offsetting_requirement', 'X-07', '국가 통보값과 불일치'])

    d['EMP']['data_management'] = ''
    log.append(['EMP.data_management', 'Q-01', '서술 항목이 비어 있음'])

    # 부족분을 '살짝'만 두면, 오류본 항공편과 함께 돌릴 때 계산 FOR 자체가 줄어들어
    # 주입이 무효가 된다(실제로 발생). 어느 조합에서도 걸리도록 약 25% 부족하게 만든다.
    u[1]['quantity'] = u[1]['quantity'] // 2
    u[2]['quantity'] = 1
    forq = docs['EUCR']['total_final_offsetting_requirement']
    assert sum(x['quantity'] for x in u) < forq * 0.8
    log.append(['units[1..2].quantity', 'X-08', '말소 총량이 최종 상쇄의무량의 약 75%에 불과'])

    u[1]['serial_start'] = 'ACR0000500'   # 첫 블록 범위 안쪽 번호 → 오름차순 위반
    log.append(['units[1].serial_start', 'F-21', '동일 프로그램 내 오름차순 위반'])

    d['EUCR']['operator_name'] = ''
    log.append(['EUCR.operator_name', 'F-01', '상쇄의무이행보고서 법인명 비어 있음'])
    d['ER']['aircraft_identification'] = 'CALLSIGN'
    log.append(['ER.aircraft_identification', 'F-02', '허용되지 않은 항공기 식별정보 선택값'])
    d['EMP']['aoc']['number'] = ''
    log.append(['EMP.aoc.number', 'F-03', 'AOC 번호 누락'])
    del d['ER']['cert_info']
    log.append(['ER.cert_info', 'F-04', '필수 항목(CERT 정보) 누락'])
    d['VR']['criteria'] = ''
    log.append(['VR.criteria', 'F-05', '검증보고서 검증기준 누락'])
    del d['EUCR']['total_cancelled_units']
    log.append(['EUCR.total_cancelled_units', 'F-06', '말소 배출권 총량 누락'])
    d['VR_EUCR']['findings_and_actions'] = ''
    log.append(['VR_EUCR.findings_and_actions', 'F-07', '상쇄의무이행검증보고서 발견사항 누락'])
    d['VR']['template_version'] = 'ETM Vol.IV Appendix 1 (3rd edition)'
    log.append(['VR.template_version', 'F-09', '구버전 서식 사용'])
    d['ER']['issue_date'] = f"{d['ER']['reporting_year']}-12-15"
    log.append(['ER.issue_date', 'F-10', '보고연도가 끝나기 전에 작성된 보고서'])
    d['EMP']['version_history'].append({'version': '2.1', 'date': f"{d['ER']['reporting_year']}-06-01",
        'changes': [{'category': '자료관리', 'detail': '배출량관리DB 교체', 'approved': False}]})
    log.append(['EMP.version_history', 'F-26', '변경승인 대상 변경을 승인 없이 적용'])
    d['EMP']['records_retained'] = [r for r in d['EMP']['records_retained'] if '지속가능항공연료' not in r]
    log.append(['EMP.records_retained', 'F-28', 'SAF 사용 이력 미보관'])
    d['ER']['operator_address'] = dict(d['ER']['operator_address'], city='서울')
    log.append(['ER.operator_address.city', 'X-02', '주소 불일치'])
    d['VR']['verified_total_fuel_t'] = d['VR']['verified_total_fuel_t'] - 1200.0
    log.append(['VR.verified_total_fuel_t', 'X-03', '검증기관 확인 연료량이 보고서와 다름'])
    d['ER']['monitoring_method'] = 'METHOD_A'
    log.append(['ER.monitoring_method', 'X-04', '승인된 모니터링 방법과 다른 방법 보고'])
    d['ER']['fuel_mass_by_type']['JET-B'] = 12.5
    log.append(['ER.fuel_mass_by_type', 'X-05', 'EMP에 없는 연료종류(JET-B) 보고'])
    d['CEF_DECL']['total_ER_t'] = d['CEF_DECL']['total_ER_t'] + 300.0
    log.append(['CEF_DECL.total_ER_t', 'X-06', '별지3 감축량 합계가 배출량보고서와 다름'])
    return d, u, log


def main():
    ctx = build_context(OUT)
    docs, units = build(ctx)
    (OUT / 'documents.json').write_text(
        json.dumps({'documents': docs, 'emission_units': units,
                    'external_fuel_data': {'purchased_fuel_t': float(ctx['total_fuel_t'])}},
                   ensure_ascii=False, indent=2), encoding='utf-8')

    dd, du, log = make_dirty(docs, units)
    (OUT / 'documents_dirty.json').write_text(
        json.dumps({'documents': dd, 'emission_units': du,
                    'external_fuel_data': {'purchased_fuel_t': float(ctx['total_fuel_t']) * 1.20}},
                   ensure_ascii=False, indent=2), encoding='utf-8')

    import csv
    with open(OUT / 'document_error_log.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['location', 'rule_id', 'description'])
        w.writerows(log)
        w.writerow(['external_fuel_data.purchased_fuel_t', 'X-26', '구매 연료량과 20% 차이'])

    print('documents.json / documents_dirty.json 생성')
    print('공항쌍 노선 수:', len(docs['ER']['aerodrome_pairs']))
    print('총 CO2(문서):', docs['ER']['total_co2_t'], '/ 총 편수:', docs['ER']['total_flights'])
    print('FOR:', docs['EUCR']['total_final_offsetting_requirement'])
    print('문서 오류 주입:', len(log) + 1, '건')


if __name__ == '__main__':
    main()
