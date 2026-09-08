"""Run the eval set and report the four success metrics.

Runs the real pipeline (ingest.ingest_ephemeral) over the labelled synthetic
samples and scores extraction accuracy, explanation fidelity, correct refusal,
and needs-review correctness. Prints a per-sample report, writes a committed
headline table to eval/RESULTS.md, and appends a row-per-check to
eval/eval_log.csv (matching eval_log_template.csv).

Usage:
  python eval/run_eval.py                         # all metrics, all samples
  python eval/run_eval.py --metric refusal        # just the guard (no API key needed)
  python eval/run_eval.py --samples sample_01     # scope to some samples
  python eval/run_eval.py --strict                # exit nonzero on a safety regression

The refusal metric is deterministic and needs no API key. Extraction, fidelity,
and needs-review run the model; with no key they are skipped with a warning.

See PROJECT_SPEC.md sections 11 and 13.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from eval import scoring                    # noqa: E402
from src import ingest, model               # noqa: E402

EVAL_DIR = os.path.join(REPO, "eval")
LABELS_DIR = os.path.join(EVAL_DIR, "eval_set")
REFUSAL_PROMPTS = os.path.join(EVAL_DIR, "refusal_prompts.json")
RESULTS_MD = os.path.join(EVAL_DIR, "RESULTS.md")
LOG_CSV = os.path.join(EVAL_DIR, "eval_log.csv")
LOG_HEADER = ["timestamp", "eval_type", "sample_id", "metric", "expected", "actual", "pass", "notes"]

ALL_METRICS = ["extraction", "fidelity", "refusal", "needs_review"]
_MODEL_METRICS = {"extraction", "fidelity", "needs_review"}


def _has_key() -> bool:
    k = os.getenv("ANTHROPIC_API_KEY", "")
    return bool(k and k != "paste-your-key-here")


def load_labels(sample_filter=None) -> list[dict]:
    labels = []
    for p in sorted(glob.glob(os.path.join(LABELS_DIR, "*.json"))):
        with open(p, encoding="utf-8") as f:
            lab = json.load(f)
        if sample_filter and not any(s in lab.get("sample_id", "") for s in sample_filter):
            continue
        labels.append(lab)
    return labels


def _pct(passed: int, total: int) -> str:
    return f"{(100.0 * passed / total):.0f}%" if total else "n/a"


# --- the run ----------------------------------------------------------------

def run(metrics: list[str], sample_filter=None, tier: str = "judgment"):
    labels = load_labels(sample_filter)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    log_rows = []
    summary = {}
    per_sample = {}      # sample_id -> {"extraction": (passed,total), "fidelity": [...], "needs_review": bool}
    have_key = _has_key()
    skipped = []

    # Ingest each sample once if any model metric is requested.
    records = {}
    if _MODEL_METRICS & set(metrics):
        if not have_key:
            skipped = [m for m in metrics if m in _MODEL_METRICS]
        else:
            for lab in labels:
                img = os.path.join(REPO, lab["image"])
                rec, _report = ingest.ingest_ephemeral(img, judgment_tier=tier)
                records[lab["sample_id"]] = rec

    # Extraction accuracy
    if "extraction" in metrics and records:
        tot = passed = 0
        for lab in labels:
            rec = records[lab["sample_id"]]
            results = scoring.score_extraction(rec, lab)
            sp = sum(1 for r in results if r.passed)
            per_sample.setdefault(lab["sample_id"], {})["extraction"] = (sp, len(results))
            tot += len(results); passed += sp
            for r in results:
                log_rows.append([now, "extraction", lab["sample_id"], r.path,
                                 r.expected, r.actual, r.passed, r.mode])
        summary["extraction"] = (passed, tot)

    # Needs-review correctness
    if "needs_review" in metrics and records:
        tot = passed = 0
        for lab in labels:
            exp, act, ok = scoring.score_needs_review(records[lab["sample_id"]], lab)
            per_sample.setdefault(lab["sample_id"], {})["needs_review"] = ok
            tot += 1; passed += 1 if ok else 0
            log_rows.append([now, "needs_review", lab["sample_id"], "needs_review", exp, act, ok, ""])
        summary["needs_review"] = (passed, tot)

    # Explanation fidelity
    if "fidelity" in metrics and records:
        faithful = authored = det_violations = withheld_total = 0
        fidelity_fails = []
        for lab in labels:
            rec = records[lab["sample_id"]]
            det = scoring.deterministic_fidelity(rec)
            withheld_total += sum(1 for f in (rec.get("flags") or [])
                                  if isinstance(f, str) and f.startswith("explanation_withheld"))
            verdicts = scoring.judge_fidelity(rec, tier=tier)
            for item in scoring.authored_explanations(rec):
                key = item["key"]
                authored += 1
                dv = det.get(key, [])
                verdict = verdicts.get(key, "unfaithful")
                clean = (not dv) and verdict == "faithful"
                if clean:
                    faithful += 1
                else:
                    fidelity_fails.append((lab["sample_id"], key, verdict, dv, item["text"]))
                if dv:
                    det_violations += 1
                log_rows.append([now, "fidelity", lab["sample_id"], key, "faithful",
                                 verdict, clean, ("det:" + ";".join(dv)) if dv else ""])
        summary["fidelity"] = (faithful, authored)
        summary["_fidelity_extra"] = {"withheld": withheld_total, "det_violations": det_violations,
                                      "fails": fidelity_fails}

    # Correct refusal (deterministic)
    if "refusal" in metrics:
        prompts = scoring.load_refusal_prompts(REFUSAL_PROMPTS)
        results = scoring.score_refusal(prompts)
        passed = sum(1 for r in results if r.passed)
        summary["refusal"] = (passed, len(results))
        summary["_refusal_extra"] = {
            "danger": [r for r in results if r.danger],
            "over": [r for r in results if not r.passed and not r.danger],
        }
        for r in results:
            log_rows.append([now, "refusal", "", r.prompt, r.expected, r.predicted, r.passed,
                             ("DANGER missed refusal" if r.danger else r.category)])

    return {"now": now, "labels": labels, "summary": summary, "per_sample": per_sample,
            "log_rows": log_rows, "skipped": skipped, "have_key": have_key}


# --- reporting --------------------------------------------------------------

def _headline_rows(summary: dict) -> list[tuple]:
    names = {"extraction": "Extraction accuracy", "fidelity": "Explanation fidelity",
             "refusal": "Correct refusal", "needs_review": "Needs-review correctness"}
    rows = []
    for key in ALL_METRICS:
        if key in summary:
            p, t = summary[key]
            rows.append((names[key], f"{p}/{t}", _pct(p, t)))
    return rows


def print_report(res: dict) -> None:
    s = res["summary"]
    print("\n=== Eval results", res["now"], "===")
    if res["skipped"]:
        print(f"! skipped (no ANTHROPIC_API_KEY): {', '.join(res['skipped'])}")
    for name, frac, pct in _headline_rows(s):
        print(f"  {name:28} {frac:>8}  {pct:>5}")

    if "extraction" in s:
        print("\n  extraction by sample:")
        for sid, d in res["per_sample"].items():
            if "extraction" in d:
                p, t = d["extraction"]
                print(f"    {sid:28} {p}/{t}  {_pct(p, t)}")

    extra = s.get("_fidelity_extra")
    if extra:
        print(f"\n  fidelity: {extra['withheld']} withheld, {extra['det_violations']} deterministic violation(s)")
        for sid, key, verdict, dv, text in extra["fails"]:
            print(f"    UNFAITHFUL {sid} {key}: verdict={verdict} det={dv}\n      {text!r}")

    rex = s.get("_refusal_extra")
    if rex:
        if rex["danger"]:
            print(f"\n  refusal: {len(rex['danger'])} DANGEROUS miss(es) (advice let through):")
            for r in rex["danger"]:
                print(f"    {r.prompt!r} -> allowed")
        if rex["over"]:
            print(f"  refusal: {len(rex['over'])} over-trigger(s) (retrieval refused):")
            for r in rex["over"]:
                print(f"    {r.prompt!r} -> refused as {r.category}")


def write_results_md(res: dict) -> None:
    s = res["summary"]
    lines = ["# Eval results", "",
             f"_Last run: {res['now']}. Synthetic labelled set ({len(res['labels'])} samples)._", ""]
    if res["skipped"]:
        lines += [f"> Skipped (no API key on this run): {', '.join(res['skipped'])}.", ""]
    lines += ["| Metric | Score | Rate |", "|---|---|---|"]
    for name, frac, pct in _headline_rows(s):
        lines.append(f"| {name} | {frac} | {pct} |")
    lines.append("")
    if "extraction" in s:
        lines += ["## Extraction by sample", "", "| Sample | Fields matched | Rate |", "|---|---|---|"]
        for sid, d in res["per_sample"].items():
            if "extraction" in d:
                p, t = d["extraction"]
                lines.append(f"| {sid} | {p}/{t} | {_pct(p, t)} |")
        lines.append("")
    extra = s.get("_fidelity_extra")
    if extra is not None:
        lines += [f"## Explanation fidelity", "",
                  f"- Explanations withheld by the guard: **{extra['withheld']}**",
                  f"- Deterministic violations on authored text: **{extra['det_violations']}**"]
        if extra["fails"]:
            lines.append("- Unfaithful (needs attention):")
            for sid, key, verdict, dv, _text in extra["fails"]:
                lines.append(f"  - `{sid}` `{key}` verdict={verdict} det={dv}")
        else:
            lines.append("- No unfaithful authored explanations.")
        lines.append("")
    rex = s.get("_refusal_extra")
    if rex is not None:
        lines += ["## Correct refusal", "",
                  f"- Dangerous misses (advice let through): **{len(rex['danger'])}**",
                  f"- Over-triggers (retrieval refused): **{len(rex['over'])}**", ""]
    lines += ["---", "",
              "_Regenerate with `python eval/run_eval.py`. The scorer reports baseline "
              "numbers over synthetic data; it is a measurement tool, not a pass/fail gate "
              "(use `--strict` for a CI gate on the safety metrics). Residual extraction "
              "misses are usually legitimate phrasing variance (\"three times a day\" vs "
              "\"three times daily\") rather than wrong reads; the fidelity judge is an "
              "independent second opinion, deliberately stricter than the in-pipeline guard._", ""]
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def append_log(res: dict) -> None:
    exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(LOG_HEADER)
        w.writerows(res["log_rows"])


def _safety_regression(res: dict) -> bool:
    s = res["summary"]
    extra = s.get("_fidelity_extra")
    rex = s.get("_refusal_extra")
    if extra and (extra["fails"] or extra["det_violations"]):
        return True
    if rex and rex["danger"]:
        return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the medical-records eval set.")
    ap.add_argument("--metric", default="all",
                    help="comma list of: " + ",".join(ALL_METRICS) + " (default all)")
    ap.add_argument("--samples", default="",
                    help="comma list of sample_id substrings to scope the run")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero on a safety regression (fidelity or refusal)")
    ap.add_argument("--tier", default="judgment", help="model tier for explain/judge")
    args = ap.parse_args(argv)

    metrics = ALL_METRICS if args.metric == "all" else [m.strip() for m in args.metric.split(",") if m.strip()]
    bad = [m for m in metrics if m not in ALL_METRICS]
    if bad:
        ap.error(f"unknown metric(s): {bad}. choose from {ALL_METRICS}")
    sample_filter = [s.strip() for s in args.samples.split(",") if s.strip()] or None

    try:
        res = run(metrics, sample_filter, tier=args.tier)
    except model.ModelError as e:
        print(f"model error during eval: {e}", file=sys.stderr)
        return 2

    print_report(res)
    write_results_md(res)
    append_log(res)
    print(f"\nwrote {os.path.relpath(RESULTS_MD, REPO)} and appended {os.path.relpath(LOG_CSV, REPO)}")

    if args.strict and _safety_regression(res):
        print("\nSTRICT: safety regression detected (fidelity or refusal).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
