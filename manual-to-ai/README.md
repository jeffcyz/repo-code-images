# Web Manual → AI Documentation Exporter

See [README_中文.md](README_中文.md) for installation, crawling and login instructions.

This standalone documentation exporter preserves original website HTML/MHTML, cleaned HTML, full Markdown, structured field tables (CSV/JSON), and compact PNG image pages. Generates manifest.txt, manifest.csv, manifest.json and image_manifest.csv.

**Install:**

~~~powershell
cd manual-to-ai
python -m pip install -r requirements.txt
~~~

**Run with Edge/Chrome auto-discovery (no Playwright Chromium download required):**

~~~powershell
python .\manual_to_ai.py --url "https://your-doc-site/manual/deal.html" --crawl --out "D:\Exports\ASAP_Manual"
~~~

**Login interactively with SSO/MFA:**

~~~powershell
python .\manual_to_ai.py --url "https://your-doc-site/manual/deal.html" --headed --login --profile-dir ".\private-login-profile" --crawl --out "D:\Exports\ASAP_Manual"
~~~

Do not commit browser profiles, login credentials, captured internal documentation, or sensitive exports. Only archive and share materials you are authorized to access.
