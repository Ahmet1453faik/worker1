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
MONGO_URI = os.getenv("MONGO_URI", "")
if not MONGO_URI:
    try:
        from mongo_manager import MongoLeadManager
        MONGO_URI = MongoLeadManager.DEFAULT_URI
    except Exception:
        MONGO_URI = ""
TARGET_KEYWORD = os.getenv("TARGET_KEYWORD", "Tekstil Toptan")
TARGET_REGION = os.getenv("TARGET_REGION", "İstanbul")
WORKFLOW_ID = os.getenv("WORKFLOW_ID", "")
WORKFLOW_NAME = os.getenv("WORKFLOW_NAME", "")
if not WORKFLOW_ID:
    safe_kw = re.sub(r'[^a-zA-Z0-9]', '_', TARGET_KEYWORD)[:15]
    WORKFLOW_ID = f"wf_auto_{datetime.now().strftime('%Y%m%d_%H')}_{safe_kw}"
if not WORKFLOW_NAME:
    WORKFLOW_NAME = f"Bulut Arama: {TARGET_KEYWORD} ({TARGET_REGION})"
WORKER_ID = os.getenv("WORKER_ID", "github-worker-node")
SUB_WORKER_INDEX = os.getenv("SUB_WORKER_INDEX", "1")

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

def decode_bing_url(bing_ck_url: str) -> Optional[str]:
    """Bing ck/a redirect linkinden hedef URL'yi base64 cozerek cikarir."""
    try:
        import base64
        parsed = urllib.parse.urlparse(bing_ck_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "u" in qs and qs["u"]:
            u_val = qs["u"][0]
            if u_val.startswith("a1"):
                clean_b64 = u_val[2:]
                padding = 4 - (len(clean_b64) % 4)
                if padding != 4:
                    clean_b64 += "=" * padding
                return base64.b64decode(clean_b64).decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None

async def search_web_engines(query: str, session: aiohttp.ClientSession, max_results: int = 120) -> List[Dict[str, str]]:
    results = []
    seen = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,tr-TR,tr;q=0.8,de;q=0.7",
        "Accept-Encoding": "gzip, deflate"
    }

    # 1. Bing Search Motoru (5 Sayfa Derin Tarama: 1, 11, 21, 31, 41)
    for offset in [1, 11, 21, 31, 41]:
        if len(results) >= max_results:
            break
        try:
            b_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&first={offset}"
            async with session.get(b_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    parser = LexborHTMLParser(body)
                    nodes = parser.css("li.b_algo")
                    for node in nodes:
                        title_node = node.css_first("h2 a")
                        if not title_node:
                            continue
                        title = title_node.text(strip=True)
                        raw_href = title_node.attributes.get("href", "")
                        real_target = decode_bing_url(raw_href) if "bing.com/ck/" in raw_href else raw_href
                        if real_target and real_target.startswith("http"):
                            host = URL(real_target).host or ""
                            clean_h = host.lower().removeprefix("www.")
                            if clean_h and not any(clean_h == s or clean_h.endswith("." + s) for s in SKIP_DOMAINS):
                                if clean_h not in seen:
                                    seen.add(clean_h)
                                    clean_name = title.split("-")[0].split("|")[0].split(":")[0].strip()
                                    results.append({"name": clean_name or clean_h, "website": real_target})
        except Exception as e:
            print(f"[Bing Error] {e}")

    # 2. DuckDuckGo POST Motoru
    if len(results) < max_results:
        try:
            ddg_headers = dict(headers)
            ddg_headers["Content-Type"] = "application/x-www-form-urlencoded"
            async with session.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=ddg_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    parser = LexborHTMLParser(body)
                    nodes = parser.css(".result, .web-result")
                    for node in nodes:
                        if len(results) >= max_results:
                            break
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
                                    clean_name = title.split("-")[0].split("|")[0].split(":")[0].strip()
                                    results.append({"name": clean_name or clean_h, "website": target})
        except Exception as e:
            print(f"[DDG Error] {e}")

    # 3. DuckDuckGo GET /lite/ Fallback (Bulut IP dostu)
    if len(results) < 25:
        try:
            lite_url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
            async with session.get(lite_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    parser = LexborHTMLParser(body)
                    for a in parser.css("a.result-link"):
                        if len(results) >= max_results:
                            break
                        raw_href = a.attributes.get("href", "")
                        title = a.text(strip=True)
                        if "uddg=" in raw_href:
                            qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                            target = qs.get("uddg", [None])[0]
                        else:
                            target = raw_href
                        if target and target.startswith("http"):
                            host = URL(target).host or ""
                            clean_h = host.lower().removeprefix("www.")
                            if clean_h and not any(clean_h == s or clean_h.endswith("." + s) for s in SKIP_DOMAINS):
                                if clean_h not in seen:
                                    seen.add(clean_h)
                                    clean_name = title.split("-")[0].split("|")[0].split(":")[0].strip()
                                    results.append({"name": clean_name or clean_h, "website": target})
        except Exception as e:
            print(f"[DDG-Lite Error] {e}")

    return results

async def crawl_site(company: Dict[str, str], session: aiohttp.ClientSession) -> Dict[str, Any]:
    target_url = company["website"]
    company_region = company.get("region") or TARGET_REGION
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    lead = {
        "Firma Adı": company["name"],
        "Web Sitesi": target_url,
        "Şehir/İlçe": company_region,
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
    import time
    start_time = time.time()
    print(f"🚀 [Worker Başlatıldı] ID: {WORKER_ID} | SubWorker: {SUB_WORKER_INDEX} | Hedef: '{TARGET_KEYWORD}' ({TARGET_REGION})")
    
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

    # 1. Bölgeleri Çözümle (Virgülle ayrılmış çoklu bölgeleri ayır)
    raw_regions = [r.strip() for r in TARGET_REGION.split(",") if r.strip()]
    if not raw_regions:
        raw_regions = ["İstanbul"]

    # Matrix alt işçisi ayrıştırması
    sub_idx = 0
    try:
        sub_idx = int(re.sub(r'\D', '', str(SUB_WORKER_INDEX or "1"))) - 1
        if sub_idx < 0:
            sub_idx = 0
    except Exception:
        sub_idx = 0

    assigned_regions = raw_regions
    if len(raw_regions) >= 3:
        # Her alt işçiye kendi bölgesini paylaştır (çakışma ve mükerrer arama engellenir)
        assigned_regions = [r for i, r in enumerate(raw_regions) if i % 3 == (sub_idx % 3)]
        if not assigned_regions:
            assigned_regions = [raw_regions[sub_idx % len(raw_regions)]]

    # Anahtar kelimeleri ayrıştır (virgülle ayrılmış çoklu AI terimleri veya tekil kök kelime)
    raw_kw_list = [k.strip() for k in TARGET_KEYWORD.split(",") if k.strip()]
    if not raw_kw_list:
        raw_kw_list = [TARGET_KEYWORD.strip() or "Tekstil Toptan"]

    # Matrix alt işçileri anahtar kelimeleri veya varyasyonları dengeli paylaşır
    sub_queries_with_region = []
    for reg in assigned_regions:
        is_tr = any(c in f"{TARGET_KEYWORD} {reg}".lower() for c in "çğıöşü")
        
        # Eğer dışarıdan çoklu terim kümesi (örn. AI veya zengin liste) gelmişse:
        if len(raw_kw_list) > 1:
            # Her alt işçi terimlerin bir kısmını alır, böylece çakışma olmaz
            worker_kws = [k for i, k in enumerate(raw_kw_list) if i % 3 == (sub_idx % 3)]
            if not worker_kws:
                worker_kws = [raw_kw_list[sub_idx % len(raw_kw_list)]]
            for kw in worker_kws:
                sub_queries_with_region.append((f"{kw} {reg}".strip(), reg))
                sub_queries_with_region.append((f"{kw} in {reg}".strip(), reg))
        else:
            # Tek bir anahtar kelime verilmişse klasik ticari kalıplarla genişlet
            base_kw = raw_kw_list[0]
            if is_tr:
                if sub_idx % 3 == 0:
                    variations = [f"{base_kw} {reg} toptan satış", f"{base_kw} {reg} iletişim", f"{base_kw} mağazaları {reg}"]
                elif sub_idx % 3 == 1:
                    variations = [f"{base_kw} toptan imalat {reg}", f"{base_kw} üretici fabrika {reg}", f"{base_kw} imalatçıları {reg}"]
                else:
                    variations = [f"{base_kw} tedarikçi {reg}", f"{base_kw} sanayi ve ticaret {reg}", f"{base_kw} ihracat dış ticaret {reg}"]
            else:
                if sub_idx % 3 == 0:
                    variations = [f"{base_kw} {reg}".strip(), f"{base_kw} wholesale suppliers {reg}".strip(), f"{base_kw} stores {reg}".strip()]
                elif sub_idx % 3 == 1:
                    variations = [f"{base_kw} manufacturer factory {reg}".strip(), f"{base_kw} producers {reg}".strip(), f"{base_kw} industrial {reg}".strip()]
                else:
                    variations = [f"{base_kw} boutique distributors {reg}".strip(), f"{base_kw} trade exporters {reg}".strip(), f"{base_kw} supply chain {reg}".strip()]
            for v in variations:
                sub_queries_with_region.append((v, reg))

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        found_companies = []
        seen_sites = set()

        for q, reg in sub_queries_with_region:
            print(f"🔍 [Alt Arama #{sub_idx+1}] ({reg}) -> {q}")
            comps = await search_web_engines(q, session, max_results=50)
            for c in comps:
                w_clean = c.get("website", "").lower()
                if w_clean and w_clean not in seen_sites:
                    seen_sites.add(w_clean)
                    c["region"] = reg
                    found_companies.append(c)
            if len(found_companies) >= 150:
                break

        print(f"✓ Toplam {len(found_companies)} benzersiz aday web sitesi tespit edildi.")

        if not found_companies:
            print("Sonuç bulunamadı.")
            return

        # Siteleri Kazı (En fazla 120 işletme, Semaphore 15 ile süper hızlı)
        print("🌐 Siteler derinlemesine taranıyor ve iletişim verileri çıkarılıyor...")
        crawl_sem = asyncio.Semaphore(15)

        async def _crawl_bounded(comp):
            async with crawl_sem:
                return await crawl_site(comp, session)

        tasks = [_crawl_bounded(comp) for comp in found_companies[:120]]
        scraped_leads = await asyncio.gather(*tasks)

    # Yerel JSON Yedeği (GitHub Artifact & API İçin)
    if scraped_leads:
        try:
            import json
            with open("cloud_leads.json", "w", encoding="utf-8") as f:
                json.dump(scraped_leads, f, ensure_ascii=False, indent=2)
            print(f"📁 {len(scraped_leads)} firma cloud_leads.json dosyasına yazıldı.")
        except Exception as e:
            print(f"JSON kayıt hatası: {e}")

    # MongoDB Atlas'a Toplu Kayıt (Mükerrer tekilleştirme ve tarih koruma)
    if leads_col is not None and scraped_leads:
        ops = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for l in scraped_leads:
            l["_worker"] = WORKER_ID
            l["workflow_id"] = WORKFLOW_ID
            l["workflow_name"] = WORKFLOW_NAME
            l["Scrape_Tarihi"] = now_str
            l["Son_Guncelleme_Tarihi"] = now_str
            web = l.get("Web Sitesi", "")
            name = l.get("Firma Adı", "")
            fil = {"Web Sitesi": web} if web else {"Firma Adı": name}
            
            # $setOnInsert ile ilk kayıt tarihini sakla, $set ile son bilgileri güncelle, $addToSet ile workflow_id ekle
            ops.append(UpdateOne(
                fil,
                {
                    "$set": l,
                    "$addToSet": {"workflows": WORKFLOW_ID},
                    "$setOnInsert": {"Ilk_Eklenme_Tarihi": now_str}
                },
                upsert=True
            ))
        
        if ops:
            res = leads_col.bulk_write(ops, ordered=False)
            total_saved = (res.upserted_count or 0) + (res.modified_count or 0)
            print(f"🎉 BAŞARILI: {total_saved} firma doğrudan MongoDB Atlas havuzuna yazıldı!")
    else:
        print(f"Scraped {len(scraped_leads)} leads (local only).")

    total_dur = time.time() - start_time
    m, s = divmod(int(total_dur), 60)
    print(f"⏱️ Toplam Çalışma Süresi: {m} dakika {s} saniye ({total_dur:.1f}s)")

if __name__ == "__main__":
    asyncio.run(main())
