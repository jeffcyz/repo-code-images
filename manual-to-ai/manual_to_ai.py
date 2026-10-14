#!/usr/bin/env python3
"""Archive web documentation and export AI-readable text, tables and dense PNG pages.

Requires Python 3.10+, Playwright, BeautifulSoup, markdownify, Pillow, lxml.
Use --browser-executable for an installed Microsoft Edge / Google Chrome if
Playwright's bundled Chromium cannot be downloaded.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import datetime as dt
import hashlib
import html
import json
import mimetypes
import re
import sys
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit, unquote

from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

CONTENT_SELECTORS = [
    'main', '[role="main"]', '.rst-content', '.wy-nav-content', '.document',
    '.bd-content', '.markdown-body', '.theme-doc-markdown', '.content',
    '#content', '#main-content', 'article', '.body', 'body'
]
REMOVE_SELECTORS = [
    'nav', 'aside', 'footer', 'script', 'style', 'noscript', 'template',
    '[role="navigation"]', '.sidebar', '.toc', '.breadcrumb',
    '.wy-nav-side', '.wy-side-nav-search', '.wy-breadcrumbs', '.bd-sidebar',
    '.navbar', '.search', '.searchbox', '.headerlink',
    '.prev-next-area', '.edit-this-page', '.footer', '.sphinxsidebar',
]
NAV_SELECTOR = (
    '.wy-menu a[href], .wy-nav-side a[href], .bd-sidebar a[href], '
    '.sidebar a[href], .toc a[href], nav a[href], aside a[href], '
    '#sidebar a[href], .treeview a[href], [role="navigation"] a[href]'
)
DOC_EXTENSIONS = {'.html', '.htm', '', '/'}
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'}
MANIFEST_FIELDS = ['page_id', 'title', 'url', 'fetched_at_utc', 'status', 'source_html',
                   'raw_dom_html', 'original_mhtml', 'cleaned_html', 'markdown',
                   'ai_text', 'table_count', 'table_files', 'image_count',
                   'images', 'warnings']
IMAGE_FIELDS = ['page_id', 'url', 'title', 'image', 'vertical_part', 'vertical_parts',
                'horizontal_part', 'horizontal_parts', 'y_start_css_px', 'y_end_css_px',
                'x_start_css_px', 'horizontal_offsets_css_px', 'image_width_px', 'image_height_px']


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url.strip())
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def ident(url: str) -> str:
    """Stable, collision-resistant page key, independent of URL traversal order."""
    p = urlsplit(url)
    label = Path(unquote(p.path.rstrip('/'))).stem or 'index'
    label = re.sub(r'[^A-Za-z0-9_-]+', '-', label).strip('-')[:56] or 'page'
    return f'{label}-{hashlib.sha256(url.encode()).hexdigest()[:10]}'


def load_seeds(args) -> list[str]:
    seeds = list(args.url or [])
    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                seeds.append(line)
    seeds = list(dict.fromkeys(map(normalize_url, seeds)))
    if not seeds:
        raise ValueError('Provide --url and/or --urls-file.')
    if any(urlsplit(url).scheme not in ('http', 'https', 'file') for url in seeds):
        raise ValueError('URL schemes must be http, https, or file.')
    return seeds


def discover_browser(explicit: str | None) -> str | None:
    if explicit:
        target = Path(explicit).expanduser()
        if not target.is_file():
            raise FileNotFoundError(f'Browser does not exist: {explicit}')
        return str(target.resolve())
    import os
    import shutil
    places = []
    if sys.platform == 'win32':
        for home in (os.getenv('PROGRAMFILES(X86)'), os.getenv('PROGRAMFILES'),
                     os.getenv('LOCALAPPDATA')):
            if home:
                for suffix in ('Microsoft/Edge/Application/msedge.exe',
                               'Google/Chrome/Application/chrome.exe'):
                    places.append(Path(home) / suffix)
    elif sys.platform == 'darwin':
        places.extend(Path(x) for x in (
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
    for p in places:
        if p.is_file():
            return str(p)
    for command in ('msedge', 'microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser'):
        located = shutil.which(command)
        if located:
            return located
    return None  # try bundled Playwright Chromium


def choose_main(soup: BeautifulSoup, selector: str | None):
    if selector:
        match = soup.select_one(selector)
        if not match:
            raise ValueError(f'--content-selector matched nothing: {selector}')
        return match
    for css in CONTENT_SELECTORS:
        for match in soup.select(css):
            if len(match.get_text(' ', strip=True)) >= 40:
                return match
    return soup.body or soup


def remove_chrome(main_html: str, base_url: str, exclude: list[str]):
    cleaned = BeautifulSoup(main_html, 'lxml')
    for css in REMOVE_SELECTORS + exclude:
        for tag in list(cleaned.select(css)):
            tag.decompose()
    for el in cleaned.find_all(True):
        for key in list(el.attrs):
            if key.lower().startswith('on') or key.lower() in ('contenteditable', 'autofocus'):
                del el.attrs[key]
        for key in ('src', 'href', 'poster'):
            if el.get(key):
                value = str(el[key]).strip()
                if value.lower().startswith(('javascript:', 'vbscript:')):
                    del el.attrs[key]
                elif not value.lower().startswith(('data:', 'blob:', '#')):
                    el[key] = urljoin(base_url, value)
        if el.name == 'details':
            el.attrs['open'] = ''
    for tag in cleaned.select('form, input, button, select, textarea'):
        tag.decompose()
    return cleaned


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def table_matrix(table):
    """Expand rowspans/colspans without dropping cells; keep original cell geometry."""
    matrix, layouts, occupied = [], [], {}
    for row_idx, tr in enumerate(table.find_all('tr')):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if not cells:
            continue
        row, raw, col = [], [], 0
        def pad_to(n):
            while len(row) <= n:
                row.append('')
        for cell in cells:
            while (row_idx, col) in occupied:
                pad_to(col)
                row[col] = occupied.pop((row_idx, col))
                col += 1
            text = norm(cell.get_text(' ', strip=True))
            rs, cs = max(1, int(cell.get('rowspan', 1))), max(1, int(cell.get('colspan', 1)))
            raw.append({'text': text, 'tag': cell.name, 'rowspan': rs, 'colspan': cs, 'column_start': col})
            for c in range(col, col + cs):
                pad_to(c)
                row[c] = text
                for rr in range(row_idx + 1, row_idx + rs):
                    occupied[(rr, c)] = text
            col += cs
        tail = sorted(c for (r, c) in occupied if r == row_idx)
        for c in tail:
            pad_to(c)
            row[c] = occupied.pop((row_idx, c))
        matrix.append(row)
        layouts.append(raw)
    columns = max((len(row) for row in matrix), default=0)
    for row in matrix:
        row.extend([''] * (columns-len(row)))
    return matrix, layouts


def table_export(table, table_id: str, out: Path, base_url: str):
    matrix, layouts = table_matrix(table)
    first_has_header = bool(table.find('thead')) or bool(layouts and layouts[0] and all(c['tag'] == 'th' for c in layouts[0]))
    header = matrix[0] if first_has_header and matrix else [f'column_{i+1}' for i in range(len(matrix[0]) if matrix else 0)]
    # Preserve header text, disambiguate repeated/empty labels for CSV.
    header = [norm(h) or f'column_{i+1}' for i, h in enumerate(header)]
    counts = {}
    unique = []
    for h in header:
        counts[h] = counts.get(h, 0) + 1
        unique.append(h if counts[h] == 1 else f'{h}_{counts[h]}')
    data_rows = matrix[1:] if first_has_header else matrix
    csv_path = out / f'{table_id}.csv'
    json_path = out / f'{table_id}.json'
    out.mkdir(parents=True, exist_ok=True)
    with csv_path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(unique)
        writer.writerows(data_rows)
    package = {'source_url': base_url, 'id': table_id,
               'caption': norm(table.caption.get_text(' ', strip=True)) if table.caption else '',
               'columns': unique, 'matrix': matrix, 'raw_cell_layout': layouts,
               'has_header': first_has_header, 'rows': [dict(zip(unique, r)) for r in data_rows],
               'source_html': str(table)}
    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
    return package, csv_path, json_path


def format_md_table(tab):
    cols = tab['columns']
    rows = tab['rows']
    if not cols:
        return '*Empty table*'
    def safe(v):
        return str(v).replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')
    parts = ['| ' + ' | '.join(map(safe, cols)) + ' |',
             '| ' + ' | '.join('---' for _ in cols) + ' |']
    for row in rows:
        parts.append('| ' + ' | '.join(safe(row.get(k, '')) for k in cols) + ' |')
    return '\n'.join(parts)


def semantic_markdown(cleaned, title: str, page_url: str, table_info: list):
    clone = BeautifulSoup(str(cleaned), 'lxml')
    replacements = {}
    for t, tab in zip(clone.find_all('table'), table_info):
        token = f'AITABLEMARKER{len(replacements)+1:06d}END'
        replacements[token] = f'\n\n### Table {tab["id"]}' + (f': {tab["caption"]}' if tab['caption'] else '') + '\n\n' + format_md_table(tab) + '\n\n'
        t.replace_with(clone.new_tag('p', string=token))
    body = clone.body or clone
    md = to_markdown(str(body), heading_style='ATX', bullets='-', strip=['script', 'style'])
    for token, content in replacements.items():
        md = md.replace(token, content)
    md = re.sub(r'\n{4,}', '\n\n\n', md).strip()
    # Do not summarize/omit prose or tables. AI text is a faithful text-first projection.
    prefix = f'# {title}\n\nSource URL: {page_url}\n\n'
    if md.lstrip().startswith('# '):
        prefix = f'Source URL: {page_url}\n\n'
    return prefix + md + '\n'


DENSE_CSS = r'''
:root { color-scheme:light; }
* {box-sizing:border-box}
html,body {margin:0;padding:0;background:#fff;color:#142030}
body {font-family:Arial,"Segoe UI","Microsoft YaHei",sans-serif;font-size:16px;line-height:1.27}
#paper {width:1600px;padding:22px 25px 20px 25px;background:#fff}
#metadata {border-bottom:2px solid #3c596c;margin-bottom:10px;padding-bottom:8px;overflow-wrap:anywhere}
#metadata strong {font-size:19px} #metadata small{display:block;color:#526273;font-size:12px;margin-top:3px}
h1,h2,h3,h4,h5,h6 {color:#123f61;line-height:1.2;margin:12px 0 5px}
h1 {font-size:23px} h2{font-size:20px} h3{font-size:18px} h4,h5,h6{font-size:16px}
p {margin:4px 0 7px} ul,ol {padding-left:22px;margin:5px 0 9px} li{margin:2px 0}
table {border-collapse:collapse;width:100%;margin:7px 0 12px;table-layout:auto;font-size:14.5px}
th,td {border:1px solid #93a6b8;padding:4px 7px;text-align:left;vertical-align:top;overflow-wrap:anywhere;word-break:normal}
th {background:#e7eef5;color:#1c3550;font-weight:700}
tr:nth-child(even) td {background:#f6f8fb}
pre {font-family:Consolas,"Cascadia Mono",Menlo,monospace;font-size:14px;line-height:1.32;white-space:pre!important;overflow-wrap:normal!important;word-break:normal!important;max-width:none;display:table;padding:8px 11px;border:1px solid #d0d9e4;border-left:3px solid #6d91bc;background:#f5f7fa;margin:6px 0 10px;tab-size:4}
code {font-family:Consolas,Menlo,monospace;font-size:.93em}
pre code {white-space:pre!important}
blockquote {border-left:3px solid #9db8d0;padding:4px 12px;margin:8px 0;background:#f5f8fc}
img,svg {max-width:100%;height:auto}
figure {margin:6px 0 10px} figcaption {font-size:13px;color:#53647b}
a {color:#174a7c;text-decoration:none}
hr {border:0;border-top:1px solid #cdd5e1;margin:8px 0}
''' 


def build_dense_html(cleaned, title: str, url: str, width: int, font: int):
    stylesheet = DENSE_CSS + f'\n#paper{{width:{width}px}}body{{font-size:{font}px}}\n'
    # Keep full HTML/tables, not textual summaries.
    safe_title = html.escape(title)
    safe_url = html.escape(url, quote=True)
    inner = str(cleaned.body or cleaned)
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{stylesheet}</style></head>
<body><div id="paper"><div id="metadata"><strong>{safe_title}</strong><small>Source: {safe_url}</small></div>
<div id="doc">{inner}</div></div></body></html>'''


def choose_cuts(boundaries: list[float], height: int, max_height: int):
    """Prefer a row/paragraph boundary near the bottom; overlap hard cuts."""
    starts, y = [], 0
    while y < height - 1:
        limit = min(y + max_height, height)
        eligible = [b for b in boundaries if y + max_height*.80 <= b <= limit - 2]
        if limit == height:
            end, next_start = height, height
        elif eligible:
            end = max(eligible)
            next_start = end
        else:
            end = limit
            next_start = max(y + 1, end - 28)
        if end <= y:
            end = limit
            next_start = max(y + 1, end - 28)
        starts.append((round(y), round(end)))
        y = next_start
    return starts or [(0, height)]


def banner_png(source: Path, page_name: str, vi: int, vt: int, hi: int, ht: int):
    with Image.open(source) as im:
        im = im.convert('RGB')
        size = max(14, min(24, int(im.width / 90)))
        try:
            font = ImageFont.truetype('DejaVuSans.ttf', size)
        except OSError:
            font = ImageFont.load_default()
        pad = 13
        bar = size+pad*2
        final = Image.new('RGB', (im.width, im.height+bar), '#192638')
        final.paste(im, (0, bar))
        label = f'{page_name[:75]}   |   vertical {vi}/{vt}   horizontal {hi}/{ht}'
        draw = ImageDraw.Draw(final)
        draw.text((14, pad//2), label, fill='#f3f8ff', font=font)
        final.save(source, 'PNG', optimize=True)
        return final.width, final.height


async def image_pages(browser_context, dense: str, page_id: str, title: str, url: str,
                      directory: Path, args):
    """Render main pages plus compact horizontal supplements for exceptionally long code."""
    page = await browser_context.new_page()
    await page.route('**/*', lambda route: route.abort())
    output, issues = [], []
    try:
        await page.set_viewport_size({'width': args.image_width, 'height': 900})
        await page.set_content(dense, wait_until='load', timeout=30000)
        await page.evaluate('document.fonts.ready')
        metrics = await page.evaluate('''() => {
          const paper=document.querySelector('#paper');
          const total=Math.max(paper.scrollHeight,document.documentElement.scrollHeight);
          const nodes=[...document.querySelectorAll('#doc h1,#doc h2,#doc h3,#doc h4,#doc h5,#doc h6,#doc p,#doc li,#doc tr,#doc pre,#doc blockquote,#doc figure')];
          const cuts=[...new Set(nodes.map(n=>Math.ceil(n.getBoundingClientRect().bottom+scrollY)).filter(n=>n>0&&n<total))].sort((a,b)=>a-b);
          const wide=[...document.querySelectorAll('#doc pre,#doc table,#doc img,#doc svg')].map(n=>{
            const r=n.getBoundingClientRect();
            return {top:r.top+scrollY,bottom:r.bottom+scrollY,edge:r.left+scrollX+Math.max(r.width,n.scrollWidth)};
          });
          return {total:Math.ceil(total),cuts,wide};
        }''')
        cuts = choose_cuts(metrics['cuts'], metrics['total'], args.image_height)
        directory.mkdir(parents=True, exist_ok=True)
        for vi, (y0, y1) in enumerate(cuts, 1):
            path = directory / f'{page_id}.v{vi:03d}.png'
            await page.screenshot(path=str(path), type='png', animations='disabled',
                clip={'x':0,'y':y0,'width':args.image_width,'height':y1-y0},
                full_page=True, timeout=60000)
            w,h = banner_png(path, title, vi, len(cuts), 1, 1)
            output.append(dict(page_id=page_id,url=url,title=title,image=path.name,
                vertical_part=vi,vertical_parts=len(cuts),horizontal_part=1,horizontal_parts=1,
                y_start_css_px=y0,y_end_css_px=y1,x_start_css_px=0,
                horizontal_offsets_css_px='0',image_width_px=w,image_height_px=h))
            wide = [x for x in metrics['wide'] if x['bottom'] > y0 and x['top'] < y1 and
                    x['edge'] > args.image_width+2]
            for wi, block in enumerate(wide, 1):
                b_top=max(y0, int(block['top'])-3)
                b_bottom=min(y1, int(block['bottom'])+4)
                if b_bottom <= b_top:
                    continue
                edge=int(block['edge'])+2
                offsets=[]
                x=args.image_width-args.horizontal_overlap
                while x < edge:
                    offsets.append(x)
                    x += args.image_width-args.horizontal_overlap
                slices=[]
                for x in offsets:
                    segment_width=min(args.image_width, edge-x)
                    data=await page.screenshot(type='png',animations='disabled',full_page=True,
                        clip={'x':x,'y':b_top,'width':segment_width,'height':b_bottom-b_top},
                        timeout=60000)
                    with Image.open(BytesIO(data)) as im:
                        slices.append((x,im.convert('RGB').copy()))
                if not slices:
                    continue
                label_height=34
                assembled_width=max(img.width for _,img in slices)
                assembled_height=sum(img.height+label_height for _,img in slices)
                assembled=Image.new('RGB',(assembled_width,assembled_height),'#ffffff')
                draw=ImageDraw.Draw(assembled)
                try:
                    font=ImageFont.truetype('DejaVuSans.ttf',17)
                except OSError:
                    font=ImageFont.load_default()
                cursor=0
                for index,(x,img) in enumerate(slices,1):
                    draw.rectangle((0,cursor,assembled_width,cursor+label_height-1),fill='#243449')
                    draw.text((12,cursor+8),f'{title[:42]} | wide {wi} | segment {index}/{len(slices)} | X={x}px',fill='white',font=font)
                    cursor+=label_height
                    assembled.paste(img,(0,cursor))
                    cursor+=img.height
                extra=directory/f'{page_id}.v{vi:03d}.wide{wi:03d}.png'
                assembled.save(extra,'PNG',optimize=True)
                output.append(dict(page_id=page_id,url=url,title=title,image=extra.name,
                    vertical_part=vi,vertical_parts=len(cuts),horizontal_part=2,
                    horizontal_parts=len(offsets)+1,y_start_css_px=b_top,y_end_css_px=b_bottom,
                    x_start_css_px=offsets[0],horizontal_offsets_css_px=';'.join(map(str,offsets)),
                    image_width_px=assembled.width,image_height_px=assembled.height))
    finally:
        await page.close()
    return output, issues


def scope_checker(seeds: list[str], custom_prefix: str | None):
    first = urlsplit(seeds[0])
    origin = (first.scheme, first.netloc)
    base_dir = first.path.rsplit('/', 1)[0] + '/'
    prefix = urlsplit(custom_prefix).path if custom_prefix and '://' in custom_prefix else (custom_prefix or base_dir)
    def allowed(url):
        p = urlsplit(url)
        return (p.scheme, p.netloc) == origin and p.path.startswith(prefix) and (Path(p.path).suffix.lower() in DOC_EXTENSIONS)
    return allowed, prefix


def all_links(html_text: str, base_url: str, selector: str):
    soup = BeautifulSoup(html_text, 'lxml')
    found = []
    for a in soup.select(selector):
        href = a.get('href')
        if href:
            link = normalize_url(urljoin(base_url, href))
            found.append({'url':link, 'label':norm(a.get_text(' ',strip=True))})
    return list({v['url']:v for v in found}.values())


async def inline_images(clean, page, base_url: str, warnings: list[str], max_size=6_000_000):
    """Store same-origin image resources as data: URIs so cleaned HTML and PNG work offline."""
    origin = urlsplit(base_url).netloc
    for tag in clean.find_all('img'):
        src = tag.get('src')
        if not src or src.startswith('data:'):
            continue
        source = urljoin(base_url, src)
        if urlsplit(source).netloc != origin:
            warnings.append(f'External image not embedded: {source}')
            continue
        try:
            response = await page.request.get(source, timeout=10000)
            if not response.ok:
                raise ValueError(f'HTTP {response.status}')
            ctype = response.headers.get('content-type', '').split(';')[0].lower().strip()
            data = await response.body()
            if ctype not in IMAGE_MIMES or len(data)>max_size:
                raise ValueError(f'image format/size unsupported ({ctype}, {len(data)} bytes)')
            tag['src'] = f'data:{ctype};base64,{base64.b64encode(data).decode()}'
        except Exception as exc:
            warnings.append(f'Image unavailable in offline rendering: {source} ({exc})')


async def fetch_one(page, url: str, out: Path, args):
    page_id = ident(url)
    warnings = []
    base = out
    fetched = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    response = await page.goto(url, wait_until='domcontentloaded', timeout=args.timeout_ms)
    await page.wait_for_timeout(args.wait_ms)
    if args.expand_details:
        await page.evaluate("() => document.querySelectorAll('details').forEach(x=>x.open=true)")
        await page.wait_for_timeout(100)
    status_code = response.status if response else 200
    dom = await page.content()
    if status_code >= 400:
        raise ValueError(f'HTTP {status_code}: {url}')
    if urlsplit(page.url).netloc != urlsplit(url).netloc and re.search(
        r'<input[^>]*type=["\']?password|\b(sign in|log in|single sign.on)\b',
        dom[:120000], flags=re.IGNORECASE):
        raise ValueError('Redirected to a login page; try --headed --login --profile-dir')
    soup = BeautifulSoup(dom, 'lxml')
    if not soup.body:
        raise ValueError('No HTML body returned; check login or --content-selector')
    main = choose_main(soup, args.content_selector)
    title = norm((main.select_one('h1') or soup.select_one('h1') or soup.title or soup.body).get_text(' ',strip=True))[:300] or url
    cleaned = remove_chrome(str(main), url, args.exclude_selector or [])
    if len(cleaned.get_text(' ', strip=True)) < 30:
        warnings.append('Very little extracted text; verify authentication and --content-selector.')
    if soup.find('iframe'):
        warnings.append('The original document contains iframe(s); iframe content may require separate URLs.')
    if soup.find('canvas'):
        warnings.append('The original document contains canvas; keep MHTML / original page for visual comparison.')
    await inline_images(cleaned, page, url, warnings)
    links = all_links(dom, url, args.link_selector)
    raw_path = base/'raw'/f'{page_id}.raw.html'
    src_path = base/'raw'/f'{page_id}.source.html'
    mhtml_path = base/'raw'/f'{page_id}.mhtml'
    cleaned_path = base/'cleaned_html'/f'{page_id}.cleaned.html'
    md_path = base/'markdown'/f'{page_id}.md'
    ai_path = base/'ai_text'/f'{page_id}.ai.md'
    for p in (raw_path,src_path,mhtml_path,cleaned_path,md_path,ai_path):
        p.parent.mkdir(parents=True,exist_ok=True)
    raw_path.write_text(dom,encoding='utf-8')
    if response:
        try:
            body = await response.text()
            if '<html' in body.lower() or '<!doctype' in body.lower():
                src_path.write_text(body,encoding='utf-8')
        except Exception as exc:
            warnings.append(f'Original HTTP response body unavailable: {exc}')
    if args.mhtml:
        try:
            session = await page.context.new_cdp_session(page)
            data = await session.send('Page.captureSnapshot', {'format':'mhtml'})
            mhtml_path.write_text(data['data'],encoding='utf-8')
            await session.detach()
        except Exception as exc:
            warnings.append(f'MHTML archival unavailable: {exc}')
    tables = []
    table_files = []
    for i, tab in enumerate(cleaned.find_all('table'), 1):
        tid = f'{page_id}.t{i:03d}'
        data, cp, jp = table_export(tab, tid, base/'tables', url)
        tables.append(data)
        table_files.extend([cp.relative_to(base).as_posix(), jp.relative_to(base).as_posix()])
    dense = build_dense_html(cleaned, title, url, args.image_width, args.font_size)
    cleaned_path.write_text(dense,encoding='utf-8')
    md = semantic_markdown(cleaned,title,url,tables)
    md_path.write_text(md,encoding='utf-8')
    # AI text is not a summary; every paragraph and extracted table is retained.
    ai = md + '\n## Machine-readable table references\n\n' + '\n'.join(
        f'- Table {i+1}: {table_files[i*2]} ; {table_files[i*2+1]}' for i in range(len(tables))) + '\n'
    ai_path.write_text(ai,encoding='utf-8')
    row = dict(page_id=page_id,title=title,url=url,fetched_at_utc=fetched,status='ok',
        source_html=src_path.relative_to(base).as_posix() if src_path.exists() else '',
        raw_dom_html=raw_path.relative_to(base).as_posix(),
        original_mhtml=mhtml_path.relative_to(base).as_posix() if mhtml_path.exists() else '',
        cleaned_html=cleaned_path.relative_to(base).as_posix(),
        markdown=md_path.relative_to(base).as_posix(),
        ai_text=ai_path.relative_to(base).as_posix(), table_count=len(tables),
        table_files=table_files, image_count=0, images=[], warnings=warnings)
    return row, dense, links


def write_csv(path: Path, columns: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key:'; '.join(str(y) for y in val) if isinstance(val,list) else val for key,val in row.items() if key in columns})


async def run(args):
    seeds = load_seeds(args)
    output = Path(args.out).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    browser_exe = discover_browser(args.browser_executable)
    print('Browser:', browser_exe or 'Playwright managed Chromium')
    print('Output:', output)
    allowed, scope = scope_checker(seeds,args.scope_prefix)
    print('Crawl scope:', scope, '(only used with --crawl)')
    queue, visited, queued = deque(seeds), set(), set(seeds)
    records, image_records, failures, toc = [], [], [], []
    async with async_playwright() as p:
        kw = {'headless':not args.headed, 'args':['--disable-extensions']}
        if browser_exe:
            kw['executable_path'] = browser_exe
        if args.profile_dir:
            context = await p.chromium.launch_persistent_context(str(Path(args.profile_dir).expanduser().resolve()),
                        viewport={'width':1440,'height':1000},
                        ignore_https_errors=args.ignore_https_errors, **kw)
            browser = None
        else:
            browser = await p.chromium.launch(**kw)
            context = await browser.new_context(viewport={'width':1440,'height':1000},
                                                 accept_downloads=False, ignore_https_errors=args.ignore_https_errors)
        page = await context.new_page()
        try:
            if args.login:
                if not args.headed:
                    raise ValueError('To log in interactively use --headed --login.')
                print('\nOpen the site in the browser, complete login, then press ENTER in this terminal.')
                await page.goto(seeds[0],wait_until='domcontentloaded',timeout=args.timeout_ms)
                await asyncio.to_thread(input,'After login, press ENTER: ')
            while queue and len(records)+len(failures) < args.max_pages:
                url = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                print(f'[{len(records)+len(failures)+1}/{args.max_pages}] {url}',flush=True)
                try:
                    row,dense,links = await fetch_one(page,url,output,args)
                    images, issues = await image_pages(context,dense,row['page_id'],row['title'],url,
                                                        output/'ai_images',args)
                    row['images'] = [('ai_images/'+item['image']) for item in images]
                    row['image_count'] = len(images)
                    row['warnings'].extend(issues)
                    records.append(row)
                    image_records += [{**item,'image':'ai_images/'+item['image']} for item in images]
                    toc += [{'title':v['label'], 'url':v['url']} for v in links]
                    if args.crawl:
                        for link in links:
                            u = link['url']
                            if u not in visited and u not in queued and allowed(u):
                                queue.append(u)
                                queued.add(u)
                    print(f'    tables={row["table_count"]}, PNG={len(images)}, links={len(links)}, warnings={len(row["warnings"])}')
                except Exception as exc:
                    message = f'{type(exc).__name__}: {exc}'
                    failures.append({'url':url,'error':message})
                    print('    ERROR:',message,file=sys.stderr)
            if queue:
                print(f'NOTE: stopped at --max-pages {args.max_pages}; {len(queue)} URL(s) remain.')
        finally:
            await context.close()
            if browser:
                await browser.close()
    toc = list({row['url']:row for row in toc}.values())
    (output/'toc.json').write_text(json.dumps(toc,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'toc.md').write_text('# Documentation navigation index\n\n' + ''.join(
        f'- [{r["title"] or r["url"]}]({r["url"]})\n' for r in toc),encoding='utf-8')
    write_csv(output/'manifest.csv',MANIFEST_FIELDS,records)
    write_csv(output/'image_manifest.csv',IMAGE_FIELDS,image_records)
    (output/'manifest.json').write_text(json.dumps({'source_urls':seeds,'pages':records,
          'images':image_records,'errors':failures,'pending_urls':list(queue)},ensure_ascii=False,indent=2),encoding='utf-8')
    text = ['# Documentation export manifest', '', f'Pages saved: {len(records)}',
            f'PNG images: {len(image_records)}',f'Failures: {len(failures)}','',
            'No AI-generated summaries are substituted for source text.',
            'Each item retains original HTML/DOM, cleaned HTML, full Markdown, and structured tables.','']
    for row in records:
        text.extend([f'## {row["title"]}',f'- URL: {row["url"]}',
                     f'- Source: {row["raw_dom_html"]}',
                     f'- Offline archive: {row["original_mhtml"] or "not available"}',
                     f'- AI text: {row["ai_text"]}',
                     f'- Tables: {row["table_count"]}',
                     f'- PNG images: {row["image_count"]}'])
        text.extend(f'  - {image}' for image in row['images'])
        text.extend(f'- WARNING: {warning}' for warning in row['warnings'])
        text.append('')
    if failures:
        text.extend(['## Errors',''] + [f'- {x["url"]}: {x["error"]}' for x in failures])
    (output/'manifest.txt').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(f'\nDONE: {len(records)} pages, {len(image_records)} images, {len(failures)} failures')
    print('Manifest:', output/'manifest.txt')
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--url',action='append',help='Seed URL; repeat for multiple pages')
    p.add_argument('--urls-file',help='Text file with one URL per line')
    p.add_argument('--crawl',action='store_true',help='Follow navigation/sidebar links within the seed path on the same site')
    p.add_argument('--max-pages',type=int,default=200)
    p.add_argument('--scope-prefix',help='Restrict crawl to this URL path prefix (default: seed page directory)')
    p.add_argument('--link-selector',default=NAV_SELECTOR,help='CSS selector for navigation links (crawl mode)')
    p.add_argument('--content-selector',help='Explicit CSS selector for the real documentation body')
    p.add_argument('--exclude-selector',action='append',help='Additional CSS selector to remove from reflowed copy')
    p.add_argument('--out',default='manual_ai_export')
    p.add_argument('--browser-executable','--browser',dest='browser_executable',help='Path to installed Edge/Chrome/Chromium browser')
    p.add_argument('--profile-dir',help='Dedicated browser profile directory for SSO; keep PRIVATE')
    p.add_argument('--headed',action='store_true',help='Show browser window')
    p.add_argument('--login',action='store_true',help='Pause after opening first URL for interactive login; use with --headed')
    p.add_argument('--wait-ms',type=int,default=1000,help='Wait after each navigation for JavaScript content')
    p.add_argument('--timeout-ms',type=int,default=45000)
    p.add_argument('--ignore-https-errors',action='store_true')
    p.add_argument('--expand-details',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--mhtml',action=argparse.BooleanOptionalAction,default=True,help='Archive offline MHTML when Chromium CDP supports it')
    p.add_argument('--image-width',type=int,default=1600)
    p.add_argument('--image-height',type=int,default=2600)
    p.add_argument('--font-size',type=int,default=16)
    p.add_argument('--scale',type=float,default=1.5)
    p.add_argument('--horizontal-overlap',type=int,default=85)
    args=p.parse_args()
    if args.image_width < 700 or args.image_height < 700 or args.scale <= 0 or args.max_pages < 1 or args.horizontal_overlap < 0 or args.horizontal_overlap >= args.image_width:
        p.error('Invalid image dimensions, overlap or page limit')
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f'FATAL: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
