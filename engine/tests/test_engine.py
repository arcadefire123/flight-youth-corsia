"""
계산엔진 단위테스트
    python -m unittest discover -s tests -v
또는  python tests/test_engine.py
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from corsia import fuel_use, co2, offsetting, cef_reduction, final_offset  # noqa: E402
from corsia.constants import DENSITY_STD  # noqa: E402
from corsia.cef_reduction import MixedFuelClassError  # noqa: E402
from corsia.io_utils import load_csv, check_csv, InputError  # noqa: E402

D = Decimal


class TestFuelUse(unittest.TestCase):
    """C-01 ~ C-05 - 골든 케이스는 cli.run_golden이 전수 검사하므로
    여기서는 예외 경로와 국내 고유 규칙을 본다."""

    def test_volume_to_mass_standard_density(self):
        # KR-01 · 지침 제3조④ 표준밀도 0.8 kg/L
        self.assertEqual(fuel_use.volume_to_mass(50000), D('40.0'))
        self.assertEqual(DENSITY_STD, D('0.8'))

    def test_volume_to_mass_actual_density(self):
        self.assertEqual(fuel_use.volume_to_mass(50000, '0.79'), D('39.5'))

    def test_method_a_no_uplift_uses_block_off(self):
        # GA-4 · 급유 없는 편은 T를 블록오프 연료량으로 대체
        seq = [
            {'uplift_tonnes': '', 'tank_after_uplift_t': '9.6', 'tank_block_off_t': '9.5'},
            {'uplift_tonnes': '71.7', 'tank_after_uplift_t': '75.1', 'tank_block_off_t': '75.0'},
        ]
        self.assertEqual(fuel_use.compute_sequence(seq, 'METHOD_A')[0], D('6.1'))

    def test_method_a_last_flight_is_none(self):
        seq = [{'uplift_tonnes': '10', 'tank_after_uplift_t': '20', 'tank_block_off_t': '20'}]
        self.assertIsNone(fuel_use.compute_sequence(seq, 'METHOD_A')[0])

    def test_method_b_first_flight_is_none(self):
        seq = [{'tank_block_on_t': '5', 'uplift_tonnes': '10'},
               {'tank_block_on_t': '4', 'uplift_tonnes': '12'}]
        out = fuel_use.compute_sequence(seq, 'METHOD_B')
        self.assertIsNone(out[0])
        self.assertEqual(out[1], D('13.0'))

    def test_fuel_uplift_includes_own_flight(self):
        # C-04 핵심: 급유편 N 자신도 배분 대상
        vals = fuel_use.fuel_uplift_group('26.9', ['3.1', '0.9'])
        self.assertEqual(vals, [D('20.8'), D('6.1')])
        # 구간이 1편뿐이면 F_N = U_N
        self.assertEqual(fuel_use.fuel_uplift_group('89.3', ['11.8']), [D('89.3')])

    def test_fuel_uplift_zero_block_hours_guard(self):
        with self.assertRaises(ValueError):
            fuel_use.fuel_uplift_group('10.0', ['0', '0'])

    def test_afbr_rounds_to_three_decimals(self):
        # KR-02 · 지침 별표2 제5호 2) 주3
        self.assertEqual(fuel_use.afbr('1000.0', '137.55'), D('7.270'))

    def test_afbr_zero_guard(self):
        with self.assertRaises(ValueError):
            fuel_use.afbr('100', '0')

    def test_round_half_up_not_bankers(self):
        # 파이썬 기본 round(0.5)는 0을 주지만 우리는 사사오입이어야 한다
        self.assertEqual(fuel_use.block_hour_alloc('7.270', '6.5'), D('47.3'))


class TestCO2(unittest.TestCase):
    """C-06 · C-07"""

    def test_fcf_by_fuel_type(self):
        self.assertEqual(co2.co2_from_fuel('100', 'JET-A1'), D('316.000'))
        self.assertEqual(co2.co2_from_fuel('100', 'AVGAS'), D('310.000'))

    def test_invalid_fuel_type_raises(self):
        # F-17
        with self.assertRaises(ValueError):
            co2.co2_from_fuel('100', 'SAF-100')

    def test_mtow_boundary_is_exclusive(self):
        # KR-12 · 법 제6조①1 "초과" - 정확히 5,700kg은 제외
        self.assertFalse(co2.is_obligor_aircraft(5700))
        self.assertTrue(co2.is_obligor_aircraft(5701))

    def test_cert_threshold(self):
        # KR-08a/b/c
        self.assertTrue(co2.cert_eligible(49000, 2027))
        self.assertFalse(co2.cert_eligible(51000, 2027))
        self.assertFalse(co2.cert_eligible(50000, 2027))   # "이상"이므로 CERT 불가

    def test_cert_threshold_2019_2020(self):
        # KR-09 · 지침 별표1 4-a
        self.assertTrue(co2.cert_eligible(400000, 2019))
        self.assertFalse(co2.cert_eligible(400000, 2027))

    def test_method_requirement_first_year_exception(self):
        # 지침 제3조③ 단서 - 지정 최초 연도는 제외
        self.assertEqual(co2.monitoring_method_required(60000, 2027), 'FUEL_USE_REQUIRED')
        self.assertEqual(
            co2.monitoring_method_required(60000, 2027, is_first_designated_year=True),
            'EITHER')

    def test_data_gap_ratio_rounds_to_one_decimal(self):
        # T-04b · 지침 별표4 6-b1
        flights = [{'is_international': '1', 'is_offsetting_scope': '1', 'data_gap_flag': '1' if i < 51 else '0'}
                   for i in range(1000)]
        self.assertEqual(co2.data_gap_ratio(flights, [None] * 1000), D('5.1'))


class TestOffsetting(unittest.TestCase):
    """C-08 · C-09"""

    def test_ogf_is_percentage_not_ratio(self):
        # KR-06b · 이번 프로젝트에서 실제로 틀렸던 부분
        val = offsetting.ogf(11_000_000, 5_100_700)
        self.assertEqual(val, D('0.5363'))
        self.assertNotEqual(val, D('2.1566'))     # 배수가 아님
        self.assertTrue(D('0') <= val <= D('1'))  # X-27 범위 검사

    def test_ogf_new_entrant_uses_10000(self):
        # KR-06 · 지침 별표6 제4호 단서
        self.assertEqual(offsetting.ogf(50_000, is_new_entrant=True), D('0.8000'))

    def test_ogf_zero_division_guard(self):
        # KR-06c
        with self.assertRaises(ValueError):
            offsetting.ogf(0, 10_000)

    def test_pct_table(self):
        self.assertEqual(offsetting.pct_for_year(2027)[:2], (D('1.00'), D('0.00')))
        self.assertEqual(offsetting.pct_for_year(2034)[:2], (D('0.85'), D('0.15')))

    def test_pct_2021_2023_carries_warning(self):
        # 미확정 사항 1 - 국내 지침에 없는 구간
        self.assertIsNotNone(offsetting.pct_for_year(2022)[2])
        self.assertIsNone(offsetting.pct_for_year(2027)[2])

    def test_year_out_of_scope(self):
        with self.assertRaises(ValueError):
            offsetting.pct_for_year(2040)

    def test_or_2034_golden(self):
        # GO-3 · ETM 그림4-4
        res = offsetting.offsetting_requirement(11_000_000, 2034, '0.3108',
                                                ogf_value='0.5363')
        self.assertEqual(res['OR_y_t'], D('3790875.00'))
        self.assertEqual(res['sector_part_t'], D('2905980.00'))
        self.assertEqual(res['individual_part_t'], D('884895.00'))

    def test_eu_ets_adjustment(self):
        # X-23 · 지침 제9조③
        flights = [{'eu_ets_offset_flag': '1'}, {'eu_ets_offset_flag': '0'},
                   {'eu_ets_offset_flag': '1'}]
        total, n = offsetting.eu_ets_adjustment(flights, [D('10'), D('20'), D('5')])
        self.assertEqual((total, n), (D('15.0'), 2))


class TestCEF(unittest.TestCase):
    """C-10"""

    def test_reduction_formula(self):
        # KR-16 · ER = FCF × [Σ MS × (1 - L_CEF/LC)]
        batches = [{'saf_batch_id': 'B1', 'equivalent_fuel_type': 'JET-A1',
                    'mass_tonnes': '100', 'l_cef_gco2e_mj': '45', 'lc_gco2e_mj': '89'}]
        self.assertEqual(cef_reduction.emission_reduction(batches)['ER_y_t'],
                         D('156.22'))

    def test_fcf_applied_outside_sum(self):
        """FCF가 합계 밖에서 한 번만 곱해지는지 확인.
        같은 계열 두 배치를 넣었을 때 개별 계산 합과 같아야 한다."""
        b = [{'equivalent_fuel_type': 'JET-A1', 'mass_tonnes': '100',
              'l_cef_gco2e_mj': '45', 'lc_gco2e_mj': '89'},
             {'equivalent_fuel_type': 'JET-A1', 'mass_tonnes': '50',
              'l_cef_gco2e_mj': '20', 'lc_gco2e_mj': '89'}]
        total = cef_reduction.emission_reduction(b)['ER_y_t']
        self.assertEqual(total, D('278.72'))

    def test_mixed_fuel_class_raises(self):
        # KR-11 · Jet 계열 + AvGas 계열 혼재
        b = [{'equivalent_fuel_type': 'JET-A1', 'mass_tonnes': '100',
              'l_cef_gco2e_mj': '45', 'lc_gco2e_mj': '89'},
             {'equivalent_fuel_type': 'AVGAS', 'mass_tonnes': '30',
              'l_cef_gco2e_mj': '24', 'lc_gco2e_mj': '95'}]
        with self.assertRaises(MixedFuelClassError):
            cef_reduction.emission_reduction(b)
        # 분리 계산 옵션을 켜면 통과해야 한다
        self.assertGreater(
            cef_reduction.emission_reduction(b, split_by_class=True)['ER_y_t'], 0)

    def test_batch_validation(self):
        bad = [{'saf_batch_id': 'X', 'equivalent_fuel_type': 'JET-A1',
                'mass_tonnes': '0', 'l_cef_gco2e_mj': '45',
                'certification_scheme': '', 'certificate_no': '',
                'lca_value_source': 'GUESS'}]
        self.assertEqual(len(cef_reduction.validate_batches(bad)), 4)


class TestFinalOffset(unittest.TestCase):
    """C-11 · C-12"""

    def test_negative_becomes_zero(self):
        # KR-03 · 지침 별표6 제1호
        res = final_offset.final_offsetting_requirement([1000], [1500])
        self.assertEqual(res['FOR_t'], 0)
        self.assertTrue(res['was_negative'])
        self.assertFalse(res['carried_forward'])

    def test_rounds_up_to_tonne(self):
        # KR-04 · SARPs 3.4.4 "rounded up" (국내 지침은 반올림 — ICAO 기준 적용)
        self.assertEqual(
            final_offset.final_offsetting_requirement(['3790874.6'], [0])['FOR_t'],
            3790875)
        self.assertEqual(
            final_offset.final_offsetting_requirement(['3790874.4'], [0])['FOR_t'],
            3790875)
        # 이미 정수면 그대로
        self.assertEqual(
            final_offset.final_offsetting_requirement(['3790874'], [0])['FOR_t'],
            3790874)

    def test_exemption_under_3000(self):
        # KR-05 · 영 제9조①1
        self.assertTrue(final_offset.exemption_check(2999)['exempt'])
        self.assertFalse(final_offset.exemption_check(3000)['exempt'])

    def test_exemption_new_entrant(self):
        # 영 제9조①2
        res = final_offset.exemption_check(
            prev_period_total_or=5000, is_new_entrant_within_3y=True,
            emissions_since_start=500, world_2019_emissions=1_000_000)
        self.assertTrue(res['exempt'])

    def test_materiality_icao(self):
        # SARPs App.6 3.5.1 · 50만 t 초과 2%, 이하 5% (경계값은 '이하' 쪽)
        self.assertEqual(final_offset.materiality_threshold(500_000), D('0.05'))
        self.assertEqual(final_offset.materiality_threshold(500_001), D('0.02'))
        self.assertEqual(final_offset.materiality_threshold(300_000), D('0.05'))

    def test_materiality_limit(self):
        # KR-14 · 우리 데이터셋(약 190만 t)은 2% 적용
        res = final_offset.materiality(1_899_987)
        self.assertEqual(res['applied_pct'], D('0.02'))
        self.assertEqual(res['limit_t'], D('37999.7'))


class TestPipeline(unittest.TestCase):
    """통합 - flights_clean.csv에서 목표 수치를 재현하는지"""

    @classmethod
    def setUpClass(cls):
        from corsia.cli import run_pipeline
        base = Path(__file__).resolve().parent.parent
        cls.res = run_pipeline(base / 'dataset', 'BLOCK_OFF_ON')

    def test_total_co2_matches_target(self):
        self.assertEqual(self.res['C06_emissions']['total_co2_t'], D('1899987.0'))

    def test_total_fuel(self):
        self.assertEqual(self.res['C06_emissions']['total_fuel_t'], D('601261.7'))

    def test_counted_flights(self):
        self.assertEqual(self.res['C06_emissions']['counted_flights'], 18446)

    def test_mtow_5700_aircraft_excluded(self):
        self.assertIn('HL9001', self.res['meta']['aircraft_excluded_by_mtow'])

    def test_clean_dataset_has_no_data_gap(self):
        self.assertEqual(self.res['C06_emissions']['data_gap_pct'], D('0.0'))
        self.assertFalse(self.res['C06_emissions']['gap_over_limit'])

    def test_materiality_applied(self):
        self.assertEqual(self.res['materiality']['applied_pct'], D('0.02'))

    def test_offsetting_excludes_non_participant(self):
        """대만은 2024년 참여국이 아니라 타이베이 노선은 보고 대상이지만 상쇄 대상이 아니다"""
        e = self.res['C06_emissions']
        self.assertEqual(e['offsetting_flights'], 12946)
        self.assertEqual(e['offsetting_co2_t'], D('1769609.2'))
        self.assertLess(e['offsetting_co2_t'], e['total_co2_t'])
        self.assertEqual(self.res['C11_final']['FOR_t'], 265773)

    def test_reporting_year_is_2024(self):
        self.assertEqual(self.res['meta']['reporting_year'], 2024)

    def test_final_offset_positive(self):
        self.assertGreater(self.res['C11_final']['FOR_t'], 0)


class TestInput(unittest.TestCase):
    """CSV 읽기와 올린 파일 점검 (io_utils)"""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())

    def _write(self, name, text, enc='utf-8'):
        p = self.dir / name
        p.write_bytes(text.encode(enc))
        return p

    def test_cp949_csv_with_korean_column(self):
        """한국어 엑셀 「CSV(쉼표로 분리)」 기본 저장(cp949)도 읽는다 - 한글 열 이름·값 포함"""
        p = self._write('k.csv', '항공편,비고\nF001,한글 비고\n', enc='cp949')
        with self.assertRaises(UnicodeDecodeError):
            p.read_bytes().decode('utf-8')          # utf-8로는 실제로 실패하는 파일인지 먼저 확인
        rows = load_csv(p)
        self.assertEqual(rows, [{'항공편': 'F001', '비고': '한글 비고'}])

    def test_utf8_bom_still_first(self):
        p = self._write('b.csv', '\ufeff항공편\nF001\n')
        self.assertEqual(load_csv(p), [{'항공편': 'F001'}])

    def test_missing_required_column_is_readable(self):
        p = self._write('f.csv', 'registration,dep_aerodrome\nHL1,RKSI\n')
        with self.assertRaises(InputError) as cm:
            check_csv(p, 'flights', '비행 기록 CSV')
        self.assertIn('필수 열', cm.exception.message)
        self.assertIn('fuel_type', cm.exception.message)
        self.assertNotIn('KeyError', cm.exception.message)

    def test_empty_and_header_only(self):
        for name, text in (('e.csv', ''), ('h.csv', 'registration,fuel_type\n')):
            with self.assertRaises(InputError) as cm:
                check_csv(self._write(name, text), 'flights', '비행 기록 CSV')
            self.assertIn('데이터가 없습니다', cm.exception.message)

    def test_result_has_no_full_path(self):
        """결과의 dataset에는 폴더 이름만 - PC 사용자 이름 등 전체 경로를 남기지 않는다"""
        from corsia.cli import run_pipeline
        import json
        ds = Path(__file__).resolve().parent.parent / 'dataset'
        res = run_pipeline(ds)
        self.assertEqual(res['meta']['dataset'], 'dataset')
        self.assertNotIn(str(ds.parent), json.dumps(res, default=str, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
