"""
CSV 입출력 및 파라미터 로딩
"""
import csv
import io
from decimal import Decimal
from pathlib import Path

__all__ = ['load_csv', 'load_params', 'group_by_aircraft',
           'InputError', 'check_csv', 'REQUIRED_COLUMNS', 'CSV_ENCODINGS']

# 한국어 엑셀의 「CSV(쉼표로 분리)」 기본 저장은 cp949다. UTF-8(BOM 포함)을 먼저 시도하고
# 실패하면 cp949로 다시 읽는다.
CSV_ENCODINGS = ('utf-8-sig', 'cp949')

# 열이 빠지면 엔진이 멈추는 열. 열을 하나씩 빼고 계산·검증을 돌려서 찾았다 (2026-10-24).
#  - flights: tank_block_off_t · tank_block_on_t는 기본 측정 방법(BLOCK_OFF_ON)에 필요
REQUIRED_COLUMNS = {
    'flights': ['registration', 'dep_aerodrome', 'arr_aerodrome', 'fuel_type',
                'tank_block_off_t', 'tank_block_on_t'],
    'saf': ['saf_batch_id', 'mass_tonnes', 'equivalent_fuel_type', 'l_cef_gco2e_mj'],
}


class InputError(Exception):
    """사람이 읽을 수 있는 입력 오류. message는 화면에, detail은 접힌 상세에 쓴다."""

    def __init__(self, message, detail=''):
        super().__init__(message)
        self.message = message
        self.detail = detail


def _read_text(path):
    raw = Path(path).read_bytes()
    errors = []
    for enc in CSV_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as e:
            errors.append(f'{enc}: {e}')
    raise InputError('파일의 글자 인코딩을 읽을 수 없습니다. '
                     'UTF-8 또는 CSV(쉼표로 분리)로 다시 저장해 올리세요.',
                     ' / '.join(errors))


def load_csv(path):
    """UTF-8(BOM 포함)로 읽다가 실패하면 cp949로 다시 읽는다."""
    text, _ = _read_text(path)
    return list(csv.DictReader(io.StringIO(text, newline='')))


def check_csv(path, kind, label=None):
    """올린 CSV를 엔진에 넣기 전에 확인한다. 문제가 있으면 InputError.

    kind  : REQUIRED_COLUMNS의 키 ('flights', 'saf')
    label : 화면에 쓸 파일 이름 (예: '비행 기록 CSV')
    """
    label = label or Path(path).name
    text, enc = _read_text(path)
    if not text.strip():
        raise InputError(f'{label}에 데이터가 없습니다.', '파일이 비어 있음')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    header = [h.strip() for h in (reader.fieldnames or []) if h is not None]
    rows = list(reader)
    if not header:
        raise InputError(f'{label}에 데이터가 없습니다.', '열 이름(첫 줄)이 없음')
    if not rows:
        raise InputError(f'{label}에 데이터가 없습니다. 열 이름만 있고 내용 줄이 없습니다.',
                         f'열 {len(header)}개, 행 0개')
    missing = [c for c in REQUIRED_COLUMNS.get(kind, []) if c not in header]
    if missing:
        raise InputError(
            f'{label}에 필수 열 {", ".join(missing)}이 없습니다. '
            '올린 파일의 열 이름을 확인하세요.',
            f'인코딩 {enc} · 읽은 열: {", ".join(header)}')
    return {'encoding': enc, 'columns': header, 'rows': len(rows)}


def load_params(path):
    rows = load_csv(path)
    out = {}
    for r in rows:
        v = r['value']
        try:
            v = Decimal(v)
        except Exception:
            pass
        out[r['key']] = v
    return out


def group_by_aircraft(flights, key='registration',
                      order=('date_utc', 'block_off_utc', 'flight_id')):
    """항공기별로 묶고 시간순 정렬. 5방법 모두 시간순 전제이므로 필수."""
    groups = {}
    for f in flights:
        groups.setdefault(f[key], []).append(f)
    for k in groups:
        groups[k].sort(key=lambda x: tuple(str(x.get(o, '')) for o in order))
    return groups


def default_dataset_dir():
    here = Path(__file__).resolve().parent.parent
    for cand in (here / 'dataset', here.parent / 'dataset'):
        if cand.is_dir():
            return cand
    return here / 'dataset'
