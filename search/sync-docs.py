#!/usr/bin/env python3
"""Sync docs into a standalone Mintlify project.

Supports two modes:
  Single-source: one docs directory where subdirs are categories (tabs).
  Multi-source:  a parent directory where each subdir is a "source" (tab),
                 and each source's subdirs become sidebar groups.

Usage:
    python sync-docs.py [DOCS_DIR] [--project NAME]
    python sync-docs.py                          # defaults to /etc/nixos/docs -> nixos/
    python sync-docs.py /path/to/docs            # custom source dir
    python sync-docs.py --project my-docs        # custom project name
    python sync-docs.py /path --project docs --multi-source   # unified multi-source
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Files to skip (meta docs, not useful as site pages)
SKIP_FILES = {"CLAUDE.md", "NAVIGATION.md", "README.md"}

# Special-case display names (all-caps acronyms, etc.)
# Everything else is auto-titled via str.title()
CATEGORY_NAME_OVERRIDES = {
    "ai": "AI",
}

# Source-level display name overrides (for multi-source mode)
SOURCE_NAME_OVERRIDES = {
    "nixos": "NixOS",
}

# Icons for source cards on the index page (multi-source mode)
SOURCE_ICONS = {
    "career": "briefcase",
    "nixos": "server",
    "study-plans": "graduation-cap",
}

# Default Mintlify theme (matches the papermint template)
DEFAULT_DOCS_JSON = {
    "$schema": "https://mintlify.com/docs.json",
    "theme": "mint",
    "name": "",
    "colors": {
        "primary": "#0E0E0E",
        "light": "#D4A27F",
        "dark": "#0E0E0E",
    },
    "appearance": {"default": "dark"},
    "background": {
        "color": {
            "light": "#FDFDF7",
            "dark": "#09090B",
        }
    },
    "navigation": {"tabs": []},
    "code": {"copy": True},
    "modeToggle": {"default": "dark"},
    "seo": {"indexHiddenPages": False},
}


def display_name(name: str, overrides: dict[str, str] | None = None) -> str:
    """Convert a directory name to a display name."""
    if overrides and name in overrides:
        return overrides[name]
    return CATEGORY_NAME_OVERRIDES.get(name, name.replace("-", " ").replace("_", " ").title())


def extract_title(content: str, filename: str) -> str:
    """Extract title from first H1 heading, falling back to filename.

    Special case: man pages starting with '# NAME' use the next line's
    command name (e.g., 'create-repo - description' -> 'create-repo').
    """
    lines = content.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            # Man page: '# NAME' -> grab command name from next non-empty line
            if title == "NAME":
                for next_line in lines[i + 1:]:
                    next_line = next_line.strip()
                    if next_line:
                        # "command - description" -> "command"
                        cmd = next_line.split(" - ")[0].strip()
                        if cmd:
                            return cmd
                        break
            # Strip leading emoji (unicode emoji + optional space)
            title = re.sub(r"^[\U0001f300-\U0001f9ff\u2600-\u27bf\u2700-\u27bf]+\s*", "", title)
            return title
    # Fallback: humanize filename
    return filename.replace("-", " ").replace("_", " ").title()


def extract_description(content: str) -> str:
    """Extract first non-heading, non-empty paragraph as description."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        # Skip headings, empty lines, horizontal rules, and metadata
        if not line or line.startswith("#") or line.startswith("---") or line.startswith("**Last"):
            continue
        # Skip table/list lines
        if line.startswith("|") or line.startswith("- ") or line.startswith("* "):
            continue
        # Found a text paragraph
        desc = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)  # strip bold
        desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", desc)  # strip links
        desc = re.sub(r"`([^`]+)`", r"\1", desc)  # strip inline code
        if len(desc) > 160:
            desc = desc[:157] + "..."
        return desc
    return ""


def escape_mdx(content: str) -> str:
    """Escape content that MDX would interpret as JSX.

    MDX treats <word> as JSX components which causes parse errors.
    We escape angle brackets outside of fenced code blocks.
    """
    lines = content.splitlines(keepends=True)
    result = []
    in_fence = False

    for line in lines:
        stripped = line.strip()
        # Track fenced code blocks (``` or ~~~)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            result.append(line)
            continue

        if in_fence:
            result.append(line)
            continue

        # Outside code fences: escape ALL < characters.
        # MDX treats any <text> as JSX. Replace < with &lt; which renders
        # correctly in both MDX and markdown.
        # Preserve HTML comments <!-- --> and standard inline HTML tags.
        # Simple approach: replace < unless followed by known-safe HTML tag or !--
        line = re.sub(
            r"<(?!/?(?:a|abbr|b|br|code|details|div|em|h[1-6]|hr|i|img|li|ol|p|pre|span|strong|sub|summary|sup|table|tbody|td|th|thead|tr|u|ul)[\s/>])(?!!--)",
            r"&lt;",
            line,
        )
        result.append(line)

    return "".join(result)


def strip_leading_title(content: str, title: str, description: str) -> str:
    """Remove the leading H1 heading and description from body content.

    Mintlify renders title/description from frontmatter, so keeping them
    in the body causes duplicate display.  Also strips a trailing ``---``
    horizontal rule that typically follows the description block.
    """
    lines = content.splitlines(keepends=True)
    idx = 0

    # Skip leading blank lines
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    # Strip H1 that matches the title
    if idx < len(lines):
        line = lines[idx].strip()
        if line.startswith("# "):
            idx += 1

    # Skip blank lines between heading and description
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    # Strip description line (may be wrapped in *italics* or plain)
    if idx < len(lines) and description:
        line = lines[idx].strip()
        # Compare stripped of markdown formatting
        plain = re.sub(r"[*_`]", "", line)
        desc_plain = re.sub(r"[*_`]", "", description)
        if plain == desc_plain:
            idx += 1

    # Skip blank lines after description
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    # Strip a lone horizontal rule (---) that often follows the title block
    if idx < len(lines) and lines[idx].strip() == "---":
        idx += 1

    # Skip blank lines after the rule
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    return "".join(lines[idx:])


def generate_mdx(content: str, title: str, description: str) -> str:
    """Wrap markdown content with YAML frontmatter for Mintlify."""
    # Escape quotes in frontmatter values
    safe_title = title.replace('"', '\\"')
    safe_desc = description.replace('"', '\\"')

    frontmatter = f'---\ntitle: "{safe_title}"\n'
    if safe_desc:
        frontmatter += f'description: "{safe_desc}"\n'
    frontmatter += "---\n\n"

    body = strip_leading_title(content, title, description)
    return frontmatter + escape_mdx(body)


def sync_files(docs_dir: Path, output_dir: Path, path_prefix: str = "") -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[tuple[str, str]]], int, int, int]:
    """Sync .md files from docs_dir into output_dir as .mdx files.

    Returns (groups, root_pages, written, skipped, deleted) where:
      groups: dict mapping subdir name -> list of (page_path, title)
      root_pages: list of (page_path, title) for files at root level
      written/skipped/deleted: file counts
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    root_pages: list[tuple[str, str]] = []
    written = 0
    skipped = 0
    expected_mdx: set[Path] = set()

    for md_file in sorted(docs_dir.rglob("*.md")):
        rel = md_file.relative_to(docs_dir)

        # Skip hidden directories
        if any(part.startswith(".") for part in rel.parts):
            continue

        # Skip meta files
        if rel.name in SKIP_FILES:
            continue

        content = md_file.read_text(encoding="utf-8")
        title = extract_title(content, rel.stem)
        description = extract_description(content)
        mdx_content = generate_mdx(content, title, description)

        # Output path
        out_path = output_dir / rel.with_suffix(".mdx")
        expected_mdx.add(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Only write if content differs
        if out_path.exists() and out_path.read_text(encoding="utf-8") == mdx_content:
            skipped += 1
        else:
            out_path.write_text(mdx_content, encoding="utf-8")
            written += 1

        # Navigation page path (relative to project root, no extension)
        if path_prefix:
            page_path = f"{path_prefix}/{rel.with_suffix('')}"
        else:
            page_path = str(rel.with_suffix(""))

        parts = rel.parts
        if len(parts) == 1:
            # Root-level file
            root_pages.append((page_path, title))
        else:
            # File in a subdirectory
            group_name = parts[0]
            groups.setdefault(group_name, []).append((page_path, title))

    # Delete orphaned .mdx files
    deleted = 0
    for mdx_file in sorted(output_dir.rglob("*.mdx")):
        if mdx_file not in expected_mdx and mdx_file.name != "index.mdx":
            mdx_file.unlink()
            deleted += 1

    # Clean up empty directories
    for subdir in sorted(output_dir.rglob("*"), reverse=True):
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()

    return groups, root_pages, written, skipped, deleted


def sort_pages(pages: list[tuple[str, str]]) -> list[str]:
    """Sort pages with 'overview' first, then alphabetically by title.

    Input is a list of (page_path, title) tuples.
    Returns a list of page_path strings.
    """
    overviews = [p for p, _ in pages if p.endswith("/overview")]
    rest = [p for p, _ in sorted(
        ((p, t) for p, t in pages if not p.endswith("/overview")),
        key=lambda x: x[1].lower(),
    )]
    return overviews + rest


# ---------------------------------------------------------------------------
# Single-source mode (original behavior)
# ---------------------------------------------------------------------------

def sync_docs(docs_dir: Path, output_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Sync .md files from docs_dir into output_dir as .mdx files.

    Only writes files whose content has changed (skip identical).
    Deletes orphaned .mdx files whose source .md no longer exists.

    Returns a dict mapping category -> list of (page_path, title) tuples.
    """
    categories: dict[str, list[tuple[str, str]]] = {}
    written = 0
    skipped = 0
    expected_mdx: set[Path] = set()

    for md_file in sorted(docs_dir.rglob("*.md")):
        rel = md_file.relative_to(docs_dir)

        # Skip hidden directories
        if any(part.startswith(".") for part in rel.parts):
            continue

        # Skip meta files at root level
        if rel.name in SKIP_FILES:
            continue

        # Must be in a subdirectory (category)
        parts = rel.parts
        if len(parts) < 2:
            continue

        category = parts[0]
        content = md_file.read_text(encoding="utf-8")
        title = extract_title(content, rel.stem)
        description = extract_description(content)
        mdx_content = generate_mdx(content, title, description)

        # Output path: <project>/<category>/<filename>.mdx
        out_path = output_dir / rel.with_suffix(".mdx")
        expected_mdx.add(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Only write if content differs (avoid unnecessary I/O and Mintlify hot-reload)
        if out_path.exists() and out_path.read_text(encoding="utf-8") == mdx_content:
            skipped += 1
        else:
            out_path.write_text(mdx_content, encoding="utf-8")
            written += 1

        # Navigation page path (no extension, relative to project root) + title for sorting
        page_path = str(rel.with_suffix(""))
        categories.setdefault(category, []).append((page_path, title))

    # Delete orphaned .mdx files (source .md was removed from NAS)
    deleted = 0
    for mdx_file in sorted(output_dir.rglob("*.mdx")):
        if mdx_file not in expected_mdx and mdx_file.name != "index.mdx":
            mdx_file.unlink()
            deleted += 1

    # Clean up empty category directories
    for subdir in sorted(output_dir.iterdir(), reverse=True):
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()

    print(f"Sync: {written} written, {skipped} unchanged, {deleted} deleted in {output_dir}")
    return categories


def update_docs_json(docs_json_path: Path, site_name: str, categories: dict[str, list[tuple[str, str]]]) -> None:
    """Create or update docs.json as a standalone Mintlify project."""
    if docs_json_path.exists():
        with open(docs_json_path) as f:
            config = json.load(f)
    else:
        config = dict(DEFAULT_DOCS_JSON)
        config["name"] = site_name

    # Build one horizontal tab per category (alphabetical)
    tabs = []

    # "Getting Started" tab with index page if it exists
    index_path = docs_json_path.parent / "index.mdx"
    if index_path.exists():
        tabs.append({
            "tab": "Getting Started",
            "groups": [{"group": "Getting Started", "pages": ["index"]}],
        })

    for cat in sorted(categories):
        cat_display = display_name(cat)
        tabs.append({
            "tab": cat_display,
            "groups": [{"group": cat_display, "pages": sort_pages(categories[cat])}],
        })

    config["navigation"] = {"tabs": tabs}

    with open(docs_json_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    total_pages = sum(len(p) for t in tabs for g in t["groups"] for p in [g["pages"]])
    print(f"Updated {docs_json_path}: {len(tabs)} tabs, {total_pages} pages")


# ---------------------------------------------------------------------------
# Multi-source mode (unified docs site)
# ---------------------------------------------------------------------------

def sync_multi_source(docs_dir: Path, project_dir: Path) -> dict[str, dict]:
    """Sync multiple source directories into a unified Mintlify project.

    docs_dir contains source directories (career, nixos, study-plans).
    Each source becomes a tab with sidebar groups for its subdirectories.

    Returns dict mapping source_name -> {groups, root_pages, display_name}.
    """
    sources: dict[str, dict] = {}
    total_written = 0
    total_skipped = 0
    total_deleted = 0

    for source_dir in sorted(docs_dir.iterdir()):
        if not source_dir.is_dir() or source_dir.name.startswith("."):
            continue

        source_name = source_dir.name
        source_output = project_dir / source_name

        print(f"  Syncing source: {source_name}")
        groups, root_pages, written, skipped, deleted = sync_files(
            source_dir, source_output, path_prefix=source_name
        )

        sources[source_name] = {
            "groups": groups,
            "root_pages": root_pages,
            "display_name": display_name(source_name, SOURCE_NAME_OVERRIDES),
        }

        total_written += written
        total_skipped += skipped
        total_deleted += deleted

    print(f"Sync: {total_written} written, {total_skipped} unchanged, {total_deleted} deleted")
    return sources


def generate_index_mdx(project_dir: Path, sources: dict[str, dict]) -> None:
    """Generate the Getting Started index.mdx with source cards."""
    cards = []
    for source_name in sorted(sources):
        src = sources[source_name]
        icon = SOURCE_ICONS.get(source_name, "book")
        name = src["display_name"]

        # Find the first page to link to
        href = None
        if src["root_pages"]:
            # Prefer overview page, then first root page
            for p, t in src["root_pages"]:
                if p.endswith("/overview"):
                    href = f"/{p}"
                    break
            if not href:
                href = f"/{src['root_pages'][0][0]}"
        elif src["groups"]:
            first_group = sorted(src["groups"])[0]
            pages = src["groups"][first_group]
            if pages:
                href = f"/{pages[0][0]}"

        # Build description from overview.md if available
        overview_path = project_dir / source_name / "overview.mdx"
        desc = ""
        if overview_path.exists():
            content = overview_path.read_text(encoding="utf-8")
            # Extract description from frontmatter
            m = re.search(r'^description:\s*"(.+)"', content, re.MULTILINE)
            if m:
                desc = m.group(1)

        if not desc:
            # Fallback descriptions
            desc = f"Browse {name} documentation"

        href_attr = f' href="{href}"' if href else ""
        cards.append(f'  <Card title="{name}" icon="{icon}"{href_attr}>\n    {desc}\n  </Card>')

    index_content = f"""---
title: Docs
description: Personal documentation hub covering career, infrastructure, and learning resources.
---

## Browse by topic

<CardGroup cols={{2}}>
{chr(10).join(cards)}
</CardGroup>
"""

    index_path = project_dir / "index.mdx"
    # Only write if changed
    if index_path.exists() and index_path.read_text(encoding="utf-8") == index_content:
        print("index.mdx unchanged")
    else:
        index_path.write_text(index_content, encoding="utf-8")
        print("Updated index.mdx")


def update_docs_json_multi(docs_json_path: Path, site_name: str, sources: dict[str, dict]) -> None:
    """Generate docs.json for multi-source unified site.

    Each source becomes a tab. Within each tab, subdirectories become groups.
    Root-level files in a source go into an "Overview" group (or source-named group).
    """
    if docs_json_path.exists():
        with open(docs_json_path) as f:
            config = json.load(f)
    else:
        config = dict(DEFAULT_DOCS_JSON)
        config["name"] = site_name

    tabs = []

    # Getting Started tab
    index_path = docs_json_path.parent / "index.mdx"
    if index_path.exists():
        tabs.append({
            "tab": "Getting Started",
            "groups": [{"group": "Getting Started", "pages": ["index"]}],
        })

    # One tab per source (alphabetical)
    for source_name in sorted(sources):
        src = sources[source_name]
        src_display = src["display_name"]
        groups_list = []

        # Root-level pages go into a group named after the source
        if src["root_pages"]:
            groups_list.append({
                "group": src_display,
                "pages": sort_pages(src["root_pages"]),
            })

        # Subdirectory groups (alphabetical)
        for group_name in sorted(src["groups"]):
            group_display = display_name(group_name)
            groups_list.append({
                "group": group_display,
                "pages": sort_pages(src["groups"][group_name]),
            })

        if groups_list:
            tabs.append({
                "tab": src_display,
                "groups": groups_list,
            })

    config["navigation"] = {"tabs": tabs}

    with open(docs_json_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    total_pages = sum(
        len(g["pages"]) for t in tabs for g in t["groups"]
    )
    print(f"Updated {docs_json_path}: {len(tabs)} tabs, {total_pages} pages")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync docs into a Mintlify project")
    parser.add_argument("docs_dir", nargs="?", default=None, help="Source docs directory (default: /etc/nixos/docs)")
    parser.add_argument("--project", default=None, help="Project name (default: $PROJECT or 'nixos')")
    parser.add_argument("--multi-source", action="store_true", help="Multi-source mode: each subdir of docs_dir is a separate source/tab")
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir) if args.docs_dir else Path("/etc/nixos/docs")
    if not docs_dir.is_dir():
        print(f"Error: {docs_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    project = args.project or os.environ.get("PROJECT", "nixos")

    # Resolve project dir relative to repo root (parent of search/)
    repo_root = Path(__file__).resolve().parent.parent
    project_dir = repo_root / project
    docs_json_path = project_dir / "docs.json"

    # Humanize project name for site title
    site_name = project.replace("-", " ").replace("_", " ").title()

    if args.multi_source:
        print(f"Multi-source sync: {docs_dir} -> {project_dir}")
        project_dir.mkdir(parents=True, exist_ok=True)
        sources = sync_multi_source(docs_dir, project_dir)
        generate_index_mdx(project_dir, sources)
        update_docs_json_multi(docs_json_path, site_name, sources)
    else:
        print(f"Syncing {docs_dir} -> {project_dir}")
        categories = sync_docs(docs_dir, project_dir)
        update_docs_json(docs_json_path, site_name, categories)

    print("Done!")


if __name__ == "__main__":
    main()
