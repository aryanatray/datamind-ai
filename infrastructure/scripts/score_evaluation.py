import argparse
import json
from pathlib import Path


def score(cases, key):
    values = [case.get("human_grades", {}).get(key) for case in cases]
    graded = [value for value in values if isinstance(value, bool)]
    return {
        "graded": len(graded),
        "total": len(values),
        "accuracy": sum(graded) / len(graded) if graded else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text())
    result = {
        key: score(report["cases"], key)
        for key in ("answer_correct", "citation_supported", "appropriate_abstention")
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
