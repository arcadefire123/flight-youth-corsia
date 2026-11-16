#!/usr/bin/env python
"""저장된 분류 결과를 골든셋으로 다시 채점한다 (AI 호출 없음, 비용 0).

  python score_classifier.py --run ..\\8_classifier\\runs\\<결과 폴더> --golden <채운 양식 파일·폴더> [--exclude ID,ID] [--golden-label "팀 골든셋(전문가 검토 후)"]
결과: 그 폴더에 accuracy_report.md, accuracy.json, 오답원인분석표.xlsx
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corsia.classify import report as RP  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description='분류 결과 골든셋 채점(AI 호출 없음)')
    ap.add_argument('--run', required=True)
    ap.add_argument('--golden', nargs='+', required=True)
    ap.add_argument('--exclude', default='')
    ap.add_argument('--golden-label', default='팀 골든셋(전문가 검토 전)')
    a = ap.parse_args(argv)
    ex = [x.strip() for x in a.exclude.split(',') if x.strip()]
    res = RP.score_run(Path(a.run), a.golden, exclude=ex, label=a.golden_label)
    return 0 if res else 1


if __name__ == '__main__':
    sys.exit(main())
