import os
import re
import time
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

os.makedirs("documents", exist_ok=True)


# =========================
# GLAVNA FUNKCIJA
# =========================
def process_from_list():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        print("OPEN LIST PAGE")

        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(6)

        rows = driver.find_elements(By.XPATH, "//tr")

        print("TOTAL ROWS:", len(rows))

        counter = 0

        for row in rows:

            if counter >= 10:
                break

            try:
                # 🔥 NAĐI STRELICU U REDU
                expand = row.find_element(
                    By.XPATH,
                    ".//mat-icon[contains(text(),'expand_more') or contains(text(),'keyboard_arrow_down')]"
                )

                driver.execute_script("arguments[0].click();", expand)

                print("\nEXPANDED ROW:", counter)

                time.sleep(2)

            except:
                continue

            # 🔥 TRAŽI DOKUMENTE U TOM REDU
            links = row.find_elements(
                By.XPATH,
                ".//a[contains(@href,'.pdf') or contains(@href,'.doc')]"
            )

            if not links:
                print("NO DOCUMENT IN ROW")
                continue

            for l in links:
                href = l.get_attribute("href")

                if not href:
                    continue

                print("FOUND DOC:", href)

                try:
                    r = requests.get(href, headers=HEADERS)

                    if r.status_code != 200:
                        continue

                    filename = f"documents/tender_{counter}"

                    if ".pdf" in href:
                        filename += ".pdf"
                    else:
                        filename += ".docx"

                    with open(filename, "wb") as f:
                        f.write(r.content)

                    print("SAVED:", filename)

                except Exception as e:
                    print("DOWNLOAD ERROR:", e)

            counter += 1

        print("\nDONE PROCESSING")

    finally:
        driver.quit()


# =========================
# MAIN
# =========================
def main():
    process_from_list()


if __name__ == "__main__":
    main()
