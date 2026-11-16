"""Replace real refinery names in the synthetic SAF data with neutral placeholders.

Run from the engine folder:   python anonymize_suppliers.py
Safe to run twice. Files are edited as raw bytes, so encoding and line endings are kept.
"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    os.path.join(BASE, 'dataset', 'saf_batches.csv'),
    os.path.join(BASE, 'dataset', 'saf_batches_dirty.csv'),
    os.path.join(BASE, 'dataset', 'gen_aux.py'),
]
MAP = [
    ('GS Caltex 여수', '공급사 A(가상)'),
    ('SK Energy 울산', '공급사 B(가상)'),
    ('S-OIL 온산', '공급사 C(가상)'),
]
total = 0
for path in FILES:
    if not os.path.exists(path):
        print('not found:', path)
        continue
    data = open(path, 'rb').read()
    n = 0
    for old, new in MAP:
        o, w = old.encode('utf-8'), new.encode('utf-8')
        n += data.count(o)
        data = data.replace(o, w)
    open(path, 'wb').write(data)
    total += n
    print(f'{os.path.relpath(path, BASE)}: {n} replaced')
print('done, total', total)
