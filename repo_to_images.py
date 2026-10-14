#!/usr/bin/env python3
"""Convert a source-code repository to nonwrapped, syntax-highlighted PNG slices."""
from __future__ import annotations
import argparse
import asyncio
import csv
import datetime as dt
import fnmatch
import hashlib
import os
import subprocess
import sys
from html import escape
from pathlib import Path
from shutil import which

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.util import ClassNotFound
from playwright.async_api import async_playwright

EXTENSIONS = frozenset('.py .pyi .sql .js .jsx .ts .tsx .mjs .cjs .json .jsonc .yaml .yml .toml .ini .cfg .ps1 .psm1 .psd1 .bat .cmd .sh .bash .zsh .java .kt .go .rs .c .h .cpp .hpp .cc .cs .php .rb .swift .r .m .mm .html .htm .css .scss .less .xml .md .rst .txt .tf .tfvars .vue .svelte .ipynb .graphql .gql .proto'.split())
SPECIAL = frozenset('dockerfile makefile containerfile jenkinsfile gemfile .gitignore .gitattributes .editorconfig .prettierrc'.split())
IGNORE_DIRS = frozenset('.git .hg .svn .venv venv env __pycache__ node_modules dist build .next .idea .pytest_cache .mypy_cache .ruff_cache .tox .turbo coverage .cache'.split())
SECRET = ('.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', 'id_rsa*', 'id_ed25519*', '*credentials*', '*secret*.json', '*secrets*.yaml', '*secrets*.yml', '*.tfstate*')


def read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16'), 'utf-16'
    if b'\x00' in data:
        raise ValueError('Binary file')
    for encoding in ('utf-8-sig', 'gb18030', 'cp1252'):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return data.decode('latin-1'), 'latin-1'


def source_files(root: Path, output: Path, allowed: frozenset[str], include_sensitive: bool):
    """Prefer git ls-files (including untracked files) to respect nested .gitignore."""
    candidates = None
    if which('git'):
        check = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
        if check.returncode == 0:
            top = Path(check.stdout.strip()).resolve()
            found = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--full-name'], capture_output=True)
            if found.returncode == 0:
                candidates = [top / os.fsdecode(x) for x in found.stdout.split(b'\x00') if x]
    if candidates is None:
        candidates = []
        try:
            from pathspec import PathSpec
            ign = root / '.gitignore'
            spec = PathSpec.from_lines('gitwildmatch', ign.read_text('utf-8').splitlines()) if ign.is_file() else None
        except ImportError:
            spec = None
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(x for x in dirs if x not in IGNORE_DIRS)
            for name in files:
                path = Path(current) / name
                if spec and spec.match_file(path.relative_to(root).as_posix()):
                    continue
                candidates.append(path)
    selected, skipped = [], []
    for path in sorted(set(candidates)):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved.is_relative_to(output):
            continue
        rel = path.relative_to(root)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in allowed and path.name.lower() not in SPECIAL:
            continue
        if not include_sensitive and any(fnmatch.fnmatch(part.lower(), p) for part in rel.parts for p in SECRET):
            skipped.append((rel.as_posix(), 'sensitive filename'))
            continue
        selected.append(path)
    return selected, skipped


def html_document(code: str, rel: str, first: int, last: int, font_size: int, style: str) -> str:
    try:
        lexer = get_lexer_for_filename(rel, code)
    except ClassNotFound:
        lexer = TextLexer()
    fmt = HtmlFormatter(style=style, nowrap=True)
    marked = highlight(code, lexer, fmt)
    nums = '\n'.join(map(str, range(first, last + 1)))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<style>{fmt.get_style_defs('.code')}
*{{box-sizing:border-box}} html,body{{margin:0;background:#0c1118;color:#d6e1ee}}
#capture{{display:inline-block;padding:12px;background:#0c1118}}
#panel{{display:inline-block;border:1px solid #344155;border-radius:8px;overflow:hidden;background:#151b26}}
#header{{padding:10px 16px;background:#202a39;font-family:Arial,sans-serif;color:#d6e1ee}}
#path{{font-weight:bold;font-size:15px}} #meta{{color:#a8bdd6;font-size:12px;padding-top:5px}}
#body{{display:flex;gap:14px;padding:12px;align-items:stretch}}
#numbers{{flex:none;text-align:right;padding-right:12px;border-right:1px solid #3c4b61;color:#8da3be}}
#code-window{{flex:none;overflow:hidden;position:relative}} #code{{position:relative;left:0;display:block;width:max-content;max-width:none}}
#numbers,#code{{white-space:pre!important;overflow-wrap:normal!important;word-break:normal!important;tab-size:4;margin:0;font-family:Consolas,Menlo,"DejaVu Sans Mono",monospace;font-size:{font_size}px;line-height:1.45;font-variant-ligatures:none}}
.code{{background:transparent!important}}
</style></head><body><div id="capture"><div id="panel"><div id="header"><div id="path">{escape(rel)}</div><div id="meta">Lines {first}-{last}<span id="horizontal"></span></div></div><div id="body"><pre id="numbers">{nums}</pre><div id="code-window"><pre id="code" class="code">{marked}</pre></div></div></div></div></body></html>'''


async def take_slices(browser, page_html: str, target_base: Path, first: int, last: int, v: int, v_count: int, width: int, overlap: int, scale: float):
    ctx = await browser.new_context(viewport={'width': max(1200, width), 'height': 1000}, device_scale_factor=scale)
    page = await ctx.new_page()
    await page.route('**/*', lambda route: route.abort())
    await page.set_content(page_html)
    await page.evaluate('document.fonts.ready')
    dims = await page.evaluate("""() => { const code=document.querySelector('#code'); const win=document.querySelector('#code-window'); const cap=document.querySelector('#capture'); return {code:Math.ceil(code.getBoundingClientRect().width), extra:Math.ceil(cap.getBoundingClientRect().width-win.getBoundingClientRect().width)} }""")
    available = width - dims['extra'] - 3
    if available <= 100 or available <= overlap:
        await ctx.close()
        raise ValueError('--max-width too small for gutters and header')
    visible = min(dims['code'], available)
    if dims['code'] <= visible:
        offsets = [0]
    else:
        stride = visible - overlap
        offsets = list(range(0, dims['code'] - visible + 1, stride))
        offsets.append(dims['code'] - visible)
        offsets = sorted(set(offsets))
    rows = []
    for h, offset in enumerate(offsets, 1):
        await page.evaluate("""({w,x,h,total})=>{document.querySelector('#code-window').style.width=w+'px';document.querySelector('#code').style.left=(-x)+'px';document.querySelector('#horizontal').textContent=total>1?' | Horizontal '+h+'/'+total+' | offset '+x+'px':''}""", {'w': visible, 'x': offset, 'h': h, 'total': len(offsets)})
        name = f'{target_base.name}.v{v:03d}.h{h:03d}.png' if v_count > 1 or len(offsets) > 1 else target_base.name+'.png'
        target = target_base.with_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.locator('#capture').screenshot(path=str(target), type='png', animations='disabled')
        rows.append(dict(path=target, first=first, last=last, vertical=v, vcount=v_count, horizontal=h, hcount=len(offsets), offset=offset, visible=visible, full=dims['code']))
    await ctx.close()
    return rows


async def export(args):
    root = Path(args.repo).expanduser().resolve()
    out = Path(args.out).expanduser().resolve() if args.out else Path(str(root) + '_code_images')
    if not root.is_dir() or out == root or args.max_width < 450 or args.max_lines < 0 or args.scale <= 0 or args.font_size < 8 or not 0 <= args.overlap < args.max_width/2:
        raise ValueError('Invalid directory or image options')
    allowed = frozenset(x if x.startswith('.') else '.'+x for x in (y.strip().lower() for y in args.extensions.split(',')) if x) if args.extensions else EXTENSIONS
    files, skipped = source_files(root, out, allowed, args.include_sensitive)
    out.mkdir(parents=True, exist_ok=True)
    manifest = ['Repository Code Images — manifest.txt', f'SOURCE_ROOT: {root}', f'OUTPUT_ROOT: {out}', f'GENERATED_UTC: {dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}', f'OPTIONS: max_lines={args.max_lines}; max_width={args.max_width}; overlap={args.overlap}; scale={args.scale}; font_size={args.font_size}; style={args.style}', 'NO WRAP: Horizontal slices overlap; read h001 then h002.', '']
    columns = ['source_file','source_sha256','encoding','line_start','line_end','vertical_part','vertical_parts','horizontal_part','horizontal_parts','horizontal_offset_px','visible_code_width_px','full_code_width_px','image_file']
    csv_rows, done = [], 0
    async with async_playwright() as playwright:
        launch = {'headless': True}
        if args.browser_executable:
            launch['executable_path'] = args.browser_executable
        browser = await playwright.chromium.launch(**launch)
        try:
            for idx, file in enumerate(files, 1):
                rel = file.relative_to(root).as_posix()
                print(f'[{idx}/{len(files)}] {rel}', flush=True)
                try:
                    text, encoding = read_text(file)
                    digest = hashlib.sha256(file.read_bytes()).hexdigest()
                    lines = text.splitlines(keepends=True) or ['']
                    group_size = args.max_lines or len(lines)
                    groups = [lines[i:i+group_size] for i in range(0, len(lines), group_size)]
                    manifest += [f'FILE: {rel}', f'  SHA256: {digest}', f'  ENCODING: {encoding}', f'  LINES: {len(lines)}']
                    for v, group in enumerate(groups, 1):
                        first = (v-1)*group_size+1
                        last = first+len(group)-1
                        markup = html_document(''.join(group), rel, first, last, args.font_size, args.style)
                        images = await take_slices(browser, markup, out / file.relative_to(root).parent / file.name, first, last, v, len(groups), args.max_width, args.overlap, args.scale)
                        for row in images:
                            image = row['path'].relative_to(out).as_posix()
                            manifest.append(f'  IMAGE: {image} | lines {first}-{last} | vertical {v}/{len(groups)} | horizontal {row["horizontal"]}/{row["hcount"]} | x_offset={row["offset"]}px')
                            csv_rows.append(dict(source_file=rel, source_sha256=digest, encoding=encoding, line_start=first, line_end=last, vertical_part=v, vertical_parts=len(groups), horizontal_part=row['horizontal'], horizontal_parts=row['hcount'], horizontal_offset_px=row['offset'], visible_code_width_px=row['visible'], full_code_width_px=row['full'], image_file=image))
                    manifest.append('')
                    done += 1
                except Exception as exc:
                    skipped.append((rel, f'{type(exc).__name__}: {exc}'))
                    print(f'  ERROR: {exc}', file=sys.stderr)
        finally:
            await browser.close()
    manifest += ['SUMMARY:', f'  Source files discovered: {len(files)}', f'  Source files rendered: {done}', f'  PNG images generated: {len(csv_rows)}', f'  Skipped/error entries: {len(skipped)}', '']
    if skipped:
        manifest.append('SKIPPED_OR_FAILED:')
        manifest += [f'  {file}: {reason}' for file, reason in skipped]
    (out / 'manifest.txt').write_text('\n'.join(manifest)+'\n', encoding='utf-8')
    with (out / 'manifest.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f'Complete: {done}/{len(files)} source files; {len(csv_rows)} PNG images. Output: {out}')
    return 0 if done == len(files) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('repo')
    p.add_argument('--out')
    p.add_argument('--max-lines', type=int, default=80)
    p.add_argument('--max-width', type=int, default=2000)
    p.add_argument('--overlap', type=int, default=35)
    p.add_argument('--font-size', type=int, default=18)
    p.add_argument('--scale', type=float, default=1.5)
    p.add_argument('--style', default='monokai')
    p.add_argument('--extensions', help='Comma-separated extensions such as py,sql,ps1')
    p.add_argument('--include-sensitive', action='store_true')
    p.add_argument('--browser-executable')
    return asyncio.run(export(p.parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
