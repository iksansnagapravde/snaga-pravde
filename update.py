# =========================================================
# update.py
# FINALNI KOD
# AUTOMATSKO PRAĆENJE JAVNIH NABAVKI SRBIJE
# Portal: https://jnportal.ujn.gov.rs
# Period: 01.01.2026 → danas
# Ažuriranje: svakih 15 minuta preko GitHub Actions
# =========================================================

import json
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# =========================================================
# CONFIG
# =========================================================

BASE_URL = "https://jnportal.ujn.gov.rs/contract-eo/"
START_DATE = datetime(2026, 1, 1)
EUR_RATE = 117.2

LAST_ID_FILE = "last_id.txt"
DB_FILE = "contracts_db.json"
STATS_FILE = "stats.json"
LOSS_FILE = "loss-data.json"

# koliko novih ID-jeva proverava po jednom pokretanju
SCAN_BATCH = 3000

# gornja sigurnosna granica
MAX_ID_LIMIT = 5000000

# odakle kreće prvi put ako nema last_id.txt
DEFAULT_START_ID = 1000000

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# =========================================================
# HELPERS
# =========================================================

def load_last_id():
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return DEFAULT_START_ID


def save_last_id(last_id):
    with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
        f.write(str(last_id))


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return data
            except Exception:
                pass
    return []


def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    # 1. prioritet: ugovorena vrednost SA IZMENAМА i SA PDV
    patterns_priority = [
        r"Уг\.?\s*вред\.?\s*са изменама\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност са изменама\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Ug\.?\s*vred\.?\s*sa izmenama\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost sa izmenama\s*\(sa PDV\)[:\s]+([\d\.,]+)",
    ]

    for pattern in patterns_priority:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return extract_money(match.group(1))

    # 2. fallback: osnovna ugovorena vrednost SA PDV
    patterns_fallback = [
        r"Уг\.?\s*вредност\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност\s*\(са ПДВ\)[:\s]+([\d\.,]+)",
        r"Ug\.?\s*vrednost\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost\s*\(sa PDV\)[:\s]+([\d\.,]+)",
        r"Уговорена вредност са ПДВ[:\s]+([\d\.,]+)",
        r"Ugovorena vrednost sa PDV[:\s]+([\d\.,]+)",
    ]

    for pattern in patterns_fallback:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return extract_money(match.group(1))

    return 0.0


# =========================================================
# CONTRACT PARSER
# =========================================================

def parse_contract(contract_id):
    url = BASE_URL + str(contract_id)

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)

        if response.status_code != 200:
            return None

        html = response.text

        not_found_markers = [
            "Нема података",
            "Уговор није пронађен",
            "Not Found",
        ]
        if any(marker in html for marker in not_found_markers):
            return None

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        contract_date = parse_date_from_text(text)
        if not contract_date:
            return None

        if contract_date < START_DATE:
            return None

        amount = parse_amount_from_text(text)

        return {
            "id": contract_id,
            "url": url,
            "date": contract_date.strftime("%d.%m.%Y"),
            "date_iso": contract_date.strftime("%Y-%m-%d"),
            "amount_rsd": round(amount),
            "amount_eur": round(amount / EUR_RATE),
        }

    except Exception:
        return None


# =========================================================
# LOSS CALCULATOR
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


# =========================================================
# MAIN
# =========================================================

def main():
    start_id = load_last_id()
    end_id = min(start_id + SCAN_BATCH, MAX_ID_LIMIT)

    print(f"Scanning contracts from {start_id} to {end_id}")

    # učitaj staru bazu
    db = load_db()
    known_ids = {item["id"] for item in db if isinstance(item, dict) and "id" in item}

    new_items = []

    for contract_id in range(start_id, end_id):
        if contract_id in known_ids:
            continue

        data = parse_contract(contract_id)
        if data:
            new_items.append(data)
            print(f"OK {contract_id}: {data['amount_rsd']} RSD | {data['date']}")

    # dodaj samo nove
    if new_items:
        db.extend(new_items)

    # sortiraj po ID radi reda
    db.sort(key=lambda x: x.get("id", 0))

    # snimi bazu svih ugovora od 01.01.2026.
    save_db(db)

    # ukupne vrednosti iz cele baze, ne samo iz poslednjeg batch-a
    total_contracts = len(db)
    total_value_rsd = sum(item.get("amount_rsd", 0) for item in db)
    total_value_eur = round(total_value_rsd / EUR_RATE)

    # stats.json
    stats = {
        "broj_tendera": total_contracts,
        "ukupna_vrednost": format_int_rsd(total_value_rsd),
        "ukupna_vrednost_eur": format_int_eur(total_value_eur),
        "broj_ugovora": total_contracts,
        "ugovorena_vrednost": format_int_rsd(total_value_rsd),
        "ugovorena_vrednost_eur": format_int_eur(total_value_eur)
    }

    # loss-data.json
    all_values = [item.get("amount_rsd", 0) for item in db if item.get("amount_rsd", 0) > 0]
    loss_data = build_loss_data(all_values)

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    with open(LOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(loss_data, f, ensure_ascii=False, indent=2)

    save_last_id(end_id)

    print("====================================")
    print("DONE")
    print("New contracts found:", len(new_items))
    print("All contracts in DB:", total_contracts)
    print("Total RSD:", total_value_rsd)
    print("Total EUR:", total_value_eur)
    print("Saved files:", STATS_FILE, LOSS_FILE, DB_FILE, LAST_ID_FILE)
    print("Next start ID:", end_id)
    print("====================================")


if __name__ == "__main__":
    main()
