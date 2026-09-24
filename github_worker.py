"""
GitHub Actions Bağımsız Worker Scripti.
Her bir GitHub reposunda bağımsız olarak çalışır, harici aramaları yapar,
siteleri Scrapling / Async motorla tarar ve sonuçları doğrudan ortak MongoDB Atlas'a kaydeder.
"""
import os
import sys
import asyncio
import pandas as pd
from datetime import datetime

# Ortam değişkenleri
MONGO_URI = os.getenv("MONGO_URI", "")
TARGET_KEYWORD = os.getenv("TARGET_KEYWORD", "Tekstil Toptan")
TARGET_REGION = os.getenv("TARGET_REGION", "İstanbul")
WORKER_ID = os.getenv("WORKER_ID", "github-worker")

print(f"🚀 [Worker Başlatıldı] Kimlik: {WORKER_ID} | Hedef: '{TARGET_KEYWORD}' in '{TARGET_REGION}'")

from mongo_manager import MongoLeadManager
from discovery.searcher import UnifiedDiscoverySearcher
from crawler.async_crawler import AsyncLeadCrawler

async def run_worker():
    mongo = MongoLeadManager(MONGO_URI)
    if not mongo.is_connected():
        print("⚠️ UYARI: MongoDB bağlantısı kurulamadı. URI ve Network Access kontrol edilmeli.")
    else:
        print("✓ MongoDB Atlas bağlantısı doğrulandı!")

    # 1. Arama Yap
    searcher = UnifiedDiscoverySearcher()
    print(f"🔍 Firmalar taranıyor: {TARGET_KEYWORD} ({TARGET_REGION})...")
    discovered_leads = searcher.search_all(query=TARGET_KEYWORD, location=TARGET_REGION, max_results=50)
    print(f"✓ {len(discovered_leads)} potansiyel firma tespit edildi.")

    if not discovered_leads:
        print("Firma bulunamadı, worker tamamlandı.")
        return

    # 2. Siteleri Kazı ve E-posta/Telefon/Sosyal Medya Çıkar
    crawler = AsyncLeadCrawler()
    print("🌐 Derin web taraması ve iletişim bilgisi çıkarma başlatılıyor...")
    scraped_leads = await crawler.crawl_leads(discovered_leads)
    print(f"✓ {len(scraped_leads)} firma başarıyla analiz edildi.")

    # 3. MongoDB'ye Kaydet
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_leads = []
    for lead in scraped_leads:
        d = lead.to_dict() if hasattr(lead, "to_dict") else dict(lead)
        d["Scrape_Tarihi"] = now_str
        d["Bolge"] = TARGET_REGION
        formatted_leads.append(d)

    if mongo.is_connected():
        saved = mongo.insert_or_update_leads(formatted_leads, worker_id=WORKER_ID)
        print(f"🎉 BAŞARILI: {saved} adet firma doğrudan merkezi MongoDB Atlas kümesine aktarıldı!")
    else:
        # Fallback CSV kaydet (GitHub Artifact için)
        out_csv = f"output_{WORKER_ID}.csv"
        pd.DataFrame(formatted_leads).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"📁 MongoDB bağlı olmadığı için CSV'ye yazıldı: {out_csv}")

if __name__ == "__main__":
    asyncio.run(run_worker())
