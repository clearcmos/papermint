#!/usr/bin/env python3
"""Sync /etc/nixos/docs into a standalone Mintlify project.

Scans a docs directory for .md files, generates .mdx copies with YAML
frontmatter, and creates/updates docs.json navigation.

Usage:
    python sync-docs.py [DOCS_DIR] [--project NAME]
    python sync-docs.py                          # defaults to /etc/nixos/docs -> nixos/
    python sync-docs.py /path/to/docs            # custom source dir
    python sync-docs.py --project my-docs        # custom project name
    PROJECT=nixos python sync-docs.py            # via env var
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Files to skip (meta docs, not useful as site pages)
SKIP_FILES = {"CLAUDE.md", "NAVIGATION.md", "README.md"}

# Category display names and ordering
CATEGORY_NAMES = {
    "ai": "AI",
    "desktop": "Desktop",
    "guides": "Guides",
    "infrastructure": "Infrastructure",
    "operations": "Operations",
    "reference": "Reference",
    "security": "Security",
    "services": "Services",
    "storage": "Storage",
    "tools": "Tools",
    "troubleshooting": "Troubleshooting",
    "updates": "Updates",
}

# Order categories appear in navigation
CATEGORY_ORDER = [
    "infrastructure",
    "security",
    "services",
    "desktop",
    "ai",
    "operations",
    "storage",
    "tools",
    "guides",
    "reference",
    "troubleshooting",
    "updates",
]

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


def generate_mdx(content: str, title: str, description: str) -> str:
    """Wrap markdown content with YAML frontmatter for Mintlify."""
    # Escape quotes in frontmatter values
    safe_title = title.replace('"', '\\"')
    safe_desc = description.replace('"', '\\"')

    frontmatter = f'---\ntitle: "{safe_title}"\n'
    if safe_desc:
        frontmatter += f'description: "{safe_desc}"\n'
    frontmatter += "---\n\n"

    return frontmatter + escape_mdx(content)


def sync_docs(docs_dir: Path, output_dir: Path) -> dict[str, list[str]]:
    """Sync .md files from docs_dir into output_dir as .mdx files.

    Returns a dict mapping category -> list of page paths (relative to project root).
    """
    categories: dict[str, list[str]] = {}
    files_written = 0

    for md_file in sorted(docs_dir.rglob("*.md")):
        rel = md_file.relative_to(docs_dir)

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
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(mdx_content, encoding="utf-8")
        files_written += 1

        # Navigation page path (no extension, relative to project root)
        page_path = str(rel.with_suffix(""))
        categories.setdefault(category, []).append(page_path)

    print(f"Wrote {files_written} .mdx files to {output_dir}")
    return categories


def sort_pages(pages: list[str]) -> list[str]:
    """Sort pages with 'overview' first, then alphabetically."""
    overviews = [p for p in pages if p.endswith("/overview")]
    rest = sorted(p for p in pages if not p.endswith("/overview"))
    return overviews + rest


def update_docs_json(docs_json_path: Path, site_name: str, categories: dict[str, list[str]]) -> None:
    """Create or update docs.json as a standalone Mintlify project."""
    if docs_json_path.exists():
        with open(docs_json_path) as f:
            config = json.load(f)
    else:
        config = dict(DEFAULT_DOCS_JSON)
        config["name"] = site_name

    # Build navigation groups
    groups = []
    for cat in CATEGORY_ORDER:
        if cat not in categories:
            continue
        display_name = CATEGORY_NAMES.get(cat, cat.title())
        groups.append({
            "group": display_name,
            "pages": sort_pages(categories[cat]),
        })

    # Add any categories not in CATEGORY_ORDER
    for cat in sorted(categories):
        if cat not in CATEGORY_ORDER:
            display_name = CATEGORY_NAMES.get(cat, cat.title())
            groups.append({
                "group": display_name,
                "pages": sort_pages(categories[cat]),
            })

    docs_tab = {
        "tab": "Documentation",
        "groups": groups,
    }

    # Replace or set the Documentation tab
    tabs = config.get("navigation", {}).get("tabs", [])
    replaced = False
    for i, tab in enumerate(tabs):
        if tab.get("tab") == "Documentation":
            tabs[i] = docs_tab
            replaced = True
            break
    if not replaced:
        tabs.append(docs_tab)

    config["navigation"] = {"tabs": tabs}

    with open(docs_json_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    total_pages = sum(len(g["pages"]) for g in groups)
    print(f"Updated {docs_json_path}: {len(groups)} groups, {total_pages} pages")


def main():
    parser = argparse.ArgumentParser(description="Sync docs into a Mintlify project")
    parser.add_argument("docs_dir", nargs="?", default=None, help="Source docs directory (default: /etc/nixos/docs)")
    parser.add_argument("--project", default=None, help="Project name (default: $PROJECT or 'nixos')")
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

    print(f"Syncing {docs_dir} -> {project_dir}")
    categories = sync_docs(docs_dir, project_dir)
    update_docs_json(docs_json_path, site_name, categories)
    print("Done!")


if __name__ == "__main__":
    main()
