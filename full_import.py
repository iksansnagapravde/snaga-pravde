import os
import re
import json
from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

BASE_URL = "https://jnportal.ujn.gov.rs"

# =========================
# FETCH IDS
# =========================
def fetch_entity_ids():
    ids = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("tr")

        rows = page.locator("tr").all()

        for row in rows:
            match = re.search(r"\d{6,}", row.inner_text())
            if match:
                ids.append(int(match.group()))

        browser.close()

    return list(dict.fromkeys(ids))[:10]

# =========================
# DOWNLOAD + OCR
# =========================
def read_pdf_from_portal(eid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
            page.wait_for_load_state("networkidle")

            rows = page.locator("tr").all()

            for row in rows:
                if str(eid) in row.inner_text():
                    button = row.locator("a, button").first

                    with page.expect_download() as download_info:
                        button.click()

                    download = download_info.value
                    path = f"{eid}.pdf"
                    download.save_as(path)

                    browser.close()

                    images = convert_from_path(path, dpi=300)
                    text = ""

                    for img in images:
                        text += pytesseract.image_to_string(img, lang="srp+eng")

                    return text

        return ""

    except Exception as e:
        print("ERROR:", e)
        return ""

# =========================
# AI PARSING
# =========================
def analyze(text):
    try:
        prompt = f"""
Izvuci sve ponude iz dokumenta javne nabavke.

Vrati JSON:

[
  {{
    "firma": "",
    "cena": 0,
    "pobednik": true/false
  }}
]

TEKST:
{text[:12000]}
"""

        res = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        return json.loads(res["choices"][0]["message"]["content"])

    except Exception as e:
        print("AI ERROR:", e)
        return []

# =========================
# MAIN LOGIC
# =========================
def main():
    total_value = 0
    total_loss = 0
    count = 0

    for eid in fetch_entity_ids():
        print("PROCESS:", eid)

        text = read_pdf_from_portal(eid)
        if not text:
            continue

        data = analyze(text)
        if not data:
            continue

        if len(data) == 1:
            continue  # jedan ponuđač

        winner = next((x for x in data if x["pobednik"]), None)
        lowest = min(data, key=lambda x: x["cena"])

        if winner and winner["cena"] > lowest["cena"]:
            diff = winner["cena"] - lowest["cena"]
            total_loss += diff
            total_value += winner["cena"]
            count += 1

    # =========================
    # STATS
    # =========================
    stats = {
        "broj_tendera": count,
        "ukupna_vrednost": f"{int(total_value)} RSD",
        "ukupna_vrednost_eur": f"{int(total_value/117.2)} EUR",
        "broj_ugovora": count,
        "ugovorena_vrednost": f"{int(total_value)} RSD",
        "ugovorena_vrednost_eur": f"{int(total_value/117.2)} EUR"
    }

    loss = {
        "broj_analiziranih": count,
        "gubitak_prema_najboljoj": int(total_loss),
        "gubitak_prema_najboljoj_eur": int(total_loss/117.2),
        "period_od": "2026-01-01"
    }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    with open("loss-data.json", "w") as f:
        json.dump(loss, f, indent=2)

    print("DONE")

if __name__ == "__main__":
    main()
