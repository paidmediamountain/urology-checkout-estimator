"""Score a coder against the gold set.

  python3 -m evals baseline            run the keyword baseline and score it
  python3 -m evals score FILE [NAME]   score a predictions file (JSON lines: {"id", "lines": [{"hcpcs_cd", "modifier"}]})
  python3 -m evals prompts             write the exact prompt for every case, to run through any model
"""
import json
import sys
from pathlib import Path

from estimator.load import load_price_book

from .coding import PROMPT_VERSION, allowed_codes, baseline_coder, build_prompt
from .score import load_cases, report, score

OUT = Path(__file__).resolve().parent.parent / "out"
CLINIC = {"state": "MN", "locality_cd": "00", "state_fips": "27"}


def run(name, predictions):
    metrics = score(load_cases(), predictions, load_price_book(**CLINIC), set(allowed_codes()))
    text = report(name, metrics)
    OUT.mkdir(exist_ok=True)
    (OUT / f"eval_{name}.md").write_text(text)
    (OUT / f"eval_{name}.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(text)


def main(argv):
    command = argv[0] if argv else ""
    if command == "baseline":
        run("keyword-baseline", {c["id"]: baseline_coder(c["note"]) for c in load_cases()})
    elif command == "score" and len(argv) >= 2:
        rows = [json.loads(line) for line in Path(argv[1]).read_text().splitlines() if line.strip()]
        run(argv[2] if len(argv) > 2 else Path(argv[1]).stem, {r["id"]: r["lines"] for r in rows})
    elif command == "prompts":
        OUT.mkdir(exist_ok=True)
        path = OUT / "eval_prompts.jsonl"
        path.write_text("".join(
            json.dumps({"id": c["id"], "prompt_version": PROMPT_VERSION, "prompt": build_prompt(c["note"])}) + "\n"
            for c in load_cases()))
        print(f"wrote {path.relative_to(OUT.parent)}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
