"""
Bulut Senkronizasyon Scripti:
Yerelden veya dosyalardan gelen lead'leri doğrudan MongoDB Atlas'a yazar.
"""
import os
import json
from pymongo import MongoClient, UpdateOne

MONGO_URI = os.getenv("MONGO_URI", "")
DATA_FILE = os.getenv("SYNC_DATA_FILE", "sync_payload.json")

def main():
    if not os.path.exists(DATA_FILE):
        print(f"Hata: {DATA_FILE} bulunamadi.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        leads = json.load(f)

    if not leads:
        print("Yuklenecek lead yok.")
        return

    print(f"Toplam {len(leads)} lead MongoDB Atlas'a aktariliyor...")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client["b2b_scraper"]
    col = db["leads"]

    ops = []
    for l in leads:
        # NaN temizliği
        clean_lead = {}
        for k, v in l.items():
            if v is None or (isinstance(v, float) and str(v) == "nan"):
                clean_lead[k] = ""
            else:
                clean_lead[k] = v

        clean_lead["_worker"] = "local-pc-synced"
        web = str(clean_lead.get("Web Sitesi") or "").strip()
        if web.lower() == "nan": web = ""
        name = str(clean_lead.get("Firma Adı") or "").strip()
        if name.lower() == "nan": name = ""

        if not web and not name:
            continue

        fil = {"Web Sitesi": web} if (web and len(web) > 4) else {"Firma Adı": name}
        ops.append(UpdateOne(fil, {"$set": clean_lead}, upsert=True))

    if ops:
        res = col.bulk_write(ops, ordered=False)
        total = (res.upserted_count or 0) + (res.modified_count or 0)
        print(f"🎉 BAŞARILI: {total} firma basariyla MongoDB Atlas havuzuna yazildi!")
        print(f"📊 Havuzdaki Guncel Toplam: {col.count_documents({})} adet firma.")

if __name__ == "__main__":
    main()
