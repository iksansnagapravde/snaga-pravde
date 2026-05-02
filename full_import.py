import os
import re
import json
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright

BASE_URL = "https://jnportal.ujn.gov.rs"
os.makedirs("documents", exist_ok=True)

# =========================
# TEST IDS
# =========================
def fetch_entity_ids():
    return [675152, 670413, 666041]

# =========================
# DOWNLOAD PREKO BROWSER-A
# =========================
def download_document(eid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")

            # čekaj da se učita
            page.wait_for_timeout(5000)

            # 🔴 PRONAĐI DOWNLOAD DUGME (STRELICA)
            # uzimamo sve linkove koji imaju download
            links = page.locator("a[href*='GetDocuments']").all()

            for link in links:
                href = link.get_attribute("href") or ""

                if str(eid) in href:
                    with page.expect_download() as download_info:
                        link.click()

                    download = download_info.value

                    path = f"documents/{eid}_{download.suggested_filename}"
                    download.save_as(path)

                    print("DOWNLOADED:", path)

                    browser.close()

                    # detekcija tipa
                    with open(path, "rb") as f:
                        head = f.read(200)

                    if head.startswith(b"%PDF"):
                        return path, "pdf"
                    elif b"<?xml" in head:
                        return path, "xml"
                    else:
                        return path, "unknown"

            print("❌ NIJE NAĐEN LINK ZA ID")
            browser.close()
            return None, None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None, None

# =========================
# XML PARSER
# =========================
def parse_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()

        text = ET.tostring(root, encoding="unicode")

        prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
        prices = [float(p.replace(".", "").replace(",", ".")) for p in prices]
        prices = sorted(set(prices))

        if not prices:
            return None

        lowest = min(prices)
        accepted = max(prices)
        second = prices[1] if len(prices) > 1 else None

        return {
            "accepted": accepted,
            "lowest": lowest,
            "second": second,
            "difference": accepted - lowest,
            "suspicious": accepted > lowest
        }

    except Exception as e:
        print("XML ERROR:", e)
        return None

# =========================
# MAIN
# =========================
def main():
    ids = fetch_entity_ids()
    results = []

    for eid in ids:
        print("\nPROCESS:", eid)

        path, ext = download_document(eid)

        if not path:
            continue

        if ext == "xml":
            data = parse_xml(path)
        else:
            data = {
                "note": "PDF - parser ide kasnije"
            }

        if data:
            results.append({
                "id": eid,
                **data
            })

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nDONE")

if __name__ == "__main__":
    main()
