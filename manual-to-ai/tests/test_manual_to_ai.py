from pathlib import Path
from types import SimpleNamespace
import asyncio

from bs4 import BeautifulSoup
from manual_to_ai import (
    choose_main, remove_chrome, table_matrix, table_export,
    semantic_markdown, build_dense_html, choose_cuts,
    ident, normalize_url, all_links, image_pages,
)

FIX = Path(__file__).parent / 'fixtures' / 'deal.html'
URL = 'https://documentation.example.org/manual/deal.html'


def test_choose_content_and_clean_navigation():
    soup = BeautifulSoup(FIX.read_text(encoding='utf-8'), 'lxml')
    main = choose_main(soup, None)
    clean = remove_chrome(str(main), URL, [])
    text = clean.get_text(' ', strip=True)
    assert 'deal.fieldname' in text
    assert 'Field_144' in text
    assert 'Hidden but important details' in text
    assert 'SIDEBAR UNUSED' not in text
    assert 'FOOTER UNUSED' not in text


def test_table_merged_geometry():
    table = BeautifulSoup('<table><tr><th>Name</th><th>Type</th></tr><tr><td rowspan="2">Foo</td><td>A</td></tr><tr><td>B</td></tr></table>', 'lxml').table
    rows, layout = table_matrix(table)
    assert rows == [['Name','Type'],['Foo','A'],['Foo','B']]
    assert layout[1][0]['rowspan'] == 2


def test_full_md_includes_all_table_rows(tmp_path):
    soup = BeautifulSoup(FIX.read_text(encoding='utf-8'),'lxml')
    clean = remove_chrome(str(choose_main(soup,None)), URL, [])
    tables = [table_export(t, f'deal.t{i:03d}',tmp_path,URL)[0] for i,t in enumerate(clean.find_all('table'), 1)]
    md = semantic_markdown(clean, 'deal', URL, tables)
    assert tables[0]['columns'] == ['Name','Type','Description']
    assert len(tables[0]['rows']) == 144
    assert 'Field_001' in md and 'Field_144' in md
    assert 'Additional notes' in md
    assert 'Hidden but important details' in md
    assert 'AITABLEMARKER' not in md
    assert len(list(tmp_path.glob('*.csv'))) == 1
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_navigation_scope_helpers():
    assert normalize_url('https://EXAMPLE.org/manual/deal.html#syntax') == 'https://example.org/manual/deal.html'
    assert ident('https://a/deal.html') != ident('https://b/deal.html')
    links = all_links('<nav><a href="bond.html">bond</a></nav>',URL,'nav a[href]')
    assert links[0]['url'] == 'https://documentation.example.org/manual/bond.html'


def test_paging_no_gap():
    bounds = choose_cuts([1100,2100,3300], 4300, 1900)
    assert bounds[0][0] == 0 and bounds[-1][1] == 4300
    assert all(bounds[i+1][0] <= bounds[i][1] for i in range(len(bounds)-1))


def test_browser_renders_dense_images(tmp_path):
    from playwright.async_api import async_playwright
    soup = BeautifulSoup(FIX.read_text(encoding='utf-8'),'lxml')
    clean = remove_chrome(str(choose_main(soup,None)),URL,[])
    dense = build_dense_html(clean,'deal',URL,1400,16)
    args = SimpleNamespace(image_width=1400,image_height=1900,scale=1,horizontal_overlap=80)

    async def do_test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path='/usr/bin/chromium')
            context = await browser.new_context(viewport={'width':1400,'height':900})
            try:
                entries,_ = await image_pages(context,dense,'deal','deal',URL,tmp_path,args)
            finally:
                await context.close()
                await browser.close()
            return entries
    if not Path('/usr/bin/chromium').exists():
        import pytest
        pytest.skip('Browser fixture test is only enabled when /usr/bin/chromium is available')
    entries = asyncio.run(do_test())
    assert len(entries) == 4
    assert any('wide' in e['image'] for e in entries)
    assert all((tmp_path/e['image']).exists() for e in entries)
    assert entries[1]['horizontal_offsets_css_px'] != '0'
