import requests
import json
import os
from datetime import datetime

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
}

SAVE_FOLDER = "documents"
os.makedirs(SAVE_FOLDER, exist_ok=True)


# =========================================
# 1. UZIMANJE LISTE TENDERA
# =========================================
def fetch_tenders():
    print("FETCHING TENDERS...")

    url = BASE_URL + "/get-documents"

    params = {
        "page": 1,
        "pageSize": 20
    }

    r = requests.get(url, headers=HEADERS, params=params)

    if r.status_code != 200:
        print("ERROR fetching tenders")
        return []

    data = r.json()
    return data


# =========================================
# 2. DOWNLOAD DOKUMENTA
# =========================================
def download_document(entity_id):
    url = f"{BASE_URL}/GetDocuments.ashx?entityId={entity_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)

        if r.status_code == 200 and len(r.content) > 2000:
            filename = os.path.join(SAVE_FOLDER, f"{entity_id}.pdf")

            with open(filename, "wb") as f:
                f.write(r.content)

            print(f"SAVED: {filename}")
            return True

        else:
            print(f"FAILED: {entity_id}")
            return False

    except Exception as e:
        print("ERROR:", e)
        return False


# =========================================
# 3. GLAVNA LOGIKA
# =========================================
def main():
    tenders = fetch_tenders()

    if not tenders:
        print("NO DATA")
        return

    print(f"FOUND: {len(tenders)}")

    count = 0

    for item in tenders:
        entity_id = item.get("LotId") or item.get("EntityId")

        if not entity_id:
            continue

        print(f"DOWNLOADING: {entity_id}")

        success = download_document(entity_id)

        if success:
            count += 1

    print("====================================")
    print(f"TOTAL DOWNLOADED: {count}")


if __name__ == "__main__":
    main()
