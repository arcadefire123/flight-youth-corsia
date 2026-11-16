"""
C-00 · 공통 상수
calc_spec_v1.md §1 참조. 숫자는 여기에만 둔다. 함수 안에 리터럴을 쓰지 않는다.
"""
from decimal import Decimal, ROUND_HALF_UP

# ── 연료전환계수 (영 별표3 제1호 / 지침 별표6 제5호) ────────────────
FCF = {
    'JET-A': Decimal('3.16'),
    'JET-A1': Decimal('3.16'),
    'TS-1': Decimal('3.16'),
    'NO.3': Decimal('3.16'),
    'JET-B': Decimal('3.10'),
    'AVGAS': Decimal('3.10'),
}
FUEL_TYPES = set(FCF)

# ── 수명주기 기준 배출량 LC, gCO2e/MJ (지침 별표6 제5호) ────────────
LC = {
    'JET-A': 89, 'JET-A1': 89, 'JET-B': 89, 'TS-1': 89, 'NO.3': 89,
    'AVGAS': 95,
}

# ── 표준 연료밀도 kg/L (지침 제3조④·제5조③, 별표2 각 호 주1) ───────
# ICAO 문서에는 수치 규정이 없는 국내 고유 상수
DENSITY_STD = Decimal('0.8')

# ── 임계값 ──────────────────────────────────────────────────────────
THRESHOLD_OBLIGOR = 10_000      # t, "이상" (법 제6조①1)
THRESHOLD_CERT = 50_000         # t, "이상"이면 별표2 의무 (지침 제3조③ 단서)
THRESHOLD_CERT_1920 = 500_000   # t, 2019~2020 한정 (지침 별표1 4-a)
THRESHOLD_EXEMPT = 3_000        # t, "미만"이면 면제 (영 제9조①1)
MTOW_MIN_KG = 5_700             # kg, "초과" (법 제6조①1)
DATAGAP_LIMIT = Decimal('0.05') # 5% (지침 별표4 6-b)
OGF_DEFAULT_BASE = Decimal('10000')  # t (지침 별표6 제4호 단서)

# ── 부문별·개별 백분율 (지침 별표6 제3호 / SARPs Table 3.1) ─────────
# (시작연도, 종료연도, %S, %O)
PCT_TABLE = [
    (2021, 2023, Decimal('1.00'), Decimal('0.00')),  # SARPs 3.2.1 only (이 기간 SGF = 0이라 결과는 항상 0)
    (2024, 2032, Decimal('1.00'), Decimal('0.00')),
    (2033, 2035, Decimal('0.85'), Decimal('0.15')),
]

# ── 중요성 기준 (SARPs App.6 3.5.1) ─────────────────────────────────
# 연간 국제선 배출량 50만 t 초과 → 2%, 50만 t 이하 → 5%
# 국내 환경부 검증지침은 적용하지 않는다 (ICAO 기준만 적용하기로 확정)
MATERIALITY_BOUNDARY_T = Decimal('500000')
MATERIALITY_HIGH = Decimal('0.02')    # 50만 t 초과
MATERIALITY_LOW = Decimal('0.05')     # 50만 t 이하

# ── 검증의견 (SARPs App.6 3.10) ─────────────────────────────────────
OPINION_SATISFACTORY = '만족'                    # verified as satisfactory
OPINION_WITH_COMMENTS = '의견부 만족'            # verified as satisfactory with comments
OPINION_UNSATISFACTORY = '불만족'                # verified as unsatisfactory


# ── 반올림 헬퍼 ─────────────────────────────────────────────────────
def r(value, places=1):
    """사사오입(ROUND_HALF_UP). 파이썬 기본 round()는 은행가 반올림이라 쓰지 않는다."""
    if value is None or value == '':
        return None
    q = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP)


def r_int(value):
    """톤 단위 반올림 (지침 제9조①, 별표6 제1호)"""
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def to_dec(value, default=None):
    if value is None or value == '':
        return default
    return Decimal(str(value))


# ── 서류 형식 기준 (5단계 잔여 규칙) ────────────────────────────────
# F-09 · 사용해야 하는 서식 버전
TEMPLATE_VERSION = 'ETM Vol.IV Appendix 1 (4th edition)'

# F-02 · 배출량보고서 1-b 항공기 식별정보 선택지 (ER 템플릿 선택 목록과 동일)
AIRCRAFT_ID_OPTIONS = {
    'ICAO_DESIGNATOR': ('icao_designator',),
    'REGISTRATION_MARKS': ('registration_marks',),
    'BOTH': ('icao_designator', 'registration_marks'),
}

# F-04 · 배출량보고서 필수 15개 항목 (지침 제5조①). (항목명, 이 중 하나라도 있으면 충족)
ER_REQUIRED = [
    ('이행의무자 정보', ('operator_name',)),
    ('승인받은 모니터링계획 정보', ('emp_version',)),
    ('검증기관 정보', ('verification_body',)),
    ('보고연도', ('reporting_year',)),
    ('연료 모니터링 방법', ('monitoring_method',)),
    ('사용된 연료의 종류와 질량', ('fuel_mass_by_type',)),
    ('연료 밀도', ('density_method',)),
    ('보고기간 동안의 국제운항편 수', ('total_flights',)),
    ('비행장 쌍당 국제운항편 수', ('aerodrome_pairs', 'state_pairs')),
    ('비행장 쌍당 탄소 배출량', ('aerodrome_pairs', 'state_pairs')),
    ('데이터공백의 규모', ('data_gap_pct',)),
    ('비행기 정보', ('aircraft',)),
    ('CERT 정보', ('cert_info',)),
    ('CORSIA 적격연료 유형 및 질량, 탄소 감축량', ('cef_claim',)),
    ('탄소 배출량', ('total_co2_t',)),
]

# F-05 · 배출량검증보고서 / F-07 · 상쇄의무이행검증보고서 필수 5개 항목 (영 제8조③·제10조③)
VR_REQUIRED = [
    ('검증기관 명칭 및 검증심사원 성명', 'verifier'),
    ('검증기준', 'criteria'),
    ('발견사항 및 조치 내용', 'findings_and_actions'),
    ('최종 검증의견', 'verification_opinion'),
    ('그 밖에 국토교통부장관이 인정하는 사항', 'other'),
]

# F-06 · 상쇄의무이행보고서 필수 5개 항목 (지침 제11조①)
EUCR_REQUIRED = [
    ('이행의무자 정보', 'operator_name'),
    ('이행기간 연도', 'compliance_period'),
    ('총 상쇄의무량', 'total_final_offsetting_requirement'),
    ('말소 배출권 총량', 'total_cancelled_units'),
]   # 5번째 '말소 배출권 식별정보'는 emission_units 목록으로 확인

# F-26 · 모니터링 계획 변경승인 신청 대상 5개 사유 (지침 제3조⑥)
EMP_CHANGE_CATEGORIES = [
    '법인 및 AOC 정보',
    'ICAO 지정기호',
    '연료 모니터링 방법 및 절차',
    '자료관리',
    '그 밖에 모니터링 관리에 영향을 미칠 수 있는 사항',
]

# F-28 · 이행의무자 보관 자료 4종 (규칙 제10조①)
RECORDS_REQUIRED = [
    '모니터링 계획서',
    '배출량보고서 및 배출량검증보고서',
    '상쇄의무이행보고서 및 상쇄의무이행검증보고서',
    '지속가능항공연료 사용 이력',
]
