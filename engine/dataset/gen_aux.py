import csv, os
from pathlib import Path
OUT = str(Path(__file__).resolve().parent)   # 이 스크립트가 있는 폴더(dataset/)에 생성
os.makedirs(OUT, exist_ok=True)


def w(path, header, rows):
    with open(f'{OUT}/{path}', 'w', newline='', encoding='utf-8-sig') as f:
        c = csv.writer(f); c.writerow(header); c.writerows(rows)


# ---------------------------------------------- 1) 골든 케이스 (ETM 공개 예시)
# 주의: ETM 표3-3~3-7은 서로 독립된 예시이므로 하나의 일관된 데이터셋이 아니다.
gold = [
    # case_id, method, flight_no, 입력1..4, expected_F, source
    ['GA-1', 'METHOD_A', 1, 'U=89.3;tank_before=5.3;T_N=94.6;block_off=94.5', 87.6, 'ETM 표3-3'],
    ['GA-2', 'METHOD_A', 2, 'U=43.3;tank_before=7.0;T_N=50.3;block_off=50.3', 44.5, 'ETM 표3-3'],
    ['GA-3', 'METHOD_A', 3, 'U=26.9;tank_before=5.8;T_N=32.7;block_off=32.7', 23.2, 'ETM 표3-3'],
    ['GA-4', 'METHOD_A', 4, 'U=;tank_before=9.6;T_N=9.6;block_off=9.5', 6.1, 'ETM 표3-3 (무급유 예외)'],
    ['GA-5', 'METHOD_A', 5, 'U=71.7;tank_before=3.4;T_N=75.1;block_off=75.0', 70.6, 'ETM 표3-3'],
    ['GB-1', 'METHOD_B', 1, 'R_prev=5.5;R_N=8.5;U=89.3', 86.3, 'ETM 표3-4'],
    ['GB-2', 'METHOD_B', 2, 'R_prev=8.5;R_N=5.8;U=43.3', 46.0, 'ETM 표3-4'],
    ['GB-3', 'METHOD_B', 3, 'R_prev=5.8;R_N=9.7;U=26.9', 23.0, 'ETM 표3-4'],
    ['GB-4', 'METHOD_B', 4, 'R_prev=9.7;R_N=4.0;U=', 5.7, 'ETM 표3-4 (무급유)'],
    ['GB-5', 'METHOD_B', 5, 'R_prev=4.0;R_N=4.5;U=71.7', 71.2, 'ETM 표3-4'],
    ['GC-1', 'BLOCK_OFF_ON', 1, 'T_N=94.5;R_N=8.5', 86.0, 'ETM 표3-5'],
    ['GC-2', 'BLOCK_OFF_ON', 2, 'T_N=51.8;R_N=5.8', 46.0, 'ETM 표3-5'],
    ['GC-3', 'BLOCK_OFF_ON', 3, 'T_N=32.7;R_N=9.7', 23.0, 'ETM 표3-5'],
    ['GC-4', 'BLOCK_OFF_ON', 4, 'T_N=9.5;R_N=4.0', 5.5, 'ETM 표3-5'],
    ['GC-5', 'BLOCK_OFF_ON', 5, 'T_N=75.0;R_N=4.5', 70.5, 'ETM 표3-5'],
    ['GD-1', 'FUEL_UPLIFT', 1, 'U=89.3;BH=11.8;allocation_group=1', 89.3, 'ETM 표3-6'],
    ['GD-2', 'FUEL_UPLIFT', 2, 'U=43.3;BH=6.5;allocation_group=2', 43.3, 'ETM 표3-6'],
    ['GD-3', 'FUEL_UPLIFT', 3, 'U=26.9;BH=3.1;allocation_group=3', 20.8, 'ETM 표3-6 (N 자신도 배분)'],
    ['GD-4', 'FUEL_UPLIFT', 4, 'U=;BH=0.9;allocation_group=3', 6.1, 'ETM 표3-6'],
    ['GD-5', 'FUEL_UPLIFT', 5, 'U=71.7;BH=9.5;allocation_group=4', 71.7, 'ETM 표3-6'],
    ['GE-1', 'BLOCK_HOUR', 1, 'AFBR=7.270;BH=11.8', 85.8, 'ETM 표3-7'],
    ['GE-2', 'BLOCK_HOUR', 2, 'AFBR=7.270;BH=6.5', 47.3, 'ETM 표3-7'],
    ['GE-3', 'BLOCK_HOUR', 3, 'AFBR=7.270;BH=3.1', 22.5, 'ETM 표3-7'],
    ['GE-4', 'BLOCK_HOUR', 4, 'AFBR=7.270;BH=0.9', 6.5, 'ETM 표3-7'],
    ['GE-5', 'BLOCK_HOUR', 5, 'AFBR=7.270;BH=9.5', 69.1, 'ETM 표3-7'],
    ['GO-1', 'OFFSETTING', 2023, 'OE=5000000;SGF=0.0;pct_S=1.0;pct_O=0.0', 0, 'ETM 그림4-1'],
    ['GO-2', 'OFFSETTING', 2026, 'OE=7000000;SGF=0.150;pct_S=1.0;pct_O=0.0', 1050000, 'ETM 그림4-3'],
    ['GO-3', 'OFFSETTING', 2034, 'OE=11000000;SGF=0.3108;OGF=0.5363;pct_S=0.85;pct_O=0.15', 3790875, 'ETM 그림4-4'],
]
w('golden_cases.csv',
  ['case_id', 'method', 'flight_or_year', 'inputs', 'expected_output_t', 'source'], gold)

# ---------------------------------------------- 2) 경계·국내규칙 케이스
bound = [
    ['KR-01', 'DENSITY', '표준밀도 0.8kg/L 적용', 'uplift_volume_l=50000', '40.0 t', '지침 제3조④'],
    ['KR-02', 'AFBR_ROUND', 'AFBR 소수점 3자리 반올림', 'sum_U=1000.0;sum_BH=137.55', '7.270', '지침 별표2 제5호 2) 주3'],
    ['KR-03', 'FOR_NEGATIVE', 'FOR 음수 → 0', 'sum_OR=1000;sum_ER=1500', '0', '지침 별표6 제1호'],
    ['KR-04', 'FOR_ROUND', 'FOR 톤단위 올림 (ICAO)', 'raw=3790874.4', '3790875', 'SARPs 3.4.4 (지침 제9조①은 반올림)'],
    ['KR-05', 'EXEMPT_3000', '3천톤 미만 면제', 'sum_OR=2999', '면제(영 제9조①1)', '영 제9조①1'],
    ['KR-06', 'OGF_NEW', '2019 실적 없는 신규 운영사', 'OE_y=50000;OE_B=10000', 'OGF=0.8000', '지침 별표6 제4호'],
    ['KR-06b', 'OGF_CHECK', 'OGF 단위 검산', 'OE_y=11000000;OE_B=5100700', 'OGF=0.5363 (배수 2.156 아님)', '지침 별표6 제4호'],
    ['KR-06c', 'OGF_GUARD', '0 나누기 가드', 'OE_y=0', '예외 발생', '구현 방어'],
    ['KR-07', 'EU_ETS', 'EU 역내 구간 제외', 'eu_ets_offset_flag=1 구간 합계', 'OE에서 차감', '지침 제9조③'],
    ['KR-08a', 'CERT_49K', '5만톤 미만 → CERT 가능', 'annual_CO2=49000', 'CERT 허용', '지침 제3조③'],
    ['KR-08b', 'CERT_51K', '5만톤 초과 → 연료사용량 방법 의무', 'annual_CO2=51000', '별표2 의무', '지침 제3조③ 단서'],
    ['KR-08c', 'CERT_50K', '정확히 5만톤 (경계)', 'annual_CO2=50000', '"이상"이므로 별표2 의무', '지침 제3조③ 단서'],
    ['KR-09', 'CERT_1920', '2019~2020 구간은 50만톤 기준', 'year=2019;annual_CO2=400000', 'CERT 허용', '지침 별표1 4-a'],
    ['KR-10a', 'GAP_4_96', '갭 4.96% → 반올림 5.0%', 'gap_ratio=0.0496', '5.0% (초과 아님)', '지침 별표4 6-b1'],
    ['KR-10b', 'GAP_5_04', '갭 5.04% → 반올림 5.0%', 'gap_ratio=0.0504', '5.0% (판정 주의)', '지침 별표4 6-b1'],
    ['KR-10c', 'GAP_5_1', '갭 5.1% → 초과', 'gap_ratio=0.051', '초과 플래그', 'T-04'],
    ['KR-11', 'CEF_MIXED', 'Jet 계열과 AvGas 계열 SAF 혼재', 'FCF 3.16 + 3.10 동시', '예외 또는 분리계산', '지침 별표6 제5호'],
    ['KR-12', 'MTOW_5700', 'MTOW 정확히 5,700kg', 'mtow_kg=5700', '대상 제외("초과")', '법 제6조①1'],
    ['KR-13', 'THRESHOLD_10K', '연간 정확히 10,000t', 'annual_CO2=10000', '이행의무자 지정("이상")', '법 제6조①1'],
    ['KR-14', 'MATERIALITY', 'ICAO 중요성 임계치', 'annual_CO2=1899987', '2% (50만 t 초과)', 'SARPs App.6 3.5.1'],
    ['KR-15', 'REPORT_LEVEL', '국가쌍·공항쌍 둘 다 작성', 'level_5_1=Y;level_5_2=Y', '오류(택일 위반)', '지침 별표4 5-a'],
    ['KR-16', 'CEF_CALC', '적격연료 감축량 검산', 'MS=100;FCF=3.16;L_CEF=45;LC=89', '156.22 t', '지침 별표6 제5호'],
]
w('boundary_cases.csv',
  ['case_id', 'category', 'description', 'inputs', 'expected', 'source'], bound)

# ---------------------------------------------- 3) SAF 배치
saf = [
    ['SAF-2024-001', 'HEFA-SPK', 'ISCC CORSIA', 'ISCC-CORSIA-2024-0041', 1850.0,
     'JET-A1', 3.16, 'DEFAULT', 24.0, 89, '2024-03-14', '공급사 A(가상)', 'N'],
    ['SAF-2024-002', 'ATJ-SPK', 'RSB CORSIA', 'RSB-CORSIA-2024-0117', 920.0,
     'JET-A1', 3.16, 'ACTUAL', 18.5, 89, '2024-08-02', '공급사 B(가상)', 'N'],
    ['SAF-2024-003', 'LCAF', 'ISCC CORSIA', 'ISCC-CORSIA-2024-0208', 410.0,
     'JET-A1', 3.16, 'DEFAULT', 81.0, 89, '2024-11-20', '공급사 C(가상)', 'N'],
    ['SAF-2024-901', 'HEFA-SPK', 'ISCC CORSIA', 'ISCC-CORSIA-2024-0041', 300.0,
     'AVGAS', 3.10, 'DEFAULT', 24.0, 95, '2024-05-10', '공급사 A(가상)', 'Y'],
]
SAF_HEADER = ['saf_batch_id', 'pathway', 'certification_scheme', 'certificate_no', 'mass_tonnes',
              'equivalent_fuel_type', 'fcf', 'lca_value_source', 'l_cef_gco2e_mj', 'lc_gco2e_mj',
              'blend_date', 'supplier', 'double_claimed_elsewhere', 'is_error_case']
# 정상본: 모든 배치 double_claimed_elsewhere = N
w('saf_batches.csv', SAF_HEADER, [r[:12] + ['N'] + r[12:] for r in saf])
# 오류주입본: SAF-2024-002를 타 제도에 이미 감축실적으로 쓴 것으로 표시 (X-24)
w('saf_batches_dirty.csv', SAF_HEADER,
  [r[:12] + ['Y' if r[0] == 'SAF-2024-002' else 'N'] + r[12:] for r in saf])

# ---------------------------------------------- 4) 산정 파라미터
params = [
    ['reporting_year', '2024', '이행연도', '-'],
    ['compliance_period', '2024-2026', '이행기간 2차 (2021년 기산 3년 단위)', '영 제9조①1'],
    ['pct_S_2024', '1.00', '부문별 백분율', '지침 별표6 제3호'],
    ['pct_O_2024', '0.00', '개별 백분율', '지침 별표6 제3호'],
    ['SGF_2024', '0.15405257', '부문 성장계수 - ICAO 2024년 공식값(「CORSIA Annual SGF」 4th ed. Rev.1, 2025-12-12)', 'ICAO CORSIA Annual SGF 4ed Rev.1'],
    ['OE_2019_total', '1120000', '2019년 총 국제항공 CO2 (t) - 가상', '-'],
    ['OE_B_2024', '952000', '2019년 배출량의 85%', '지침 별표6 제4호'],
    ['FCF_JET', '3.16', 'Jet-A/A1/TS-1/No.3', '영 별표3 제1호'],
    ['FCF_AVGAS', '3.10', 'AvGas/Jet-B', '영 별표3 제1호'],
    ['DENSITY_STD', '0.8', 'kg/L', '지침 제3조④'],
    ['LC_JET', '89', 'gCO2e/MJ', '지침 별표6 제5호'],
    ['LC_AVGAS', '95', 'gCO2e/MJ', '지침 별표6 제5호'],
    ['THRESHOLD_OBLIGOR', '10000', 't, 이상', '법 제6조①1'],
    ['THRESHOLD_CERT', '50000', 't, 이상이면 별표2 의무', '지침 제3조③'],
    ['THRESHOLD_EXEMPT', '3000', 't, 미만이면 면제', '영 제9조①1'],
    ['MTOW_MIN', '5700', 'kg, 초과', '법 제6조①1'],
    ['DATAGAP_LIMIT', '0.05', '5%', '지침 별표4 6-b'],
    ['MATERIALITY_STANDARD', 'ICAO', 'ICAO 부속서 16 제4권 부록 6 3.5.1 (50만 t 초과 2% / 이하 5%)', 'SARPs App.6 3.5.1'],
    ['REPORTING_LEVEL', 'AERODROME_PAIR', 'STATE_PAIR 또는 AERODROME_PAIR 택1', '지침 별표4 5-a'],
]
w('params.csv', ['key', 'value', 'description', 'source'], params)

# ---------------------------------------------- 5) 2024년 CORSIA 참여국 목록 (X-16)
# 출처: ICAO 「CORSIA States for Chapter 3 State Pairs」 4th ed. Rev.1 (2024년 적용, 126개국)
# 우리 데이터에 등장하는 국가만 수록. 2026-09-29 원문 PDF(2023년 10월 발행)와 대조 완료.
states = [
    ['KR', '대한민국', 'Y', '2024년 목록(126개국)에 있음'],
    ['US', '미국', 'Y', '2024년 목록(126개국)에 있음'],
    ['FR', '프랑스', 'Y', '2024년 목록(126개국)에 있음'],
    ['DE', '독일', 'Y', '2024년 목록(126개국)에 있음'],
    ['JP', '일본', 'Y', '2024년 목록(126개국)에 있음'],
    ['SG', '싱가포르', 'Y', '2024년 목록(126개국)에 있음'],
    ['TH', '태국', 'Y', '2024년 목록(126개국)에 있음'],
    ['TW', '대만', 'N', '2024년 목록(126개국)에 없음. ICAO 비회원국 - 보고 대상이지만 상쇄 대상 아님'],
    ['CN', '중국', 'N', '2024년 목록(126개국)에 없음'],
]
w('corsia_states_2024.csv', ['state', 'name', 'participating', 'note'], states)

print('done')
