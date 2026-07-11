from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0]  # the tab you already opened

    print("Connected. Current URL:", page.url)
    html = page.content()

    if "Just a moment" in html:
        print("RESULT: still challenged")
    else:
        with open("rozee_sample.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"RESULT: PASSED via CDP. {len(html)} chars saved.")
        # quick sanity check: are job titles actually in there?
        import re
        titles = re.findall(r'jobTitle|job-title|jsTitle', html)
        print(f"Title-like markers found: {len(titles)}")