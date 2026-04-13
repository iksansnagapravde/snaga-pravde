import json

stats = {
    "broj_tendera": 18452,
    "ukupna_vrednost": "128.945.000.000 RSD",
    "broj_ugovora": 9674,
    "ugovorena_vrednost": "96.320.000.000 RSD"
}

with open("stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)
