#!/usr/bin/env bash
set -euo pipefail

# Scaffold a new Mintlify doc project with the papermint default theme.
#
# Usage:
#   ./scripts/new-project.sh <dir-name> [site-title]
#   ./scripts/new-project.sh my-docs "My Documentation"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <dir-name> [site-title]"
  exit 1
fi

DIR_NAME="$1"
SITE_TITLE="${2:-$(echo "$DIR_NAME" | sed 's/-/ /g; s/\b\(.\)/\u\1/g')}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="$REPO_ROOT/$DIR_NAME"

if [[ -d "$PROJECT_DIR" ]]; then
  echo "Error: $PROJECT_DIR already exists"
  exit 1
fi

mkdir -p "$PROJECT_DIR/en"

# docs.json with default papermint theme
cat > "$PROJECT_DIR/docs.json" <<DOCSJSON
{
  "\$schema": "https://mintlify.com/docs.json",
  "theme": "mint",
  "name": "$SITE_TITLE",
  "colors": {
    "primary": "#0E0E0E",
    "light": "#D4A27F",
    "dark": "#0E0E0E"
  },
  "appearance": {
    "default": "dark"
  },
  "background": {
    "color": {
      "light": "#FDFDF7",
      "dark": "#09090B"
    }
  },
  "navigation": {
    "tabs": [
      {
        "tab": "Documentation",
        "groups": [
          {
            "group": "Getting Started",
            "pages": [
              "index"
            ]
          },
          {
            "group": "Guides",
            "pages": [
              "en/example"
            ]
          }
        ]
      }
    ]
  },
  "code": {
    "copy": true
  },
  "modeToggle": {
    "default": "dark"
  },
  "seo": {
    "indexHiddenPages": false
  }
}
DOCSJSON

# Landing page
cat > "$PROJECT_DIR/index.mdx" <<'INDEXMDX'
---
title: Welcome
description: Documentation home page
---

# Welcome

Get started by browsing the guides in the sidebar.
INDEXMDX

# Example doc page
cat > "$PROJECT_DIR/en/example.mdx" <<'EXAMPLEMDX'
---
title: Example Page
description: A starter doc page
---

# Example Page

Replace this with your content.
EXAMPLEMDX

# .gitignore
cat > "$PROJECT_DIR/.gitignore" <<'GITIGNORE'
__pycache__/
GITIGNORE

echo "Created $PROJECT_DIR"
echo "  cd $DIR_NAME && npx mint dev"
