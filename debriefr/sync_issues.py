"""Sync a meeting summary's action items to GitHub Issues + Projects v2.

Three modes:

* **Setup:** create a new GitHub Project v2 with the opinionated schema
  (Priority single-select, Due date field). Run once per project::

      debriefr sync-issues --setup --title "My Project - Action Items"

* **Link:** validate an existing GitHub Project and create missing fields::

      debriefr sync-issues --link 42

* **Sync:** for each meeting summary, create issues for each action-items row
  and populate the project board fields::

      debriefr sync-issues summary.md --repo owner/name --gh-project 42 \\
          --handle Alice=alice-gh --handle Bob=bob-gh

Requires the ``gh`` CLI to be authenticated (``gh auth status``).

Idempotency:
  After a successful sync, a ``<!-- synced:#N -->`` marker is appended to
  each action-items row. The task text is also replaced with a markdown link
  to the created issue. Subsequent runs skip rows that already carry a marker.

  Rows whose Priority is ``done`` are never synced: they get a ``<!-- nosync -->``
  marker instead so they stay in the summary for the record but are skipped by
  this and every future run.

  The Action Items table's leading ``#`` column (a sequential row number) is
  ignored here — rows are matched by header name, so the extra column is fine.

Opinionated schema (don't try to customize, just match):

* Priority single-select with options ``low``, ``medium``, ``high``, ``urgent``.
  Set directly on the board's Priority field from the row's value.
* Due: date field.
* Status (optional): In Progress for urgent, Todo for the rest.
* Iteration (optional): if present, the sync matches each item's Due date to
  its enclosing iteration and assigns it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


PRIORITY_OPTIONS = ["low", "medium", "high", "urgent"]
SYNC_MARKER_RE = re.compile(r"<!--\s*synced:#(\d+)\s*-->")

# A row whose Priority is "done" is already completed: it stays in the summary
# for the record but is never turned into a GitHub issue. On sync it gets a
# ``<!-- nosync -->`` marker so subsequent runs skip it (mirrors ``synced:#N``).
DONE_PRIORITY = "done"
NOSYNC_MARKER = " <!-- nosync -->"
NOSYNC_MARKER_RE = re.compile(r"<!--\s*nosync\s*-->")

PRIORITY_MAP = {"urgent": "urgent", "high": "high", "medium": "medium", "low": "low"}
STATUS_MAP = {"urgent": "In Progress", "high": "Todo", "medium": "Todo", "low": "Todo"}

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _bold(t: str) -> str:   return _c("1", t)
def _red(t: str) -> str:    return _c("31", t)
def _yellow(t: str) -> str: return _c("33", t)
def _cyan(t: str) -> str:   return _c("36", t)
def _dim(t: str) -> str:    return _c("2", t)


def sh(*args, check: bool = True, json_out: bool = False):
    """Run a command and return stdout (or parsed JSON). Exits on non-zero."""
    r = subprocess.run(list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(args)}\nstderr: {r.stderr.strip()}")
    return json.loads(r.stdout) if json_out else r.stdout


def current_github_user() -> str:
    return sh("gh", "api", "user", "--jq", ".login").strip()


# -------- Setup mode --------

def setup_project(title: str) -> int:
    """Create a new GitHub Project v2 with Priority + Due fields. Returns the project number."""
    owner = current_github_user()
    print(f"Creating Project v2: '{title}' (owner: {owner})")
    created = sh("gh", "project", "create",
                 "--owner", owner, "--title", title,
                 "--format", "json", json_out=True)
    number = created["number"]
    print(f"  created #{number}")

    print("Adding Priority field (single-select)...")
    sh("gh", "project", "field-create", str(number),
       "--owner", owner, "--name", "Priority",
       "--data-type", "SINGLE_SELECT",
       "--single-select-options", ",".join(PRIORITY_OPTIONS))

    print("Adding Due field (date)...")
    sh("gh", "project", "field-create", str(number),
       "--owner", owner, "--name", "Due", "--data-type", "DATE")

    print(f"\nProject #{number} ready.")
    print(f"View: https://github.com/users/{owner}/projects/{number}")
    print(
        "\nNote: `gh` cannot create Iteration fields directly. To enable "
        "iteration assignment, open the project in the browser: "
        "Settings -> Fields -> New field -> Iteration."
    )
    return number


def link_project(project_number: int) -> None:
    """Validate an existing GitHub Project v2, creating Priority/Due fields if missing."""
    owner = current_github_user()
    view = sh("gh", "project", "view", str(project_number),
              "--owner", owner, "--format", "json", json_out=True)
    title = view.get("title", "(untitled)")
    print(f"Linking to existing project #{project_number}: '{title}'")

    fields_list = sh("gh", "project", "field-list", str(project_number),
                     "--owner", owner, "--format", "json", json_out=True)
    fields = {f["name"].lower(): f for f in fields_list.get("fields", [])}

    if "priority" not in fields:
        print("  no Priority field found — creating single-select (low/medium/high/urgent)")
        sh("gh", "project", "field-create", str(project_number),
           "--owner", owner, "--name", "Priority",
           "--data-type", "SINGLE_SELECT",
           "--single-select-options", ",".join(PRIORITY_OPTIONS))
    else:
        existing_opts = [o["name"].lower() for o in fields["priority"].get("options", [])]
        missing = [o for o in PRIORITY_OPTIONS if o not in existing_opts]
        if missing:
            print(f"  ⚠ Priority field exists but missing options: {missing}. "
                  f"The sync script will skip unknown options. You may want to add them in the UI.")
        else:
            print("  Priority field found with matching options.")

    if "due" not in fields:
        print("  no Due field found — creating date field")
        sh("gh", "project", "field-create", str(project_number),
           "--owner", owner, "--name", "Due", "--data-type", "DATE")
    else:
        dtype = fields["due"].get("dataType", "").upper()
        if dtype != "DATE":
            print(f"  ⚠ 'Due' field exists but has type {dtype}, not DATE. "
                  f"Sync will skip setting Due.")
        else:
            print("  Due field found (date type).")

    print(f"\nProject #{project_number} validated.")
    print(f"View: https://github.com/users/{owner}/projects/{project_number}")


# -------- Parsing --------

ACTION_SECTION_RE = re.compile(r"^##\s+Action Items\s*$", re.MULTILINE)


def parse_action_items(summary_text: str) -> tuple[list[dict], list[str]]:
    """Extract rows from the Action Items table.

    Returns ``(rows, raw_row_lines)`` where rows are dicts keyed by header name,
    and raw lines are kept in order for sync-marker rewriting later.
    """
    m = ACTION_SECTION_RE.search(summary_text)
    if not m:
        return [], []
    section = summary_text[m.end():]
    next_h = re.search(r"^##\s", section, re.MULTILINE)
    if next_h:
        section = section[:next_h.start()]

    pipe_lines = [l.strip() for l in section.splitlines() if l.strip().startswith("|")]
    if len(pipe_lines) < 3:
        return [], []
    header_cells = [c.strip() for c in pipe_lines[0].strip("|").split("|")]
    rows: list[dict] = []
    row_lines: list[str] = []
    for line in pipe_lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(header_cells):
            continue
        rows.append(dict(zip(header_cells, cells)))
        row_lines.append(line)
    return rows, row_lines


def row_synced_issue(row_line: str) -> int | None:
    m = SYNC_MARKER_RE.search(row_line)
    return int(m.group(1)) if m else None


def resolve_owners(owner_str: str, handles: dict[str, str]) -> list[dict]:
    """Split a multi-owner field into per-owner info using a flat name -> handle map."""
    parts = re.split(r"\s*[&,]\s*", owner_str.strip())
    result = []
    for raw in parts:
        name = raw.strip()
        if not name:
            continue
        result.append({"name": name, "github": handles.get(name)})
    return result


def normalize_priority(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s in PRIORITY_OPTIONS else "medium"


def is_done(priority: str) -> bool:
    """True if a row's Priority marks it already completed (kept, not synced)."""
    return (priority or "").strip().lower() == DONE_PRIORITY


ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def normalize_date(s: str) -> str | None:
    m = ISO_DATE_RE.search(s or "")
    return m.group(1) if m else None


def is_past_due(iso_date: str | None) -> bool:
    if not iso_date:
        return False
    from datetime import date
    try:
        return date.fromisoformat(iso_date) < date.today()
    except ValueError:
        return False


def normalize_words(text: str) -> set[str]:
    return set(re.sub(r"[^\w\s]", "", text.lower()).split())


def find_duplicates(task: str, existing_issues: list[dict], threshold: float = 0.45) -> list[dict]:
    """Return existing open issues whose titles have high Jaccard overlap with ``task``."""
    task_words = normalize_words(task)
    if not task_words:
        return []
    hits = []
    for issue in existing_issues:
        issue_words = normalize_words(issue.get("title", ""))
        if not issue_words:
            continue
        jaccard = len(task_words & issue_words) / len(task_words | issue_words)
        if jaccard >= threshold:
            hits.append(issue)
    return hits


# -------- gh API wrappers --------

def fetch_open_issues(repo: str) -> list[dict]:
    raw = sh("gh", "issue", "list", "--repo", repo, "--state", "open",
             "--limit", "200", "--json", "number,title", json_out=True)
    return raw if isinstance(raw, list) else []


def fetch_project_metadata(owner: str, number: int) -> tuple[str, dict]:
    view = sh("gh", "project", "view", str(number),
              "--owner", owner, "--format", "json", json_out=True)
    fields_list = sh("gh", "project", "field-list", str(number),
                     "--owner", owner, "--format", "json", json_out=True)
    fields = {f["name"].lower(): f for f in fields_list.get("fields", [])}
    return view["id"], fields


def fetch_iterations(owner: str, project_number: int) -> list[dict]:
    query = """
    query($login: String!, $num: Int!) {
      user(login: $login) {
        projectV2(number: $num) {
          field(name: "Iteration") {
            ... on ProjectV2IterationField {
              configuration {
                iterations { id title startDate duration }
              }
            }
          }
        }
      }
    }"""
    result = sh("gh", "api", "graphql",
                "-f", f"query={query}",
                "-f", f"login={owner}",
                "-F", f"num={project_number}",
                json_out=True, check=False)
    try:
        return result["data"]["user"]["projectV2"]["field"]["configuration"]["iterations"]
    except (KeyError, TypeError):
        return []


def find_iteration_for_date(iso_date: str | None, iterations: list[dict]) -> str | None:
    if not iso_date:
        return None
    from datetime import date, timedelta
    try:
        target = date.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return None
    for it in iterations:
        start = date.fromisoformat(it["startDate"])
        end = start + timedelta(days=it["duration"])
        if start <= target < end:
            return it["id"]
    return None


def set_field_iteration(item_id, project_id, field_id, iteration_id):
    sh("gh", "project", "item-edit",
       "--id", item_id, "--project-id", project_id,
       "--field-id", field_id, "--iteration-id", iteration_id)


def set_field_single_select(item_id, project_id, field, option_name):
    opt_id = next(
        (o["id"] for o in field.get("options", []) if o["name"].lower() == option_name.lower()),
        None,
    )
    if not opt_id:
        print(f"      [warn] option '{option_name}' not found on field '{field['name']}'; skipping")
        return
    sh("gh", "project", "item-edit",
       "--id", item_id, "--project-id", project_id,
       "--field-id", field["id"],
       "--single-select-option-id", opt_id)


def set_field_date(item_id, project_id, field, iso_date):
    sh("gh", "project", "item-edit",
       "--id", item_id, "--project-id", project_id,
       "--field-id", field["id"], "--date", iso_date)


def fetch_project_items(owner: str, number: int) -> dict[int, str]:
    raw = sh("gh", "project", "item-list", str(number),
             "--owner", owner, "--format", "json", json_out=True)
    mapping = {}
    for item in raw.get("items", []):
        c = item.get("content", {})
        num = c.get("number")
        if num:
            mapping[num] = item["id"]
    return mapping


def fetch_issue_body(repo: str, number: int) -> str:
    data = sh("gh", "issue", "view", str(number),
              "--repo", repo, "--json", "body", json_out=True)
    return data.get("body", "") or ""


def update_issue_body_with_notes(repo: str, issue_num: int, new_notes: str, summary_name: str) -> bool:
    """Append meeting context to an existing issue's body if not already present."""
    body = fetch_issue_body(repo, issue_num)
    if new_notes and new_notes not in body:
        appendix = (
            f"\n\n---\n"
            f"_Updated from `{summary_name}`:_\n"
            f"_{new_notes}_"
        )
        sh("gh", "issue", "edit", str(issue_num),
           "--repo", repo, "--body", body + appendix)
        return True
    return False


# -------- Sync orchestration --------

def sync_summary(
    summary_path: Path,
    repo: str,
    project_number: int,
    handles: dict[str, str],
    dry_run: bool = False,
    assume_yes: bool = False,
) -> None:
    """Sync one summary's Action Items to GitHub Issues + Project v2."""
    text = summary_path.read_text()
    rows, row_lines = parse_action_items(text)
    if not rows:
        print(f"No action items table found in {summary_path.name}.")
        return

    to_sync: list[tuple[dict, str]] = []
    already: list[tuple[dict, int | None]] = []
    done_rows: list[tuple[dict, str]] = []
    for row, line in zip(rows, row_lines):
        existing = row_synced_issue(line)
        if existing:
            already.append((row, existing))
        elif NOSYNC_MARKER_RE.search(line):
            already.append((row, None))
        elif is_done(row.get("Priority", "")):
            done_rows.append((row, line))
        else:
            to_sync.append((row, line))

    owner = current_github_user()

    print(f"Action items in {summary_path.name}: {len(rows)} total "
          f"({len(to_sync)} new, {len(already)} already synced, {len(done_rows)} done)")
    if already:
        for row, num in already:
            tag = f"synced #{num}" if num else "done, not synced"
            print(f"  [{tag}] {row.get('Task', '')[:70]}")
    if done_rows:
        for row, _ in done_rows:
            print(f"  [done — kept in summary, not synced] {row.get('Task', '')[:70]}")

    if not to_sync:
        # Nothing to create/update; just persist the nosync markers for done rows.
        if not done_rows:
            return
        if dry_run:
            print(f"\n[dry-run] {len(done_rows)} done row(s) would be marked "
                  f"'<!-- nosync -->'; nothing synced.")
            return
        new_text = text
        for row, line in done_rows:
            if NOSYNC_MARKER not in line:
                new_text = new_text.replace(line, line + NOSYNC_MARKER, 1)
        if new_text != text:
            summary_path.write_text(new_text)
            print(f"\nMarked {len(done_rows)} done row(s) as not-synced in "
                  f"{summary_path.name}.")
        return

    print("Checking for duplicates against open issues...")
    existing_issues = fetch_open_issues(repo)
    print(f"  {len(existing_issues)} open issue(s) in {repo}")

    plan: list[dict] = []
    for row, line in to_sync:
        task = row.get("Task", "")
        due = normalize_date(row.get("Due", ""))
        priority = normalize_priority(row.get("Priority", ""))
        owners = resolve_owners(row.get("Owner", ""), handles)
        dupes = find_duplicates(task, existing_issues)
        plan.append({
            "row": row, "line": line, "task": task, "due": due,
            "priority": priority, "owners": owners,
            "action": "update" if dupes else "create",
            "dup_target": dupes[0] if dupes else None,
        })

    update_count = sum(1 for p in plan if p["action"] == "update")
    past_due_count = 0
    print("\nPlan:")
    for i, p in enumerate(plan, 1):
        handles_str = ", ".join(
            _cyan(f"@{o['github']}") if o["github"] else _dim(f"{o['name']}(?)")
            for o in p["owners"]
        ) or _dim("unassigned")
        past = is_past_due(p["due"])
        if past:
            past_due_count += 1
        tags = ""
        if past:
            tags += " " + _red("[PAST DUE]")
        if p["action"] == "update":
            tags += " " + _yellow(f"[UPDATE #{p['dup_target']['number']}]")
        action_label = _dim("update") if p["action"] == "update" else "create"
        print(f"  [{i}] {_bold(p['task'][:70])}")
        print(f"      {action_label} | assignees: {handles_str} "
              f"| priority: {p['priority']} "
              f"| due: {p['due'] or '—'}{tags}")

    warnings = []
    if past_due_count:
        warnings.append(f"{past_due_count} item(s) are past due")
    if update_count:
        warnings.append(f"{update_count} item(s) will update existing issues")
    if warnings:
        print(_yellow(f"\n  ⚠ {'; '.join(warnings)}."))

    if dry_run:
        print("\n[dry-run] nothing created or updated.")
        return

    if not assume_yes:
        if not sys.stdin.isatty():
            sys.exit("stdin is not a terminal; pass --yes to skip the confirmation")
        if input("\nProceed? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return

    project_id, fields = fetch_project_metadata(owner, project_number)
    priority_field = fields.get("priority")
    due_field = fields.get("due")
    status_field = fields.get("status")
    iteration_field = fields.get("iteration")
    iterations = fetch_iterations(owner, project_number) if iteration_field else []
    item_map = fetch_project_items(owner, project_number)

    new_text = text
    for p in plan:
        task = p["task"].strip()
        if not task:
            continue
        notes = p["row"].get("Notes", "") or ""
        due = p["due"]
        priority = p["priority"]
        owner_handles = [o["github"] for o in p["owners"] if o["github"]]
        unresolved = [o["name"] for o in p["owners"] if not o["github"]]
        mapped_status = STATUS_MAP.get(priority, "Ready")

        mapped_priority = PRIORITY_MAP.get(priority)

        if p["action"] == "update":
            issue_num = p["dup_target"]["number"]
            print(f"\nUpdating #{issue_num}: {task[:60]}...")
            updated = update_issue_body_with_notes(repo, issue_num, notes, summary_path.name)
            if updated:
                print(f"  appended notes to #{issue_num}")
            else:
                print(f"  no new notes to append")
            item_id = item_map.get(issue_num)
            if item_id:
                if priority_field and mapped_priority:
                    set_field_single_select(item_id, project_id, priority_field, mapped_priority)
                if due_field and due:
                    set_field_date(item_id, project_id, due_field, due)
                if status_field:
                    set_field_single_select(item_id, project_id, status_field, mapped_status)
                iter_id = find_iteration_for_date(due, iterations) if due and iteration_field else None
                if iter_id:
                    set_field_iteration(item_id, project_id, iteration_field["id"], iter_id)
                print(f"  updated project fields (priority={mapped_priority or '—'}, "
                      f"due={due or '—'}, status={mapped_status}"
                      f"{', iteration assigned' if iter_id else ''})")
            else:
                print(f"  ⚠ #{issue_num} not found in project; fields not updated")
            marker = f" <!-- synced:#{issue_num} -->"
        else:
            body_parts = []
            if notes:
                body_parts.append(f"**Notes:** {notes}")
            body_parts.append(f"\n_Source: meeting summary `{summary_path.name}`_")
            if due:
                body_parts.append(f"_Due: {due}_")
            body_parts.append(f"_Priority: {priority}_")
            if unresolved:
                body_parts.append(f"_Also involves (no GitHub handle): {', '.join(unresolved)}_")
            body = "\n".join(body_parts)

            args = ["gh", "issue", "create",
                    "--repo", repo, "--title", task, "--body", body]
            for h in owner_handles:
                args += ["--assignee", h]
            print(f"\nCreating issue: {task[:60]}...")
            url = sh(*args).strip().splitlines()[-1]
            issue_num = int(url.rsplit("/", 1)[-1])
            print(f"  -> {url}")

            add_out = sh("gh", "project", "item-add", str(project_number),
                         "--owner", owner, "--url", url,
                         "--format", "json", json_out=True)
            item_id = add_out["id"]

            if priority_field and mapped_priority:
                set_field_single_select(item_id, project_id, priority_field, mapped_priority)
            if due_field and due:
                set_field_date(item_id, project_id, due_field, due)
            if status_field:
                set_field_single_select(item_id, project_id, status_field, mapped_status)
            iter_id = find_iteration_for_date(due, iterations) if due and iteration_field else None
            if iter_id:
                set_field_iteration(item_id, project_id, iteration_field["id"], iter_id)
                print(f"  assigned to iteration")
            marker = f" <!-- synced:#{issue_num} -->"

        issue_url = f"https://github.com/{repo}/issues/{issue_num}"
        linked_task = f"[{task}]({issue_url})"
        modified_line = p["line"].replace(task, linked_task, 1)
        new_text = new_text.replace(p["line"], modified_line + marker, 1)

    # Done rows: keep them in the summary but mark them so they're never synced.
    for row, line in done_rows:
        if NOSYNC_MARKER not in line:
            new_text = new_text.replace(line, line + NOSYNC_MARKER, 1)

    if new_text != text:
        summary_path.write_text(new_text)
        print(f"\nSync markers written to {summary_path.name}.")


def parse_handle_args(handle_args: list[str]) -> dict[str, str]:
    """Parse ``--handle Name=github-handle`` pairs into a dict."""
    handles = {}
    for pair in handle_args:
        if "=" not in pair:
            sys.exit(f"Invalid --handle '{pair}'. Expected Name=github-handle.")
        name, gh = pair.split("=", 1)
        handles[name.strip()] = gh.strip()
    return handles
