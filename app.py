"""Compare explicit pricing snapshots and prepare local role-specific artifacts."""
import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import difflib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROLES = {"Atlas": "CEO", "Mara": "PM", "Theo": "engineer", "Ren": "operations"}
ROOMS = {"intel": set(ROLES), "alerts": {"Atlas", "Ren"}}


def public_url(value):
    if not isinstance(value, str):
        raise ValueError("source URL must be a string")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("use a credential-free HTTPS source URL")
    return value


def load_snapshot(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("snapshot exceeds 2 MiB")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("source_kind") not in ("synthetic", "human-normalized"):
        raise ValueError("declare synthetic or human-normalized source_kind")
    if not isinstance(value.get("competitor"), str) or not value["competitor"].strip():
        raise ValueError("competitor must be nonempty")
    date.fromisoformat(value["date"])
    if not isinstance(value.get("sources"), list) or not value["sources"]:
        raise ValueError("snapshot needs source URLs")
    for source in value["sources"]:
        public_url(source)
    if not isinstance(value.get("plans"), list) or not value["plans"]:
        raise ValueError("snapshot needs plans")
    seen = set()
    for plan in value["plans"]:
        if not isinstance(plan, dict) or not isinstance(plan.get("name"), str) or not plan["name"].strip() or plan["name"] in seen:
            raise ValueError("plan names must be nonempty and unique")
        seen.add(plan["name"])
        if plan.get("currency") not in ("USD", "EUR", "GBP") or plan.get("period") not in ("month", "year"):
            raise ValueError("explicit supported currency and billing period required")
        if isinstance(plan.get("amount"), bool) or not isinstance(plan.get("amount"), (str, int, float)):
            raise ValueError("plan amount must be numeric")
        try:
            amount = Decimal(str(plan["amount"]))
            if not amount.is_finite() or not 0 <= amount <= 1_000_000_000 or amount.as_tuple().exponent < -6:
                raise ValueError("invalid or excessive plan amount")
        except InvalidOperation as exc:
            raise ValueError("invalid plan amount") from exc
        plan["amount"] = format(amount, "f")
        if not isinstance(plan.get("features"), list) or any(not isinstance(f, str) or not f.strip() for f in plan["features"]):
            raise ValueError("features must be nonempty text entries")
        plan["features"] = sorted(set(plan["features"]))
    return value


def compare(baseline, current):
    if baseline["competitor"] != current["competitor"]:
        raise ValueError("snapshots must concern the same competitor")
    if date.fromisoformat(current["date"]) <= date.fromisoformat(baseline["date"]):
        raise ValueError("current snapshot must be later than baseline")
    before = {p["name"]: p for p in baseline["plans"]}
    after = {p["name"]: p for p in current["plans"]}
    changes = []
    for name in sorted(before.keys() | after.keys()):
        if name not in before or name not in after:
            changes.append({"plan": name, "kind": "added" if name in after else "removed", "before": before.get(name), "after": after.get(name)})
            continue
        old, new = before[name], after[name]
        for field in ("currency", "period", "amount", "features"):
            equal = Decimal(old[field]) == Decimal(new[field]) if field == "amount" else old[field] == new[field]
            if not equal:
                changes.append({"plan": name, "kind": field, "before": old[field], "after": new[field]})
    return {"competitor": current["competitor"], "baseline_date": baseline["date"], "current_date": current["date"],
            "source_kind": current["source_kind"], "quality_evidence": False, "changes": changes,
            "source_urls": current["sources"], "roles": ROLES,
            "handoffs": ["Atlas requests tracking", "Mara defines brief", "Theo compares explicit snapshots", "Ren prepares a local private alert"],
            "delivery": "local files only; no Alook messages or external notifications"}


def markdown(snapshot):
    lines = [f"# {snapshot['competitor']} — {snapshot['date']}", f"Source kind: {snapshot['source_kind']}"]
    lines += [f"- {p['name']}: {p['currency']} {p['amount']}/{p['period']}; {', '.join(p['features'])}" for p in sorted(snapshot["plans"], key=lambda p: p["name"])]
    return "\n".join(lines + ["", "Sources:", *snapshot["sources"]]) + "\n"


def save_run(directory, baseline, current, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "snapshots").mkdir()
    (directory / "reports").mkdir()
    for snapshot in (baseline, current):
        (directory / "snapshots" / f"{snapshot['date']}.md").write_text(markdown(snapshot), encoding="utf-8")
    delta = "".join(difflib.unified_diff(markdown(baseline).splitlines(True), markdown(current).splitlines(True), fromfile=baseline["date"], tofile=current["date"]))
    (directory / "reports" / f"delta-{current['date']}.md").write_text("# Pricing changes\n\n```diff\n" + delta + "```\n", encoding="utf-8")
    (directory / "room-intel.md").write_text("# Local intel\n\n" + json.dumps(result, indent=2), encoding="utf-8")
    (directory / "room-alerts.md").write_text("# Local alert draft\n\n" + json.dumps(result["changes"], indent=2), encoding="utf-8")
    (directory / "run.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def read_room(directory, room, agent):
    if room not in ROOMS or agent not in ROOMS[room]:
        raise PermissionError("role is not a member of this room")
    return (Path(directory) / f"room-{room}.md").read_text(encoding="utf-8")


def native_commands(url):
    url = public_url(url)
    return {"requires": "installed Nimble CLI and process NIMBLE_API_KEY; commands are not executed here",
            "map": ["nimble", "--client-source", "nimble-agent-skills", "map", "--url", url, "--sitemap", "only", "--limit", "200"],
            "extract": ["nimble", "--client-source", "nimble-agent-skills", "extract", "--url", url, "--format", "markdown"],
            "normalization": "Preserve raw output as evidence, then make a human-normalized snapshot; this project does not invent an API response schema or autonomous price extractor."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode")
    run = sub.add_parser("demo")
    run.add_argument("--baseline", type=Path, default=HERE / "fixtures/baseline.json")
    run.add_argument("--current", type=Path, default=HERE / "fixtures/current.json")
    run.add_argument("--output-dir", type=Path)
    native = sub.add_parser("native-commands")
    native.add_argument("--url", required=True)
    view = sub.add_parser("view")
    view.add_argument("--run-dir", required=True)
    view.add_argument("--room", choices=tuple(ROOMS), required=True)
    view.add_argument("--agent", choices=tuple(ROLES), required=True)
    args = parser.parse_args(argv or ["demo"])
    try:
        if args.mode == "native-commands":
            result = native_commands(args.url)
        elif args.mode == "view":
            print(read_room(args.run_dir, args.room, args.agent))
            return 0
        else:
            before, after = load_snapshot(args.baseline), load_snapshot(args.current)
            result = compare(before, after)
            if args.output_dir:
                save_run(args.output_dir, before, after, result)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
