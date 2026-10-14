# 网页手册 → 原文归档 + AI 高密度图片

此工具面向 **ASAP Scripting Manual** 一类的技术文档（正文、字段表、对象说明、语法、示例）。在 Windows、macOS、Linux 的 Python 3.10+ 上运行。它不会自动修改、上传或者共享源网页内容。

## 1. 安装

下载 ZIP 并解压，在解压文件夹中打开 Cursor / VS Code 的 PowerShell Terminal：

```powershell
python -m pip install -r requirements.txt
```

**你不需要运行 `python -m playwright install chromium`。** 工具会尝试自动查找电脑中安装的 Microsoft Edge、Chrome；找不到时才尝试 Playwright 自带的 Chromium。若自动查找失败，通过 `--browser-executable` 明确指定 Edge 的完整路径。

```powershell
python .\manual_to_ai.py --url "https://你的文档网站/manual/deal.html" --out "D:\Exports\ASAP_manual"
```

## 2. 一次性抓取整个导航目录（左栏）

从手册的一个页面开始，通过站点导航/左栏链接发现同目录下的其他 HTML 页面：

```powershell
python .\manual_to_ai.py `
  --url "https://你的文档网站/manual/deal.html" `
  --crawl --max-pages 500 `
  --out "D:\Exports\ASAP_manual"
```

默认仅抓取**种子 URL 所在网站、所在路径目录内**且扩展名为 `.html` / `.htm` 或无扩展名的页面；不会跟到其他站点。

如果某些目录链接不在常见的导航元素内：

```powershell
python .\manual_to_ai.py --url "https://你的文档网站/manual/deal.html" `
  --crawl --link-selector "a[href]" --max-pages 500
```

这仍受同站点、同路径前缀限制。可以显式设置 `--scope-prefix "/manual/"` 缩小/扩大目录范围。**网页中的侧栏如果需逐级点击后才加载链接，脚本无法保证自动枚举所有子页**；请展开导航，或把每个 URL 写入 `urls.txt`，并检查最终 `toc.md` 和 `manifest.txt`。

## 3. 用 URL 列表精确批量抓取（最可靠）

`urls.txt` 每行一个网页 URL，允许 `#` 注释行：

```text
https://你的文档网站/manual/deal.html
https://你的文档网站/manual/bond.html
https://你的文档网站/manual/accounts.html
```

执行：

```powershell
python .\manual_to_ai.py --urls-file .\urls.txt --out "D:\Exports\ASAP_manual"
```

## 4. 必须登录的网站

使用一个**专用的**浏览器资料夹保存登录会话，避免修改个人或公司浏览器的日常 Profile：

```powershell
python .\manual_to_ai.py --url "https://你的文档网站/manual/deal.html" `
  --crawl --max-pages 300 `
  --headed --login --profile-dir ".\private-login-profile" `
  --out "D:\Exports\ASAP_manual"
```

浏览器窗口出现后手动登录，然后回到 PowerShell 按 Enter。下次运行可以保留 `--profile-dir`，去掉 `--login`；若登录过期则重复。**该 profile 可能包含敏感 cookie，请勿提交到 Git 或与生成文件一起共享。**

## 5. 之前遇到 Chromium 安装失败怎么办

如果 Edge 自动识别不成功，先找到 Edge 可执行文件：

```powershell
$edge = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edge) { throw "Edge executable not found" }
python .\manual_to_ai.py --urls-file .\urls.txt --browser-executable "$edge"
```

**不要传空的 `$edge`**，否则会出现 `argument --browser-executable: expected one argument`。

## 6. 输出结构

```text
ASAP_manual/
├── raw/                       # 浏览器加载后的 DOM HTML，响应 HTML，MHTML（可用时）
│   ├── deal-xxxxxxxxxx.raw.html
│   ├── deal-xxxxxxxxxx.source.html
│   └── deal-xxxxxxxxxx.mhtml
├── cleaned_html/              # 正文高密度重排 HTML
├── markdown/                  # 全文 Markdown（不是 AI 摘要）
├── ai_text/                   # 全文 + 结构化表格引用
├── tables/                    # 每张表格一份 CSV 和 JSON
├── ai_images/                 # 视觉模型阅读的高密度 PNG
│   ├── deal-xxxxxxxxxx.v001.png
│   ├── deal-xxxxxxxxxx.v001.wide001.png
│   └── deal-xxxxxxxxxx.v002.png
├── toc.md                     # 抽取的站点导航链接
├── toc.json
├── manifest.txt               # 可读总索引
├── manifest.csv
├── manifest.json
└── image_manifest.csv         # 每个 PNG 对应原 URL、图片分段坐标
```

`*.wide001.png` 表示某行特别长时，超出正常页面宽度的部分；它把多段横向裁切紧凑地上下排列，并且标注 X 偏移。**原始代码从不在文本行内部自动折行。**

## 7. 图片密度参数

默认宽 `1600 CSS px`、每页高 `2600 CSS px`、字体 `16 CSS px`、渲染密度 `1.5`，这是清晰度和张数的折中。可以改成：

```powershell
python .\manual_to_ai.py --urls-file .\urls.txt `
  --image-width 1800 --image-height 3200 --font-size 16 --scale 1.5
```

高度设得很大虽然页数更少，但上传 AI 后可能缩小到难以辨认。建议先测试 1–2 个页面再确定参数。

## 8. 质量限制与安全

- 优先上传 `ai_text/*.ai.md` 和 `tables/*.json`，必要时再附 PNG；文字与结构化表格比视觉 OCR 更准确。
- 抓取的是**浏览器加载后的 DOM**，同时尝试保存 **原始 HTTP HTML** 和包含相关资源的 **MHTML**；不保证所有站点都可以生成可离线完整工作的 MHTML。
- 默认展开 HTML `<details>`，但站点通过前端点击/分页/API 才加载的内容仍可能需要手动处理；跨域 iframe/canvas/异域图片可能需要另外归档，脚本会尽可能在 manifest 标记警告。
- 还原字段表时同时保留合并单元格原始 HTML/坐标，CSV 展开为标准矩阵。CSV 不保证保留跨行、跨列外观。
- 只抓取你有权访问的资料，遵守网站条款、网络访问策略和公司数据政策；若为公司内部 ASAP/ABS 资料，上传 ChatGPT 之前应先获得相应授权。

运行单元测试：`python -m pip install pytest`，然后执行 `python -m pytest -q`。
