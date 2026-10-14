import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace

from repo_to_images import read_text, html_document, source_files, EXTENSIONS, export


def test_gbk(tmp_path):
    file = tmp_path / '中文.py'
    file.write_bytes('变量 = 1\n'.encode('gb18030'))
    content, enc = read_text(file)
    assert content == '变量 = 1\n'
    assert enc == 'gb18030'


def test_ignore_and_sensitive(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / '.venv').mkdir()
    (tmp_path / 'src' / 'test.py').write_text('x=1\n')
    (tmp_path / '.venv' / 'cache.py').write_text('x=1\n')
    (tmp_path / '.env').write_text('secret')
    paths, skipped = source_files(tmp_path.resolve(), (tmp_path.parent / 'export').resolve(), EXTENSIONS, False)
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == ['src/test.py']


def test_unwrapped_html():
    result = html_document('    answer = "<a>"\n', 'src/main.py', 17, 17, 18, 'monokai')
    assert 'white-space:pre!important' in result
    assert 'overflow-wrap:normal!important' in result
    assert '&lt;' in result
    assert 'Lines 17-17' in result


def test_export_browser(tmp_path):
    repo = tmp_path / 'src'
    repo.mkdir()
    (repo / 'main.py').write_text('x = "' + 'A'*400 + '"\nprint(x)\n', encoding='utf-8')
    output = tmp_path / 'images'
    args = SimpleNamespace(repo=str(repo), out=str(output), max_lines=1,
                           max_width=650, overlap=35, font_size=16,
                           scale=1, style='monokai', extensions=None,
                           include_sensitive=False, browser_executable=None)
    try:
        assert asyncio.run(export(args)) == 0
    except Exception as exc:
        if 'Executable doesn' in str(exc) or 'browserType.launch' in str(exc):
            import pytest
            pytest.skip('Install Chromium with python -m playwright install chromium')
        raise
    assert (output / 'manifest.txt').exists()
    with (output / 'manifest.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 2
    assert any(int(r['horizontal_parts']) > 1 for r in rows)
    assert all((output / r['image_file']).exists() for r in rows)
