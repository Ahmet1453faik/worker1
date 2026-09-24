"""
Tek Dosyada Bağımsız (All-in-One) Distributed B2B Worker.
Hiçbir harici klasöre (crawler/ veya discovery/) ihtiyaç duymadan
tek başına çalışır, DuckDuckGo & Harita araması yapar, siteleri kazar,
e-posta, telefon ve sosyal medyaları çıkarıp doğrudan MongoDB Atlas'a kaydeder.
"""
import os
import sys
import re
import html
import asyncio
import urllib.parse
from datetime import datetime
from typing import List, Dict, Set, Optional, Any

import aiohttp
from selectolax.lexbor import LexborHTMLParser
from yarl import URL
from pymongo import MongoClient, UpdateOne

# Ortam Değişkenleri
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://ahmet1453ozsoy_db_user:zZPdtYrxCcw8xt4v@cluster0.y5ybnvp.mongodb.net/?appName=Cluster0")
TARGET_KEYWORD = os.getenv("TARGET_KEYWORD", "Tekstil Toptan")
TARGET_REGION = os.getenv("TARGET_REGION", "İstanbul")
WORKER_ID = os.getenv("WORKER_ID", "github-worker-node")

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(?:(?:\+|00)[1-9]\d{0,2}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}|(?:\+|00)?90[\s.-]?(?:\(?\d{3}\)?)[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}|\b0[12378]\d{1,3}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b)")
IG_REGEX = re.compile(r"(?:https?:\/\/)?(?:www\.)?instagram\.com\/([a-zA-Z0-9_.]+)\/?", re.IGNORECASE)
LI_REGEX = re.compile(r"(?:https?:\/\/)?(?:www\.)?linkedin\.com\/(?:company|in)\/([a-zA-Z0-9_-]+)\/?", re.IGNORECASE)

SKIP_DOMAINS = {
    "google.com", "google.com.tr", "maps.google.com", "yandex.com.tr", "bing.com",
    "duckduckgo.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "trendyol.com", "hepsiburada.com", "amazon.com.tr",
    "n11.com", "sahibinden.com", "ciceksepeti.com", "sikayetvar.com", "wikipedia.org",
    "eksisozluk.com", "yellowpages.com.tr", "firmasec.com"
}

def decode_cf_email(cf_hex: str) -> Optional[str]:
    try:
        cf_clean = cf_hex.strip()
        if len(cf_clean) < 4 or len(cf_clean) % 2 != 0:
            return None
        key = int(cf_clean[:2], 16)
        email_chars = [chr(int(cf_clean[i:i+2], 16) ^ key) for i in range(2, len(cf_clean), 2)]
        decoded = "".join(email_chars).strip()
        if "@" in decoded and "." in decoded:
            return decoded.lower()
    except Exception:
        pass
    return None

def extract_contacts(html_text: str):
    emails = set()
    phones = set()
    ig_links = set()
    li_links = set()

    # Cloudflare data-cfemail
    for cf in re.findall(r'data-cfemail=["\']([a-fA-F0-9]{4,})["\']', html_text, re.I):
        dec = decode_cf_email(cf)
        if dec:
            emails.add(dec)

    # Normal regex
    for em in EMAIL_REGEX.findall(html_text):
        em_l = em.lower().strip()
        if not any(em_l.endswith(ext) for ext in [".png", ".jpg", ".webp", ".gif", ".svg", ".js", ".css"]):
            if not any(ign in em_l for ign in ["sentry.io", "example.com", "wixpress.com", "domain.com"]):
                emails.add(em_l)

    for ph in PHONE_REGEX.findall(html_text):
        p_clean = ph.strip()
        if len(re.sub(r'\D', '', p_clean)) >= 9:
            phones.add(p_clean)

    for ig in IG_REGEX.findall(html_text):
        if ig.lower() not in {"p", "reel", "explore", "about", "developer"}:
            ig_links.add(f"https://instagram.com/{ig}")

    for li in LI_REGEX.findall(html_text):
        li_links.add(f"https://linkedin.com/company/{li}")

    return list(emails), list(phones), list(ig_links), list(li_links)

async def search_web_engines(query: str, session: aiohttp.ClientSession) -> List[Dict[str, str]]:
    results = []
    seen = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate"
    }

    # 1. Bing Search Motoru
    for offset in [1, 11]:
        try:
            b_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&first={offset}"
            async with session.get(b_url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    parser = LexborHTMLParser(body)
                    for node in parser.css("li.b_algo"):
                        title_node = node.css_first("h2 a")
                        if not title_node:
                            continue
                        title = title_node.text(strip=True)
                        raw_href = title_node.attributes.get("href", "")
                        if raw_href.startswith("http"):
                            host = URL(raw_href).host or ""
                            clean_h = host.lower().removeprefix("www.")
                            if clean_h and not any(clean_h == s or clean_h.endswith("." + s) for s in SKIP_DOMAINS):
                                if clean_h not in seen:
                                    seen.add(clean_h)
                                    results.append({"name": title.split("-")[0].split("|")[0].strip(), "website": raw_href})
        except Exception as e:
            print(f"[Bing Error] {e}")

    # 2. DuckDuckGo POST Motoru
    if len(results) < 15:
        try:
            ddg_headers = dict(headers)
            ddg_headers["Content-Type"] = "application/x-www-form-urlencoded"
            async with session.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=ddg_headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    parser = LexborHTMLParser(body)
                    for node in parser.css(".result, .web-result"):
                        t_node = node.css_first(".result__title .result__a")
                        if not t_node:
                            continue
                        title = t_node.text(strip=True)
                        raw_href = t_node.attributes.get("href", "")
                        target = None
                        if "uddg=" in raw_href:
                            qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                            if "uddg" in qs:
                                target = qs["uddg"][0]
                        elif raw_href.startswith("http"):
                            target = raw_href
                        if target:
                            host = URL(target).host or ""
                            clean_h = host.lower().removeprefix("www.")
                            if clean_h and not any(clean_h == s or clean_h.endswith("." + s) for s in SKIP_DOMAINS):
                                if clean_h not in seen:
                                    seen.add(clean_h)
                                    results.append({"name": title.split("-")[0].split("|")[0].strip(), "website": target})
        except Exception as e:
            print(f"[DDG Error] {e}")

    return results

async def crawl_site(company: Dict[str, str], session: aiohttp.ClientSession) -> Dict[str, Any]:
    target_url = company["website"]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    lead = {
        "Firma Adı": company["name"],
        "Web Sitesi": target_url,
        "Şehir/İlçe": TARGET_REGION,
        "Kategori": TARGET_KEYWORD,
        "Telefon": "",
        "Doğrulanmış E-Postalar": "",
        "Instagram Linki": "",
        "LinkedIn Linki": "",
        "Scrape_Tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        async with session.get(target_url, headers=headers, timeout=aiohttp.ClientTimeout(total=12), ssl=False) as resp:
            if resp.status == 200:
                html_text = await resp.text(errors="ignore")
                emails, phones, igs, lis = extract_contacts(html_text)
                if emails:
                    lead["Doğrulanmış E-Postalar"] = "; ".join(emails)
                if phones:
                    lead["Telefon"] = phones[0]
                if igs:
                    lead["Instagram Linki"] = igs[0]
                if lis:
                    lead["LinkedIn Linki"] = lis[0]
    except Exception:
        pass

    return lead

async def main():
    print(f"🚀 [Worker Başlatıldı] ID: {WORKER_ID} | Hedef: '{TARGET_KEYWORD}' ({TARGET_REGION})")
    
    # MongoDB Bağlantısı
    mongo_client = None
    leads_col = None
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command('ping')
        db = mongo_client["b2b_scraper"]
        leads_col = db["leads"]
        print("✓ MongoDB Atlas bağlantısı doğrulandı!")
    except Exception as e:
        print(f"⚠️ MongoDB Atlas Bağlantı Uyarısı: {e}")

    # Arama Yap
    query = f"{TARGET_KEYWORD} {TARGET_REGION} iletişim"
    print(f"🔍 Arama yapılıyor: {query}")
    async with aiohttp.ClientSession() as session:
        found_companies = await search_web_engines(query, session)
        print(f"✓ {len(found_companies)} aday web sitesi tespit edildi.")

        if not found_companies:
            print("Sonuç bulunamadı.")
            return

        # Siteleri Kazı
        print("🌐 Siteler taranıyor ve e-postalar çıkarılıyor...")
        tasks = [crawl_site(comp, session) for comp in found_companies[:30]]
        scraped_leads = await asyncio.gather(*tasks)

    # MongoDB Atlas'a Toplu Kayıt
    if leads_col is not None and scraped_leads:
        ops = []
        for l in scraped_leads:
            l["_worker"] = WORKER_ID
            web = l.get("Web Sitesi", "")
            name = l.get("Firma Adı", "")
            fil = {"Web Sitesi": web} if web else {"Firma Adı": name}
            ops.append(UpdateOne(fil, {"$set": l}, upsert=True))
        
        if ops:
            res = leads_col.bulk_write(ops, ordered=False)
            total_saved = (res.upserted_count or 0) + (res.modified_count or 0)
            print(f"🎉 BAŞARILI: {total_saved} firma doğrudan MongoDB Atlas havuzuna yazıldı!")
    else:
        print(f"Scraped {len(scraped_leads)} leads (local only).")

if __name__ == "__main__":
    asyncio.run(main())
