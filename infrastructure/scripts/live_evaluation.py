"""Opt-in live generation evaluation. Requires an authenticated, scoped evaluation workspace.

Exports evidence for human grading. Deterministic checks are labeled and are never an LLM quality guarantee.
"""

import argparse
import json
import os
import re
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def numbers(value):
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        return [n for item in value for n in numbers(item)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in numbers(item)]
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true", help="Explicitly permit budgeted Gemini calls")
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument(
        "--sources", required=True, help="JSON mapping from category to permitted source ID lists"
    )
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--token-budget", type=int, default=30000)
    parser.add_argument("--output", default="docs/live-evaluation.json")
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit(
            "Live calls disabled. Pass --allow-live only after configuring the centralized backend key."
        )
    token = os.environ.get("EVAL_SESSION_TOKEN", "")
    workspace = os.environ.get("EVAL_WORKSPACE_ID", "")
    if not token or not workspace:
        raise SystemExit(
            "Set EVAL_SESSION_TOKEN and EVAL_WORKSPACE_ID locally. Do not paste tokens into chat."
        )
    mapping = json.loads(Path(args.sources).read_text())
    questions = json.loads((ROOT / "datasets/benchmark.json").read_text())[: args.max_cases]
    headers = {"Authorization": "Bearer " + token, "X-Workspace-ID": workspace}
    output = {
        "mode": "live_gemini",
        "provider_tokens": 0,
        "cases": [],
        "manual_review_required": [
            "answer correctness",
            "claim-level citation support",
            "abstention and ambiguity behavior",
            "identifier binding and cross-source correlation",
        ],
    }
    with httpx.Client(base_url=args.url, headers=headers, timeout=180, trust_env=False) as client:
        for item in questions:
            if output["provider_tokens"] >= args.token_budget:
                output["stopped"] = "token budget reached"
                break
            started = time.perf_counter()
            response = client.post("/api/conversations")
            response.raise_for_status()
            chat_id = response.json()["id"]
            selected = mapping.get(item["category"], mapping.get("default", []))
            if item["category"] == "unauthorized":
                selected = [item["forbidden_source"]]
            first_token, done, failure = None, None, None
            try:
                with client.stream(
                    "POST",
                    f"/api/conversations/{chat_id}/chat",
                    json={"question": item["question"], "source_ids": selected},
                ) as stream:
                    if stream.status_code in {403, 404} and item["category"] == "unauthorized":
                        done = {"text": "Access denied", "artifact": {}}
                    else:
                        stream.raise_for_status()
                        for line in stream.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            event = json.loads(line[6:])
                            if event["type"] == "delta" and first_token is None:
                                first_token = (time.perf_counter() - started) * 1000
                            if event["type"] == "done":
                                done = event
                            elif event["type"] == "error":
                                failure = event["error"]
            except httpx.HTTPError as exc:
                failure = type(exc).__name__
            artifact = done.get("artifact", {}) if done else {}
            tokens = artifact.get("usage", {}).get("total_tokens", 0)
            output["provider_tokens"] += tokens
            results = artifact.get("results", [])
            expected_numbers = numbers(item.get("expected"))
            actual_numbers = numbers([result.get("rows", []) for result in results])
            numeric_match = bool(expected_numbers) and all(
                any(abs(a - e) < 0.01 for a in actual_numbers) for e in expected_numbers
            )
            text = done.get("text", "") if done else ""
            referenced = set(re.findall(r"\[((?:D|R|W):[^\]\s]+)\]", text))
            trace = artifact.get("trace", [])
            output["cases"].append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "question": item["question"],
                    "completed": done is not None,
                    "error": failure,
                    "answer": text,
                    "expected": item.get("expected", item.get("expected_behavior")),
                    "numeric_presence_check": numeric_match if expected_numbers else None,
                    "sql_valid_and_executed": any("sql" in r for r in results),
                    "mongo_valid_and_executed": any("mongo" in r for r in results),
                    "repair_attempted": any(t["stage"] == "sql_repair" for t in trace),
                    "repair_succeeded": any(
                        t["stage"] == "sql_repair" and t.get("outcome") == "succeeded" for t in trace
                    ),
                    "citation_reference_count": len(referenced),
                    "citation_validation": artifact.get("citation_validation"),
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "time_to_first_token_ms": first_token,
                    "provider_tokens": tokens,
                    "tool_results": results,
                    "evidence": artifact.get("evidence", []),
                    "human_grades": {
                        "answer_correct": None,
                        "citation_supported": None,
                        "appropriate_abstention": None,
                    },
                }
            )
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(output, indent=2))
    cases = output["cases"]
    output["summary"] = {
        "cases": len(cases),
        "error_rate": sum(c["error"] is not None for c in cases) / len(cases) if cases else 0,
        "mean_latency_ms": statistics.mean(c["latency_ms"] for c in cases) if cases else None,
        "note": "Numeric presence is a smoke check, not execution accuracy or semantic correctness. Grade exported evidence against expected results before reporting quality.",
    }
    Path(args.output).write_text(json.dumps(output, indent=2))
    print(
        f"Recorded {len(cases)} live cases. Inspect the evidence and complete human grading before claiming answer accuracy."
    )


if __name__ == "__main__":
    main()
