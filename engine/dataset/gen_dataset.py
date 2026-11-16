"""
CORSIA 가상 데이터셋 생성기 v1
- 가상 항공사 1개(한빛항공/HBA), 이행연도 2024
- 정상본(clean) / 오류주입본(dirty) / 골든케이스 / 경계케이스 생성
결정론적: SEED 고정 → 몇 번 돌려도 같은 파일
"""
import csv, os, random, datetime as dt
from pathlib import Path

SEED = 20260922
random.seed(SEED)
OUT = str(Path(__file__).resolve().parent)   # 이 스크립트가 있는 폴더(dataset/)에 생성
os.makedirs(OUT, exist_ok=True)

YEAR = 2024
FCF = {'JET-A1': 3.16, 'JET-A': 3.16, 'TS-1': 3.16, 'NO.3': 3.16,
       'JET-B': 3.10, 'AVGAS': 3.10}
DENSITY_STD = 0.8

# 2024년 CORSIA 참여국 중 이 데이터에 등장하는 국가 (ICAO 「CORSIA States for Chapter 3 State Pairs」 4th ed. Rev.1)
# 대만(TW)은 목록에 없다 → 인천↔타이베이는 보고 대상이지만 상쇄 대상이 아님
PARTICIPATING = {'KR', 'US', 'FR', 'DE', 'JP', 'SG', 'TH'}

# ---------------------------------------------------------------- 기단
# MTOW 단위: kg
FLEET = []
def add(reg_prefix, n, icao_type, mtow, owner, start=1):
    for i in range(start, start + n):
        FLEET.append(dict(registration=f'HL{reg_prefix}{i:02d}', icao_type=icao_type,
                          mtow_kg=mtow, ownership=owner, fuel_type='JET-A1'))

add(80, 12, 'B77W', 351534, 'OWNED')      # 장거리
add(75, 4,  'A333', 233000, 'LEASED')     # 중거리
add(83, 10, 'B738', 79016,  'OWNED')      # 단거리
# 경계값 테스트용: MTOW 정확히 5,700kg (법 제6조①1 "초과" → 대상 제외되어야 함)
FLEET.append(dict(registration='HL9001', icao_type='BE20', mtow_kg=5700,
                  ownership='OWNED', fuel_type='AVGAS'))

# ------------------------------------------------------------- 노선망
# (출발, 출발국, 도착, 도착국, 블록시간h, 기준연료t, 국제선, EU역내)
LONG = [
    ('RKSI', 'KR', 'KLAX', 'US', 11.8, 95.0, 1, 0),
    ('KLAX', 'US', 'RKSI', 'KR', 13.2, 104.0, 1, 0),
    ('RKSI', 'KR', 'KJFK', 'US', 13.9, 108.0, 1, 0),
    ('KJFK', 'US', 'RKSI', 'KR', 14.6, 112.0, 1, 0),
    ('RKSI', 'KR', 'LFPG', 'FR', 12.4, 99.0, 1, 0),
    ('LFPG', 'FR', 'RKSI', 'KR', 11.1, 90.0, 1, 0),
    ('LFPG', 'FR', 'EDDF', 'DE', 1.1, 5.2, 1, 1),   # EU 역내 구간
    ('EDDF', 'DE', 'LFPG', 'FR', 1.1, 5.2, 1, 1),
]
MED = [
    ('RKSI', 'KR', 'VTBS', 'TH', 5.8, 26.0, 1, 0),
    ('VTBS', 'TH', 'RKSI', 'KR', 5.4, 24.5, 1, 0),
    ('RKSI', 'KR', 'WSSS', 'SG', 6.4, 28.5, 1, 0),
    ('WSSS', 'SG', 'RKSI', 'KR', 6.1, 27.0, 1, 0),
]
SHORT_INTL = [
    ('RKSI', 'KR', 'RJTT', 'JP', 2.3, 6.8, 1, 0),
    ('RJTT', 'JP', 'RKSI', 'KR', 2.4, 7.0, 1, 0),
    ('RKSI', 'KR', 'RCTP', 'TW', 2.6, 7.4, 1, 0),
    ('RCTP', 'TW', 'RKSI', 'KR', 2.7, 7.6, 1, 0),
]
DOMESTIC = [
    ('RKSS', 'KR', 'RKPC', 'KR', 1.1, 3.4, 0, 0),
    ('RKPC', 'KR', 'RKSS', 'KR', 1.1, 3.4, 0, 0),
]

COLS = ['flight_id', 'date_utc', 'registration', 'icao_type', 'callsign',
        'dep_aerodrome', 'dep_state', 'arr_aerodrome', 'arr_state',
        'is_international', 'is_offsetting_scope', 'flight_purpose',
        'block_off_utc', 'block_on_utc', 'block_hours', 'fuel_type',
        'uplift_volume_l', 'fuel_density_kg_l', 'uplift_tonnes',
        'tank_before_uplift_t', 'tank_after_uplift_t', 'tank_block_off_t',
        'tank_block_on_t', 'data_gap_flag', 'eu_internal_flag',
        'eu_ets_offset_flag', 'saf_batch_id', 'notes']


def make_leg(fid, date, ac, route, seq, prev_tank_on, no_uplift=False):
    dep, dst, arr, ast, bh, base, intl, eu = route
    burn = round(base * random.uniform(0.94, 1.06), 1)
    bh_act = round(bh * random.uniform(0.96, 1.05), 2)
    off_t = dt.datetime(date.year, date.month, date.day, 0, 0) + dt.timedelta(hours=(seq * 3) % 24)
    on_t = off_t + dt.timedelta(hours=bh_act)
    reserve = round(random.uniform(3.0, 9.5), 1)
    tank_off = round(burn + reserve, 1)
    tank_on = round(tank_off - burn, 1)
    if no_uplift:
        uplift_t, vol, tank_before, tank_after = '', '', round(tank_off, 1), round(tank_off, 1)
    else:
        uplift_t = round(max(tank_off - prev_tank_on, 0.5), 1)
        vol = int(round(uplift_t * 1000 / DENSITY_STD))
        tank_before = round(tank_off - uplift_t, 1)
        tank_after = round(tank_off, 1)
    return dict(
        flight_id=fid, date_utc=date.isoformat(), registration=ac['registration'],
        icao_type=ac['icao_type'], callsign=f'HBA{(seq % 900) + 100}',
        dep_aerodrome=dep, dep_state=dst, arr_aerodrome=arr, arr_state=ast,
        is_international=intl,
        is_offsetting_scope=int(bool(intl) and dst in PARTICIPATING and ast in PARTICIPATING),
        flight_purpose='NORMAL',
        block_off_utc=off_t.strftime('%Y-%m-%dT%H:%M:%SZ'),
        block_on_utc=on_t.strftime('%Y-%m-%dT%H:%M:%SZ'), block_hours=bh_act,
        fuel_type=ac['fuel_type'], uplift_volume_l=vol,
        fuel_density_kg_l=DENSITY_STD, uplift_tonnes=uplift_t,
        tank_before_uplift_t=tank_before, tank_after_uplift_t=tank_after,
        tank_block_off_t=tank_off, tank_block_on_t=tank_on,
        data_gap_flag=0, eu_internal_flag=eu, eu_ets_offset_flag=0,
        saf_batch_id='', notes=''), tank_on


def build_clean():
    rows = []
    fid = 1
    d0 = dt.date(YEAR, 1, 1)
    groups = [('B77W', LONG[:6], 1), ('A333', MED, 2), ('B738', SHORT_INTL, 3)]
    for ac in FLEET:
        if ac['icao_type'] == 'BE20':
            continue
        routes = next((g[1] for g in groups if g[0] == ac['icao_type']), SHORT_INTL)
        legs_per_day = next((g[2] for g in groups if g[0] == ac['icao_type']), 2)
        tank_on = 6.0
        for day in range(365):
            date = d0 + dt.timedelta(days=day)
            for k in range(legs_per_day):
                route = routes[(day * legs_per_day + k) % len(routes)]
                r, tank_on = make_leg(f'F{fid:06d}', date, ac, route,
                                      day * legs_per_day + k, tank_on)
                rows.append(r); fid += 1
            # B738은 주 2회 국내선 편성
            if ac['icao_type'] == 'B738' and day % 3 == 0:
                route = DOMESTIC[day % 2]
                r, tank_on = make_leg(f'F{fid:06d}', date, ac, route,
                                      day * legs_per_day + 9, tank_on)
                rows.append(r); fid += 1
    # EU 역내 구간 (별도 편성, EU-ETS 상쇄 대상)
    for day in range(0, 365, 7):
        date = d0 + dt.timedelta(days=day)
        ac = FLEET[0]
        for route in (LONG[6], LONG[7]):
            r, _ = make_leg(f'F{fid:06d}', date, ac, route, day, 8.0)
            r['eu_ets_offset_flag'] = 1
            r['notes'] = 'EU 역내 구간, EU-ETS 배출권으로 상쇄 (지침 제9조③)'
            rows.append(r); fid += 1
    # 무급유 연속 구간 (C-04 배분 검증용): 30개 구간
    for i in range(30):
        date = d0 + dt.timedelta(days=i * 12)
        ac = FLEET[16]
        tank_on = 7.0
        for k, route in enumerate([SHORT_INTL[0], SHORT_INTL[1], SHORT_INTL[2]]):
            r, tank_on = make_leg(f'F{fid:06d}', date, ac, route, 200 + k, tank_on,
                                  no_uplift=(k > 0))
            if k > 0:
                r['notes'] = '무급유 연속편 (C-04 블록타임 비례 배분 대상)'
            rows.append(r); fid += 1
    # 인도적/의료 국제운항 (법 제2조5 단서 → 배출량 산정 제외)
    for i in range(6):
        date = d0 + dt.timedelta(days=40 + i * 50)
        r, _ = make_leg(f'F{fid:06d}', date, FLEET[13], MED[0], 300 + i, 8.0)
        r['flight_purpose'] = 'HUM' if i % 2 == 0 else 'HOSP'
        r['is_offsetting_scope'] = 0
        r['notes'] = '인도적/의료 목적 국제운항 → 배출량 산정 제외'
        rows.append(r); fid += 1
    return rows


def add_saf(rows):
    """SAF 사용편 표시 (JET-A1 계열만)"""
    tagged = 0
    for r in rows:
        if r['is_offsetting_scope'] == 1 and r['icao_type'] == 'B77W' and tagged < 240:
            r['saf_batch_id'] = 'SAF-2024-001' if tagged < 120 else 'SAF-2024-002'
            tagged += 1
    return rows


def inject_errors(rows):
    """오류주입본: 검증 규칙이 정확히 걸려야 하는 케이스만 심는다"""
    d = [dict(r) for r in rows]
    log = []

    def mark(i, rule, desc):
        d[i]['notes'] = (d[i]['notes'] + ' | ' if d[i]['notes'] else '') + f'[오류주입 {rule}] {desc}'
        log.append([d[i]['flight_id'], rule, desc])

    # X-21 블록온 > 블록오프
    i = 500; d[i]['tank_block_on_t'] = round(d[i]['tank_block_off_t'] + 2.4, 1)
    mark(i, 'X-21', '블록온 연료량이 블록오프보다 큼')
    # X-20 탱크 용량 초과 급유 (B77W 최대 연료탱크 약 145.5t)
    i = 1200; d[i]['uplift_tonnes'] = 190.0; d[i]['uplift_volume_l'] = 237500
    mark(i, 'X-20', '급유량이 기종 탱크 용량(145.5t) 초과')
    # X-19 미해결 데이터 공백
    for i in (2100, 2101):
        d[i]['tank_block_on_t'] = ''; d[i]['data_gap_flag'] = 1
        mark(i, 'X-19', '블록온 연료량 누락, 재확보 불가')
    # X-18 항공편 순서 누락 → 해당 행 자체를 삭제 표시
    i = 3300; mark(i, 'X-18', '이 편 다음 일련번호가 통째로 결번 (항공편 순서 누락)')
    del_id = d[3301]['flight_id']
    log.append([del_id, 'X-18', '행 삭제됨 (결번 생성)'])
    d.pop(3301)
    # X-22 편당 연료소비 이상치
    i = 4000; d[i]['tank_block_off_t'] = 260.0; d[i]['tank_block_on_t'] = 5.0
    mark(i, 'X-22', '편당 연료소비가 평균 대비 현저히 높음 (255t)')
    # X-15 출발·도착이 같은 국가인데 국제선으로 표기
    i = 4600
    d[i]['dep_aerodrome'] = 'RKSI'; d[i]['dep_state'] = 'KR'
    d[i]['arr_aerodrome'] = 'RKPK'; d[i]['arr_state'] = 'KR'
    mark(i, 'X-15', '출발·도착 모두 KR인데 is_international=1')
    # T-04 데이터갭 5% 초과 유도
    for i in range(5000, 5000 + 1100):
        if i < len(d):
            d[i]['data_gap_flag'] = 1; d[i]['tank_block_on_t'] = ''
    log.append(['(다수)', 'T-04', '데이터갭 1,100편 주입 → 갭 비율 5% 초과 유도'])
    # X-14b 동일 노선에 연료형식 혼재
    i = 6200; d[i]['fuel_type'] = 'AVGAS'
    mark(i, 'X-14b', '동일 노선인데 연료형식만 AVGAS로 다름 (분리 보고 필요)')
    # F-17 허용되지 않는 연료종류
    i = 7000; d[i]['fuel_type'] = 'SAF-100'
    mark(i, 'F-17', '연료종류가 허용 6종에 없음')
    # F-16 날짜 형식 오류
    i = 7500; d[i]['date_utc'] = '15/03/2024'
    mark(i, 'F-16', '날짜 형식이 YYYY-MM-DD가 아님')
    # X-24 SAF 이중 청구
    i = 8000; d[i]['saf_batch_id'] = 'SAF-2024-001'
    d[i]['notes'] = (d[i]['notes'] + ' | ' if d[i]['notes'] else '') + \
        '[오류주입 X-24] 동일 배치를 타 제도(K-ETS)에도 감축실적으로 제출한 것으로 가정'
    # 실제 X-24 판정은 saf_batches_dirty.csv의 double_claimed_elsewhere=Y로 한다 (gen_aux.py)
    log.append(['SAF-2024-002', 'X-24', 'saf_batches_dirty.csv에서 double_claimed_elsewhere=Y'])
    # X-16 비참여국(중국) 노선을 상쇄 대상으로 잘못 표기
    i = 9000
    d[i]['arr_aerodrome'] = 'ZBAA'; d[i]['arr_state'] = 'CN'; d[i]['is_offsetting_scope'] = 1
    mark(i, 'X-16', '비참여국(CN) 노선인데 상쇄 대상으로 표기')
    return d, log


def write_csv(path, rows, cols=COLS):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in cols})


# ------------------------------------------------------------- 실행
clean = add_saf(build_clean())
dirty, errlog = inject_errors(clean)

write_csv(f'{OUT}/flights_clean.csv', clean)
write_csv(f'{OUT}/flights_dirty.csv', dirty)

with open(f'{OUT}/error_injection_log.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f); w.writerow(['flight_id', 'rule_id', 'description'])
    w.writerows(errlog)

with open(f'{OUT}/aircraft.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=['registration', 'icao_type', 'mtow_kg',
                                      'ownership', 'fuel_type', 'tank_capacity_t'])
    w.writeheader()
    cap = {'B77W': 145.5, 'A333': 109.2, 'B738': 20.8, 'BE20': 1.6}
    for a in FLEET:
        a2 = dict(a); a2['tank_capacity_t'] = cap[a['icao_type']]
        w.writerow(a2)

# 집계
tot_fuel = sum(float(r['tank_block_off_t']) - float(r['tank_block_on_t'])
               for r in clean if r['is_offsetting_scope'] == 1 and r['tank_block_on_t'] != '')
print('총 항공편(clean):', len(clean))
print('상쇄대상 국제선 편수:', sum(1 for r in clean if r['is_offsetting_scope'] == 1))
print('상쇄대상 연료(t): %.1f' % tot_fuel)
print('상쇄대상 CO2(t): %.1f' % (tot_fuel * 3.16))
print('오류주입 건수:', len(errlog))
