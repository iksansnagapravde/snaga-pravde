# full_import.py
# Selenium verzija — rešava HTTP 500 blokadu

import json
import os
import re
import sqlite3
import time
from datetime import datetime

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://jnportal.ujn.gov.rs/contract-eo/"
START_ID = 340000
BATCH_SIZE = 20
START_DATE = datetime(2026, 1, 1)

DB_FILE = "contracts.db"
LAST_ID_FILE = "last_id.txt"
STATS_FILE = "stats.json"
LOSS_FILE = "loss-data.json"

EUR_RATE = 117.2


# =====================================================
# LAST ID
# =====================================================

def load_last_id():
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    return START_ID


def save_last_id(last_id):
    with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
        f.write(str(last_id))


# =====================================================
# DB
# =====================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY,
        contract_id INTEGER UNIQUE,
        date TEXT,
        amount REAL
    )
    """)

    conn.commit()
    conn.close()


def save_contract(contract_id, date_str, amount):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR IGNORE INTO contracts
    (contract_id, date, amount)
    VALUES (?, ?, ?)
    """, (contract_id, date_str, amount))

    conn.commit()
    conn.close()


# =====================================================
# SELENIUM DRIVER
# =====================================================

def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=chrome_options)
    return driver


# =====================================================
# MONEY PARSER
# =====================================================

def extract_money(text):
    if not text:
        return 0

    text = text.replace(".", "").replace(",", ".")
    nums = re.findall(r"[\d\.]+", text)

    if nums:
        return float(nums[0])

    return 0


# =====================================================
# PARSE CONTRACT
# =====================================================

def parse_contract(driver, contract_id):
    url = BASE_URL + str(contract_id)

    try:
        driver.get(url)
        time.sleep(2)

        html = driver.page_source

        if "Нема података" in html:
            print(f"NOT FOUND {contract_id}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        date_match = re.search(
            r"Датум закључења[:\s]+(\d{2}\.\d{2}\.\d{4})",
            text
        )

        if not date_match:
            print(f"NO DATE {contract_id}")
            return None

        contract_date = datetime.strptime(
            date_match.group(1),
            "%d.%m.%Y"
        )

        if contract_date < START_DATE:
            print(f"OLD {contract_id}")
            return None

        vat_match = re.search(
            r"Уговорена вредност са ПДВ[:\s]+([\d\.\,]+)",
            text
        )

        amount = 0
        if vat_match:
            amount = extract_money(vat_match.group(1))

        print(f"OK {contract_id}: {amount}")

        return {
            "id": contract_id,
            "date": contract_date.strftime("%d.%m.%Y"),
            "amount": amount
        }

    except Exception as e:
        print(f"FAILED {contract_id}: {e}")
        return None


# =====================================================
# STATS
# =====================================================

def update_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT COUNT(*), SUM(amount) FROM contracts")
    row = c.fetchone()

    total_contracts = row[0] or 0
    total_value = row[1] or 0

    c.execute("SELECT amount FROM contracts ORDER BY amount ASC")
    values = [x[0] for x in c.fetchall()]

    conn.close()

    total_value_eur = round(total_value / EUR_RATE)

    if values:
        najbolja = min(values)
        srednja = values[len(values)//2]
        prihvacena = max(values)
    else:
        najbolja = srednja = prihvacena = 0

    loss_best = prihvacena - najbolja
    loss_mid = prihvacena - srednja

    stats = {
        "broj_tendera": total_contracts,
        "ukupna_vrednost":
            f"{round(total_value):,}".replace(",", ".") + " RSD",
        "ukupna_vrednost_eur":
            f"{total_value_eur:,}".replace(",", ".") + " EUR",
        "broj_ugovora": total_contracts,
        "ugovorena_vrednost":
            f"{round(total_value):,}".replace(",", ".") + " RSD",
        "ugovorena_vrednost_eur":
            f"{total_value_eur:,}".replace(",", ".") + " EUR"
    }

    loss_data = {
        "najbolja_ponuda": round(najbolja),
        "srednja_ponuda": round(srednja),
        "prihvacena_ponuda": round(prihvacena),
        "broj_analiziranih": total_contracts,
        "gubitak_prema_najboljoj": round(loss_best),
        "gubitak_prema_srednjoj": round(loss_mid)
    }

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    with open(LOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(loss_data, f, ensure_ascii=False, indent=2)


# =====================================================
# MAIN
# =====================================================

def main():
    init_db()

    start_id = load_last_id()
    end_id = start_id + BATCH_SIZE

    print("="*40)
    print(f"FULL IMPORT BATCH: {start_id} -> {end_id}")
    print("="*40)

    driver = create_driver()

    for contract_id in range(start_id, end_id):
        data = parse_contract(driver, contract_id)

        if data:
            save_contract(
                data["id"],
                data["date"],
                data["amount"]
            )

    driver.quit()

    save_last_id(end_id)
    update_stats()

    print("DONE")


if __name__ == "__main__":
    main()
