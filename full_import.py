import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# =========================
# PODEŠAVANJE
# =========================
DOWNLOAD_DIR = os.path.abspath("documents")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================
# ČEKANJE DOWNLOADA
# =========================
def wait_for_download(timeout=20):
    start = time.time()

    while True:
        files = os.listdir(DOWNLOAD_DIR)

        # ignoriši .crdownload (Chrome još skida)
        ready_files = [f for f in files if not f.endswith(".crdownload")]

        if ready_files:
            # uzmi najnoviji fajl
            ready_files.sort(key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)))
            return ready_files[-1]

        if time.time() - start > timeout:
            return None

        time.sleep(1)


# =========================
# GLAVNA LOGIKA
# =========================
def process_from_list():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # 🔥 KRITIČNO: automatski download bez popup-a
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=chrome_options)

    try:
        print("OPEN LIST PAGE")

        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(6)

        rows = driver.find_elements(By.XPATH, "//div[contains(@class,'mat-row')]")

        print("ROWS FOUND:", len(rows))

        for i, row in enumerate(rows[:5]):  # prvih 5 za test

            print("\nROW:", i)

            try:
                # 🔥 PRONAĐI DOWNLOAD/STRELICU DUGME
                button = row.find_element(By.XPATH, ".//button")
                driver.execute_script("arguments[0].click();", button)

                print("CLICKED DOWNLOAD")

            except Exception as e:
                print("NO BUTTON:", e)
                continue

            # 🔥 ČEKAJ DOWNLOAD
            downloaded_file = wait_for_download()

            if not downloaded_file:
                print("DOWNLOAD FAILED")
                continue

            print("DOWNLOADED:", downloaded_file)

        print("\nDONE")

    finally:
        driver.quit()


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    process_from_list()
