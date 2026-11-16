"""
검증 규칙 등록 체계
verification_rules_v1.md (v1.3) 구현의 기반.

규칙 하나 = 함수 하나. @rule 데코레이터로 등록한다.
함수는 ctx를 받아 위반 사례 리스트를 반환한다. 빈 리스트면 통과.
"""
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# 심각도 (verification_rules_v1.md §0)
CRITICAL = '치명'     # → 부적정
MAJOR = '중요'        # → 조건부 적정
MINOR = '경미'        # → 적정(비고)
BLOCKING = '차단'     # → 검증 불가
INFO = '정보'         # 판정에 영향 없음

# 계층
LAYER_FORMAT = '형식'
LAYER_THRESHOLD = '임계값'
LAYER_CONSISTENCY = '일관성'
LAYER_QUALITATIVE = '정성'

# 결과 상태
PASS = 'PASS'
FAIL = 'FAIL'
SKIPPED = 'SKIPPED'   # 판정에 필요한 자료가 없어 수행 못 함
NOTICE = 'NOTICE'     # INFO 등급 규칙이 걸린 경우. 판정에 영향 없음

REGISTRY = []


class MissingInput(Exception):
    """판정에 필요한 서류·자료가 없음. run_rules가 PASS가 아니라 SKIPPED로 기록한다.

    인자는 코드 이름으로 준다 (예: 'documents.VR'). 화면 이름은 display.INPUT_LABELS가 붙인다."""


@dataclass
class Rule:
    id: str
    layer: str
    severity: str
    title: str
    source: str
    func: Callable
    needs: tuple = ()          # ctx에 반드시 있어야 하는 키
    max_samples: int = 5


@dataclass
class Finding:
    rule_id: str
    layer: str
    severity: str
    title: str
    source: str
    status: str
    violation: Optional[str] = None   # 화면 표시용 위반 문구 (violation_text.py)
    count: int = 0
    samples: List[dict] = field(default_factory=list)
    note: Optional[str] = None
    missing_inputs: List[str] = field(default_factory=list)   # SKIPPED일 때 빠진 자료 (코드 이름)

    def as_dict(self):
        return {
            'rule_id': self.rule_id, 'layer': self.layer,
            'severity': self.severity, 'title': self.title,
            'source': self.source, 'status': self.status,
            'violation': self.violation or self.title,
            'count': self.count, 'samples': self.samples, 'note': self.note,
            'missing_inputs': list(self.missing_inputs),
        }


def rule(id, layer, severity, title, source, needs=()):
    def deco(fn):
        REGISTRY.append(Rule(id=id, layer=layer, severity=severity, title=title,
                             source=source, func=fn, needs=tuple(needs)))
        return fn
    return deco


def get_registry():
    """등록 순서가 아니라 ID 순으로 정렬해 돌려준다."""
    def key(r):
        prefix = r.id.split('-')[0]
        order = {'F': 0, 'T': 1, 'X': 2, 'Q': 3}
        num = ''.join(ch for ch in r.id.split('-')[1] if ch.isdigit())
        return (order.get(prefix, 9), int(num or 0), r.id)
    return sorted(REGISTRY, key=key)
