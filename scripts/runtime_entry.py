from runtime.main import main
import sys


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test-browser"]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("data:text/html,<title>KeplerV runtime ready</title>")
            assert page.title() == "KeplerV runtime ready"
            browser.close()
    else:
        main()
