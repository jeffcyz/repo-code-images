# Repo Code Images

Convert an entire local source-code repository into **syntax-highlighted, no-wrap PNG images**, for review in ChatGPT. This is an independent tool, separate from import_sync.

## Features

- Original code lines and indentation are never wrapped.
- Long source files are split vertically (80 lines per image by default).
- Extra-wide code is sliced horizontally into overlapping PNG images, preserving all content.
- Retain the source directory hierarchy, code path, syntax highlighting, and original line numbers.
- Write **manifest.txt** and **manifest.csv** with the file path, SHA-256 digest, original source line range, image file, vertical/horizontal part numbers, and horizontal offsets.
- Use Git's tracked + untracked nonignored file inventory when available.
- Skip common caches and credential-like filenames. Always review outputs for sensitive data before sharing.

## Install (Windows / macOS / Linux)

Install Python 3.10+ and then, in this repo:

~~~sh
python -m pip install -r requirements.txt
python -m playwright install chromium
~~~

## Run

In PowerShell, Cursor, or VS Code terminal:

~~~powershell
python .\repo_to_images.py "D:\Projects\my-project" --max-lines 80 --max-width 2000
~~~

Default output is beside your source repository:

~~~text
D:\Projects\my-project_code_images\
  src\validation.py.v001.h001.png
  src\validation.py.v001.h002.png
  src\validation.py.v002.h001.png
  tests\test_validation.py.png
  manifest.txt
  manifest.csv
~~~

To choose a destination:

~~~powershell
python .\repo_to_images.py "D:\Projects\my-project" --out "D:\Exports\my-project-images"
~~~

Or use the PowerShell wrapper:

~~~powershell
.\run_repo_to_images.ps1 -Repo "D:\Projects\my-project" -MaxLines 80 -MaxWidth 2000
~~~

For macOS/Linux, use python3 and POSIX paths.

## Flags

| Flag | Default | Description |
| --- | --- | --- |
| --max-lines | 80 | Source code lines per vertical part (0 = unlimited) |
| --max-width | 2000 | Maximum CSS pixel width; wide code uses horizontal slices |
| --overlap | 35 | Horizontal slice overlap in CSS pixels |
| --font-size | 18 | Code font size in CSS pixels |
| --scale | 1.5 | Screenshot device pixel ratio |
| --style | monokai | Pygments highlighting theme |
| --out | sibling source_code_images | Image output path |
| --extensions | common source formats | Restrict to e.g. py,sql,ps1 |
| --browser-executable | Playwright Chromium | Override browser binary |
| --include-sensitive | off | Also export secret-looking files (not recommended) |

## Indexes and image naming

manifest.txt and manifest.csv are generated for the **source repository you convert**, not stored as a static manifest of this tool. See examples/manifest.txt for the format. v001/v002 denote consecutive vertical ranges, while h001/h002 denote left-to-right horizontal slices; offsets let you reconstruct wide code visually.

Notebook .ipynb files are rendered as source JSON, not Jupyter cells.

## Test

~~~sh
python -m pip install pytest
python -m pytest -q
~~~

**Security and accuracy:** Do not upload proprietary, credential-bearing, or regulated source code to ChatGPT without required authorization. PNGs are lossless images but visual AI can still misread small punctuation or indentation; original text is more reliable for exact edits.
