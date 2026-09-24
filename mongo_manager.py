"""
MongoDB Atlas Entegrasyon Modülü.
GitHub Actions worker'ları ve yerel Streamlit arayüzü arasındaki
ortak merkezi bulut veritabanı köprüsüdür.
"""
import os
from typing import Dict, List, Any, Optional
import pymongo
from pymongo import MongoClient, UpdateOne


class MongoLeadManager:
    DEFAULT_URI = "mongodb+srv://ahmet1453ozsoy_db_user:zZPdtYrxCcw8xt4v@cluster0.y5ybnvp.mongodb.net/?appName=Cluster0"

    def __init__(self, uri: Optional[str] = None):
        self.uri = uri or os.getenv("MONGO_URI", "") or self.DEFAULT_URI
        self.client = None
        self.db = None
        self.collection = None
        if self.uri:
            try:
                self.client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
                self.db = self.client["b2b_scraper"]
                self.collection = self.db["leads"]
                # Tekilleştirme indexi (Firma Adı + Şehir veya Web Sitesi)
                self.collection.create_index([("Firma Adı", 1), ("Şehir/İlçe", 1)], unique=False)
                self.collection.create_index("Web Sitesi", sparse=True)
            except Exception as e:
                print(f"[MongoLeadManager] Bağlantı uyarısı: {e}")

    def is_connected(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.admin.command('ping')
            return True
        except Exception:
            return False

    def insert_or_update_leads(self, leads: List[Dict[str, Any]], worker_id: str = "local") -> int:
        """
        Gelen lead listesini topluca kaydeder. Aynı web sitesi veya firma adı varsa günceller.
        """
        if not self.is_connected() or not leads:
            return 0

        operations = []
        for lead in leads:
            lead_copy = dict(lead)
            lead_copy["_worker_id"] = worker_id
            web = lead_copy.get("Web Sitesi", "").strip()
            name = lead_copy.get("Firma Adı", "").strip()

            filter_query = {}
            if web and len(web) > 4:
                filter_query = {"Web Sitesi": web}
            elif name:
                filter_query = {"Firma Adı": name, "Şehir/İlçe": lead_copy.get("Şehir/İlçe", "")}
            else:
                continue

            operations.append(
                UpdateOne(filter_query, {"$set": lead_copy}, upsert=True)
            )

        if operations:
            result = self.collection.bulk_write(operations, ordered=False)
            return (result.upserted_count or 0) + (result.modified_count or 0)
        return 0

    def get_all_leads(self, limit: int = 5000) -> List[Dict[str, Any]]:
        if not self.is_connected():
            return []
        try:
            cursor = self.collection.find({}, {"_id": 0}).sort("Scrape_Tarihi", -1).limit(limit)
            return list(cursor)
        except Exception:
            return []

    def get_total_count(self) -> int:
        if not self.is_connected():
            return 0
        try:
            return self.collection.count_documents({})
        except Exception:
            return 0
