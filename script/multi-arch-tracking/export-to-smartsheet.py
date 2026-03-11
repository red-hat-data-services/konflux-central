#!/usr/bin/env python3
"""
Smartsheet Architecture Support Exporter

Creates a Smartsheet showing which CPU architectures each RHOAI component is
built for, with one row per component and checkbox columns grouped by branch.

Cell values:
    - Checked checkbox  : component is built for this architecture
    - Clickable Jira link (e.g. RHOAIENG-12345) : specific exception tracked
      in Jira -- the link opens the issue directly
    - "N/A"             : accelerator incompatibility (text in checkbox column)
    - "XXX"             : exception without a Jira issue assigned yet
    - Empty / unchecked : not currently built, no known exception

Requires:
    - SMARTSHEET_API_TOKEN environment variable
    - smartsheet-python-sdk  (pip install smartsheet-python-sdk)
    - PyYAML                 (pip install pyyaml)
    - Python 3.11+ or tomli  (pip install tomli)

Usage:
    export SMARTSHEET_API_TOKEN="your-token"
    ./script/multi-arch-tracking/export-to-smartsheet.py rhoai-3.4-ea.1
"""

import argparse
from datetime import date
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import smartsheet
import yaml

ARCH_COLUMNS = ["amd64", "arm64", "ppc64le", "s390x"]

# Smartsheet format string: position 6 = horizontalAlign (2 = center)
FMT_CENTER = ",,,,,,2,,,,,,,,,,"


# ---------------------------------------------------------------------------
# Pipeline parsing helpers (reused from generate-table.py)
# ---------------------------------------------------------------------------

def normalize_architecture(platform: str) -> str:
    if "/" in platform:
        arch = platform.split("/")[-1]
    else:
        arch = platform
    if arch == "x86_64":
        arch = "amd64"
    return arch


def extract_component_name(output_image: str) -> str:
    if output_image.startswith("quay.io/rhoai/"):
        name = output_image[len("quay.io/rhoai/"):]
    else:
        name = output_image
    if ":" in name:
        name = name.split(":")[0]
    return name


def parse_pipelinerun_from_content(file_path: str, content: str):
    try:
        data = yaml.safe_load(content)
        if not data or "spec" not in data or "params" not in data["spec"]:
            return None, set()

        output_image = None
        platforms = []
        for param in data["spec"]["params"]:
            if param.get("name") == "output-image":
                output_image = param.get("value")
            elif param.get("name") == "build-platforms":
                platforms = param.get("value", [])

        if not output_image or not platforms:
            return None, set()

        component_name = extract_component_name(output_image)
        architectures = {normalize_architecture(p) for p in platforms}
        return component_name, architectures
    except Exception as e:
        print(f"Warning: Error parsing {file_path}: {e}", file=sys.stderr)
        return None, set()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def validate_git_branch(repo_dir: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo_dir, capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def find_pipelinerun_files_from_git(repo_dir: Path, branch: str) -> List[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", branch, "pipelineruns/"],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    )
    return sorted(
        f for f in result.stdout.strip().split("\n")
        if f and "/.tekton/" in f and f.endswith(".yaml")
    )


def read_file_from_git(repo_dir: Path, branch: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{branch}:{file_path}"],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Config loading (exceptions.toml)
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path]) -> dict:
    if not config_path or not config_path.exists():
        return {"accelerator_incompatibility_rules": {}, "exception": []}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            print("Warning: toml library not available. pip install tomli",
                  file=sys.stderr)
            return {"accelerator_incompatibility_rules": {}, "exception": []}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def detect_accelerator(component_name: str, accelerator_rules: dict) -> Optional[str]:
    lower = component_name.lower()
    for accel in accelerator_rules:
        if accel in lower:
            return accel
    return None


def is_accelerator_incompatible(component: str, arch: str, config: dict) -> bool:
    rules = config.get("accelerator_incompatibility_rules", {})
    accel = detect_accelerator(component, rules)
    if accel and arch in rules.get(accel, []):
        return True
    return False


def get_exception_for_arch(component: str, arch: str, config: dict) -> Optional[dict]:
    for exc in config.get("exception", []):
        if exc.get("component") == component and arch in exc.get("architectures", []):
            return exc
    return None


def extract_issue_key(issue_url: str) -> str:
    if not issue_url:
        return "XXX"
    if "/browse/" in issue_url:
        return issue_url.split("/browse/")[-1]
    if "-" in issue_url and "/" not in issue_url:
        return issue_url
    return "XXX"


# ---------------------------------------------------------------------------
# Cell-value logic (mirrors generate-table.py but returns structured info)
# ---------------------------------------------------------------------------

# Cell "kind" constants
CELL_BUILT = "built"            # checked checkbox
CELL_EXCEPTION = "exception"    # Jira link or "XXX" text
CELL_NA = "na"                  # "N/A" text
CELL_EMPTY = "empty"            # unchecked / blank


def cell_info(
    component: str, arch: str, built_archs: Set[str], config: dict,
) -> dict:
    """Return a dict describing what a cell should contain.

    Keys:
        kind  - one of CELL_BUILT / CELL_EXCEPTION / CELL_NA / CELL_EMPTY
        key   - Jira issue key (only for CELL_EXCEPTION)
        url   - Jira issue URL (only for CELL_EXCEPTION, may be empty)
    """
    if arch in built_archs:
        return {"kind": CELL_BUILT}

    exc = get_exception_for_arch(component, arch, config)
    if exc:
        issue_url = exc.get("issue", "")
        return {
            "kind": CELL_EXCEPTION,
            "key": extract_issue_key(issue_url),
            "url": issue_url,
        }

    if is_accelerator_incompatible(component, arch, config):
        return {"kind": CELL_NA}

    return {"kind": CELL_EMPTY}


# ---------------------------------------------------------------------------
# Gather data across branches
# ---------------------------------------------------------------------------

def resolve_git_ref(repo_dir: Path, branch: str) -> str:
    """Resolve a branch name to a valid git ref.

    Tries the name as-is first, then origin/<branch>.
    """
    for candidate in (branch, f"origin/{branch}"):
        if validate_git_branch(repo_dir, candidate):
            return candidate
    print(f"Error: branch '{branch}' not found (also tried 'origin/{branch}'). "
          "Run 'git fetch origin' first.", file=sys.stderr)
    sys.exit(1)


def gather_branch_data(
    repo_dir: Path, branch: str, config: dict,
) -> Dict[str, Set[str]]:
    """Return {component_name: {arch, ...}} for a single branch."""
    ref = resolve_git_ref(repo_dir, branch)
    if ref != branch:
        print(f"  resolved '{branch}' -> '{ref}'", file=sys.stderr)

    files = find_pipelinerun_files_from_git(repo_dir, ref)
    if not files:
        print(f"Warning: no PipelineRun files in '{branch}'", file=sys.stderr)
        return {}

    components: Dict[str, Set[str]] = {}
    for fp in files:
        try:
            content = read_file_from_git(repo_dir, ref, fp)
        except Exception as e:
            print(f"Warning: {e}", file=sys.stderr)
            continue
        name, archs = parse_pipelinerun_from_content(fp, content)
        if name and archs:
            components[name] = archs
    return components


# ---------------------------------------------------------------------------
# Smartsheet creation
# ---------------------------------------------------------------------------

LEGEND_LINES = [
    "--- LEGEND ---",
    "Checked checkbox = built for this architecture",
    "Jira link (e.g. RHOAIENG-12345) = exception tracked in Jira",
    "XXX = exception without a Jira issue assigned",
    "N/A = accelerator / platform incompatibility",
    "Unchecked = not currently built, no known exception",
]

LEGEND_GAP = 2  # empty columns between last arch column and legend


def build_sheet(
    token: str,
    sheet_name: str,
    branch: str,
    components: Dict[str, Set[str]],
    config: dict,
) -> tuple:
    """Create a Smartsheet and return (permalink, published_url, owner)."""

    ss = smartsheet.Smartsheet(token)
    ss.errors_as_exceptions(True)

    # --- Build column definitions ---
    columns = [
        ss.models.Column({
            "title": "Component Image",
            "type": "TEXT_NUMBER",
            "primary": True,
            "width": 350,
        }),
    ]

    for arch in ARCH_COLUMNS:
        columns.append(
            ss.models.Column({
                "title": arch,
                "type": "CHECKBOX",
            })
        )

    # Gap columns (empty spacers before legend)
    for i in range(LEGEND_GAP):
        columns.append(
            ss.models.Column({
                "title": f"_spacer_{i + 1}",
                "type": "TEXT_NUMBER",
                "width": 20,
            })
        )

    # Legend column
    columns.append(
        ss.models.Column({
            "title": "Legend",
            "type": "TEXT_NUMBER",
            "width": 380,
        })
    )

    # --- Create the sheet ---
    new_sheet = ss.models.Sheet({
        "name": sheet_name,
        "columns": columns,
    })

    print(f"Creating Smartsheet '{sheet_name}' ...", file=sys.stderr)
    result = ss.Home.create_sheet(new_sheet)
    sheet_id = result.result.id

    # Re-fetch to get actual column IDs
    sheet = ss.Sheets.get_sheet(sheet_id)
    col_map = {col.title: col.id for col in sheet.columns}
    legend_col_id = col_map["Legend"]

    sorted_components = sorted(components.items())

    print(f"Adding {len(sorted_components)} component rows ...", file=sys.stderr)

    # --- Build rows (data + inline legend at top) ---
    rows_to_add = []
    for row_idx, (comp, built_archs) in enumerate(sorted_components):
        row = ss.models.Row()
        row.to_bottom = True

        # Component name
        row.cells.append(ss.models.Cell({
            "column_id": col_map["Component Image"],
            "value": comp,
        }))

        # Architecture cells
        for arch in ARCH_COLUMNS:
            info = cell_info(comp, arch, built_archs, config)
            col_id = col_map[arch]

            if info["kind"] == CELL_BUILT:
                row.cells.append(ss.models.Cell({
                    "column_id": col_id,
                    "value": True,
                }))

            elif info["kind"] == CELL_EXCEPTION:
                cell_props = {
                    "column_id": col_id,
                    "value": info["key"],
                    "strict": False,
                    "format": FMT_CENTER,
                }
                if info["url"]:
                    cell_props["hyperlink"] = ss.models.Hyperlink({
                        "url": info["url"],
                    })
                row.cells.append(ss.models.Cell(cell_props))

            elif info["kind"] == CELL_NA:
                row.cells.append(ss.models.Cell({
                    "column_id": col_id,
                    "value": "N/A",
                    "strict": False,
                    "format": FMT_CENTER,
                }))

            else:
                row.cells.append(ss.models.Cell({
                    "column_id": col_id,
                    "value": False,
                }))

        # Legend text in the first N rows (aligned to top-right of sheet)
        if row_idx < len(LEGEND_LINES):
            row.cells.append(ss.models.Cell({
                "column_id": legend_col_id,
                "value": LEGEND_LINES[row_idx],
            }))

        rows_to_add.append(row)

    # Smartsheet API allows up to 500 rows per request
    BATCH_SIZE = 500
    for i in range(0, len(rows_to_add), BATCH_SIZE):
        batch = rows_to_add[i : i + BATCH_SIZE]
        ss.Sheets.add_rows(sheet_id, batch)
        print(f"  added rows {i + 1}-{i + len(batch)}", file=sys.stderr)

    # --- Publish the sheet (read-only full view) ---
    publish_status = ss.models.SheetPublish()
    publish_status.read_only_full_enabled = True
    publish_status.read_only_full_accessible_by = "ALL"
    publish_status.read_only_full_default_view = "GRID"
    ss.Sheets.set_publish_status(sheet_id, publish_status)

    updated_publish = ss.Sheets.get_publish_status(sheet_id)
    published_url = updated_publish.read_only_full_url or ""
    print(f"  published sheet (read-only full)", file=sys.stderr)

    # --- Fetch permalink and owner info ---
    sheet = ss.Sheets.get_sheet(sheet_id)
    permalink = sheet.permalink or f"https://app.smartsheet.com/sheets/{sheet_id}"

    current_user = ss.Users.get_current_user()
    owner = current_user.email or ""
    if current_user.first_name or current_user.last_name:
        full_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
        owner = f"{full_name} ({current_user.email})" if current_user.email else full_name

    return permalink, published_url, owner


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    SCRIPT_DIR = Path(__file__).parent.resolve()
    if SCRIPT_DIR.name == "multi-arch-tracking" and SCRIPT_DIR.parent.name == "script":
        DEFAULT_BASE_DIR = SCRIPT_DIR.parent.parent
    else:
        DEFAULT_BASE_DIR = Path.cwd()
    DEFAULT_CONFIG = SCRIPT_DIR / "exceptions.toml"

    parser = argparse.ArgumentParser(
        description="Export RHOAI multi-arch build matrix to Smartsheet",
    )
    parser.add_argument(
        "branch",
        help="Git branch to scan (e.g. rhoai-3.4-ea.1)",
    )
    parser.add_argument(
        "--base-dir", type=Path, default=DEFAULT_BASE_DIR,
        help="Root of the konflux-central repo (default: auto-detected)",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="TOML exceptions config (default: exceptions.toml next to script)",
    )
    parser.add_argument(
        "--sheet-name", default=None,
        help="Smartsheet name (default: 'RHOAI Multi-Arch Support - <branches>')",
    )

    args = parser.parse_args()

    # --- Token ---
    token = os.environ.get("SMARTSHEET_API_TOKEN")
    if not token:
        print("Error: SMARTSHEET_API_TOKEN environment variable is not set.",
              file=sys.stderr)
        sys.exit(1)

    # --- Config ---
    config = load_config(args.config)
    if args.config.exists():
        print(f"Loaded config from {args.config}", file=sys.stderr)

    # --- Gather data ---
    branch = args.branch
    print(f"Scanning branch '{branch}' ...", file=sys.stderr)
    components = gather_branch_data(args.base_dir, branch, config)
    print(f"  {len(components)} components found", file=sys.stderr)

    if not components:
        print("Error: no components found.", file=sys.stderr)
        sys.exit(1)

    # --- Sheet name (max 50 chars for Smartsheet) ---
    today = date.today().strftime("%Y-%m-%d")
    if args.sheet_name:
        sheet_name = args.sheet_name[:50]
    else:
        short_branch = branch
        sheet_name = f"Multi-Arch {short_branch} - {today}"[:50]

    # --- Create the Smartsheet ---
    permalink, published_url, owner = build_sheet(
        token=token,
        sheet_name=sheet_name,
        branch=branch,
        components=components,
        config=config,
    )

    print(f"\nSmartsheet created successfully!", file=sys.stderr)
    print(f"Owner:       {owner}", file=sys.stderr)
    print(f"Edit URL:    {permalink}", file=sys.stderr)
    if published_url:
        print(f"Public URL:  {published_url}", file=sys.stderr)
    print(f"\nSMARTSHEET_PUBLIC_URL={published_url or permalink}")
    print(f"BRANCH=\"{branch}\"")


if __name__ == "__main__":
    main()
