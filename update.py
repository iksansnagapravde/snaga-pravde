import json

kurs_eur = 117.2

broj_tendera = 18452
ukupna_vrednost_rsd = 128945000000

broj_ugovora = 9674
ugovorena_vrednost_rsd = 96320000000

ukupna_vrednost_eur = round(ukupna_vrednost_rsd / kurs_eur)
ugovorena_vrednost_eur = round(ugovorena_vrednost_rsd / kurs_eur)

def format_broj(x):
    return f"{x:,.0f}".replace(",", ".")

stats = {
    "broj_tendera": broj_tendera,
    "ukupna_vrednost": format_broj(ukupna_vrednost_rsd) + " RSD",
    "ukupna_vrednost_eur": format_broj(ukupna_vrednost_eur) + " EUR",
    "broj_ugovora": broj_ugovora,
    "ugovorena_vrednost": format_broj(ugovorena_vrednost_rsd) + " RSD",
    "ugovorena_vrednost_eur": format_broj(ugovorena_vrednost_eur) + " EUR"
}

with open("stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)
