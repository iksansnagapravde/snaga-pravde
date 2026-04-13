# =========================================================
# full_import.py
# POČETNI FULL IMPORT SVIH UGOVORA OD 01.01.2026.
# Portal: https://jnportal.ujn.gov.rs/contract-eo/{id}
# =========================================================

import json
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# =========================================================
# CONFIG
# =========================================================

BASE_URL = "https://jnportal.ujn.gov.rs/contract-eo/"
START_DATE = datetime(2026, 1, 1)
EUR_RATE = 117.2

DB_FILE = "contracts_db.json"
STATS_FILE = "stats.json"
LOSS_FILE = "loss-data.json"
PROGRESS_FILE = "import_progress.json"
FAILED_FILE = "failed_ids.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# Početni i krajnji opseg za pretragu
START_ID = 1
END_ID = 5000000

# Koliko ID-jeva obrađuje po jednom batch-u
BATCH_SIZE = 500

# Koliko puta pokušava isti request
REQUEST_RETRIES = 3

# Pauza između request-ova
REQUEST_SLEEP = 0.15

# Pauza između batch-eva
BATCH_SLEEP = 1.0


# =========================================================
# FILE HELPERS
# =========================================================

def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_db():
    data = load_json_file(DB_FILE, [])
    return data if isinstance(data, list) else []


def save_db(data):
    save_json_file(DB_FILE, data)


def load_progress():
    data = load_json_file(PROGRESS_FILE, {})
    if not isinstance(data, dict):
        return {"next_id": START_ID}
    return {
        "next_id": int(data.get("next_id", START_ID))
    }


def save_progress(next_id):
    save_json_file(PROGRESS_FILE, {"next_id": next_id})


def load_failed_ids():
    data = load_json_file(FAILED_FILE, [])
    return data if isinstance(data, list) else []


def save_failed_ids(failed_ids):
    # unique + sorted
    clean = sorted(set(int(x) for x in failed_ids))
    save_json_file(FAILED_FILE, clean)


# =========================================================
# FORMAT HELPERS
# =========================================================

def format_int_rsd(value):
    return f"{round(value):,}".replace(",", ".") + " RSD"


def format_int_eur(value):
    return f"{round(value):,}".replace(",", ".") + " EUR"


def extract_money(text):
    if not text:
        return 0.0

    cleaned = text.replace(".", "").replace(",", ".")
    nums = re.findall(r"[\d]+(?:\.[\d]+)?", cleaned)

    if nums:
        try:
            return float(nums[0])
        except Exception:
            return 0.0

    return 0.0


def parse_date_from_text(text):
    patterns = [
        r"Датум закључења[:\s]+(\d{2}\.\d{2}\.\d{4})",
        r"Datum zaključenja[:\s]+(\d{2}\.\d{2}\.\d{4})",
        r"Закључен[:\s]+(\d{2}\.\d{2}\.\d{4})",
        r"Zaključen[:\s]+(\d{2}\.\d{2}\.\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return datetime.strptime(match.group(1), "%d.%m.%Y")
            except Exception:
                pass

    return None


def parse_amount_from_text(text):
    # prioritet: izmenjena vrednost sa PDV
    priority_patterns = [
        r"Уг\.?\s*вред\.?\s*са изменама\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност са изменама\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Ug\.?\s*vred\.?\s*sa izmenama\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost sa izmenama\s*\(sa PDV\)[:\s]+([\d\.,]+)",
    ]

    for pattern in priority_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return extract_money(match.group(1))

    # fallback: osnovna vrednost sa PDV
    fallback_patterns = [
        r"Уг\.?\s*вредност\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Ug\.?\s*vrednost\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност са ПДВ[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost sa PDV[:\s]+([\d\.,]+)",
    ]

    for pattern in fallback_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return extract_money(match.group(1))

    return 0.0


# =========================================================
# PARSER
# =========================================================

def parse_contract(contract_id):
    url = BASE_URL + str(contract_id)
    last_error = None

    for attempt in range(REQUEST_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                time.sleep(0.5)
                continue

            html = response.text

            not_found_markers = [
                "Нема података",
                "Уговор није пронађен",
                "Not Found",
            ]
            if any(marker in html for marker in not_found_markers):
                return None, False

            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(" ", strip=True)

            contract_date = parse_date_from_text(text)
            if not contract_date:
                # stranica postoji ali nije ugovor koji možemo obraditi
                return None, False

            if contract_date < START_DATE:
                # postoji, ali nije u periodu
                return None, False

            amount = parse_amount_from_text(text)

            item = {
                "id": contract_id,
                "url": url,
                "date": contract_date.strftime("%d.%m.%Y"),
                "date_iso": contract_date.strftime("%Y-%m-%d"),
                "amount_rsd": round(amount),
                "amount_eur": round(amount / EUR_RATE),
            }
            return item, False

        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.75)

    print(f"FAILED {contract_id}: {last_error}")
    return None, True


# =========================================================
# OUTPUT BUILDERS
# =========================================================

def build_loss_data(values):
    if not values:
        return {
            "najbolja_ponuda": 0,
            "najbolja_ponuda_eur": 0,
            "srednja_ponuda": 0,
            "srednja_ponuda_eur": 0,
            "prihvacena_ponuda": 0,
            "prihvacena_ponuda_eur": 0,
            "broj_analiziranih": 0,
            "gubitak_prema_najboljoj": 0,
            "gubitak_prema_najboljoj_eur": 0,
            "gubitak_prema_srednjoj": 0,
            "gubitak_prema_srednjoj_eur": 0,
            "valuta_kurs_eur": EUR_RATE,
            "period_od": "2026-01-01"
        }

    values_sorted = sorted(values)

    najbolja = values_sorted[0]
    srednja = values_sorted[len(values_sorted) // 2]
    prihvacena = values_sorted[-1]

    loss_best = prihvacena - najbolja
    loss_mid = prihvacena - srednja

    return {
        "najbolja_ponuda": round(najbolja),
        "najbolja_ponuda_eur": round(najbolja / EUR_RATE),
        "srednja_ponuda": round(srednja),
        "srednja_ponuda_eur": round(srednja / EUR_RATE),
        "prihvacena_ponuda": round(prihvacena),
        "prihvacena_ponuda_eur": round(prihvacena / EUR_RATE),
        "broj_analiziranih": len(values_sorted),
        "gubitak_prema_najboljoj": round(loss_best),
        "gubitak_prema_najboljoj_eur": round(loss_best / EUR_RATE),
        "gubitak_prema_srednjoj": round(loss_mid),
        "gubitak_prema_srednjoj_eur": round(loss_mid / EUR_RATE),
        "valuta_kurs_eur": EUR_RATE,
        "period_od": "2026-01-01"
    }


def write_outputs(db):
    total_contracts = len(db)
    total_value_rsd = sum(item.get("amount_rsd", 0) for item in db)
    total_value_eur = round(total_value_rsd / EUR_RATE)

    stats = {
        "broj_tendera": total_contracts,
        "ukupna_vrednost": format_int_rsd(total_value_rsd),
        "ukupna_vrednost_eur": format_int_eur(total_value_eur),
        "broj_ugovora": total_contracts,
        "ugovorena_vrednost": format_int_rsd(total_value_rsd),
        "ugovorena_vrednost_eur": format_int_eur(total_value_eur)
    }

    values = [item.get("amount_rsd", 0) for item in db if item.get("amount_rsd", 0) > 0]
    loss_data = build_loss_data(values)

    save_json_file(STATS_FILE, stats)
    save_json_file(LOSS_FILE, loss_data)


# =========================================================
# MAIN
# =========================================================

def main():
    db = load_db()
    known_ids = {item["id"] for item in db if isinstance(item, dict) and "id" in item}

    progress = load_progress()
    next_id = progress["next_id"]

    failed_ids = load_failed_ids()

    if next_id < START_ID:
        next_id = START_ID

    batch_start = next_id
    batch_end = min(batch_start + BATCH_SIZE - 1, END_ID)

    print("====================================")
    print(f"FULL IMPORT BATCH: {batch_start} -> {batch_end}")
    print(f"Known contracts in DB: {len(db)}")
    print("====================================")

    new_items = []
    new_failed = []

    for contract_id in range(batch_start, batch_end + 1):
        if contract_id in known_ids:
            continue

        item, failed = parse_contract(contract_id)

        if item:
            new_items.append(item)
            known_ids.add(contract_id)
            print(
                f"OK {contract_id} | {item['date']} | "
                f"{item['amount_rsd']} RSD"
            )
        elif failed:
            new_failed.append(contract_id)

        time.sleep(REQUEST_SLEEP)

    if new_items:
        db.extend(new_items)
        db.sort(key=lambda x: x.get("id", 0))
        save_db(db)

    failed_ids.extend(new_failed)
    save_failed_ids(failed_ids)

    write_outputs(db)

    next_batch_start = batch_end + 1
    if next_batch_start > END_ID:
        next_batch_start = END_ID

    save_progress(next_batch_start)

    print("====================================")
    print("BATCH DONE")
    print("New contracts:", len(new_items))
    print("Failed IDs in this batch:", len(new_failed))
    print("Total contracts in DB:", len(db))
    print("Next batch starts from:", next_batch_start)
    print("====================================")

    time.sleep(BATCH_SLEEP)


if __name__ == "__main__":
    main()
