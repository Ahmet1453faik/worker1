"""
B2B Lead Generation & Web Scraper Pipeline Genel Yapılandırma Modülü
"""
from dataclasses import dataclass, field
from typing import List, Set


@dataclass
class ScraperConfig:
    # Paralel istek ve ağ ayarları
    concurrency_limit: int = 50
    request_timeout: int = 12
    max_redirects: int = 4
    max_body_bytes: int = 2 * 1024 * 1024  # 2MB üzeri sayfalardan kaçın
    
    # Derinlik kontrolü: 1 = sadece ana sayfa, 2 = iletişim/hakkımızda sayfalarına derinleşme
    max_crawl_depth: int = 2
    
    # İstek başlığı
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    
    # Standart HTTP Başlıkları (brotli decompressor sorununu engellemek için gzip, deflate)
    default_headers: dict = field(default_factory=lambda: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate"
    })

    # İletişim / Hakkımızda sayfası tespit kalıpları (URL veya Link metni)
    contact_keywords: List[str] = field(default_factory=lambda: [
        "iletisim", "contact", "contact-us", "hakkimizda", "about", "about-us", "bize-ulasin",
        "reach-us", "kurumsal", "subeler", "magazalar", "impressum", "kontakt",
        "enquiry", "enquiries", "get-in-touch", "locations", "stores", "support", "help",
        "team", "customer-service", "musteri-hizmetleri"
    ])

    # Geçersiz/Sahte mail dosya uzantıları ve filtreleri
    invalid_email_extensions: Set[str] = field(default_factory=lambda: {
        "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp", "tiff",
        "js", "css", "woff", "woff2", "ttf", "eot", "mp4", "webm"
    })
    
    # Mail blacklisted domain / kalıplar (sahte, analytics veya template mailleri)
    blacklisted_email_domains: Set[str] = field(default_factory=lambda: {
        "sentry.io", "wixpress.com", "example.com", "domain.com", "email.com",
        "mysite.com", "yourdomain.com", "test.com", "schema.org", "w3.org",
        "google.com", "cloudflare.com", "gravatar.com"
    })

    # Sosyal medya domainleri
    instagram_domain: str = "instagram.com"
    linkedin_domain: str = "linkedin.com"

    # Scrapling Fallback & Anti-Bot Ayarları
    enable_scrapling_fallback: bool = True
    scrapling_headless: bool = True
    scrapling_network_idle: bool = True
    scrapling_timeout: int = 15


@dataclass
class OutreachConfig:
    # NVIDIA DeepSeek API Ayarları
    nvidia_api_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_api_key: str = "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"
    nvidia_model: str = "deepseek-ai/deepseek-v4-pro-0813"
    
    # SMTP E-posta Ayarları (Kullanıcı panelden veya ortam değişkeninden düzenleyebilir)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_name: str = "B2B Outreach"
    from_email: str = ""
    
    # Anti-Spam ve Hız Sınırları
    min_delay_seconds: int = 40   # Gönderimler arası minimum rastgele bekleme
    max_delay_seconds: int = 90   # Gönderimler arası maksimum rastgele bekleme
    daily_email_limit: int = 50   # Günlük maksimum e-posta kotası
    
    # Takip (Follow-up) Ayarları
    followup_after_days: int = 3  # Yanıt gelmezse kaç gün sonra takip yapılacak
    db_path: str = "outreach_tracker.db"


DEFAULT_CONFIG = ScraperConfig()
DEFAULT_OUTREACH_CONFIG = OutreachConfig()
