from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # Persistent context = real profile, cookies survive across runs
    ctx = p.chromium.launch_persistent_context(
        "D:/sessssion/browser_profile",
        headless=False,
        viewport={"width": 1366, "height": 768},
    )
    page = ctx.new_page()
    page.goto("https://www.rozee.pk/job/jsearch/q/data", timeout=60000)

    print(">>> If a Cloudflare checkbox appears, CLICK IT. Waiting 60s...")
    page.wait_for_timeout(60000)

    html = page.content()
    if "Just a moment" in html:
        print("RESULT: still on challenge page after 60s + manual click")
    else:
        with open("sample_listing_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"RESULT: PASSED. {len(html)} chars saved to sample_listing_page.html")
    ctx.close()