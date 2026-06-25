import sys
import re
import time
import random
import platform
import shutil
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
from bs4 import BeautifulSoup
import pandas as pd

POST_URL   = sys.argv[1] if len(sys.argv) > 1 else ""
EMAIL      = sys.argv[2] if len(sys.argv) > 2 else ""
PASSWORD   = sys.argv[3] if len(sys.argv) > 3 else ""
MAX        = int(sys.argv[4]) if len(sys.argv) > 4 else 10
OUTPUT_CSV = sys.argv[5] if len(sys.argv) > 5 else "fb_comments.csv"

_TIME_RE = re.compile(
    r'\s*(\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago'
    r'|a\s+(?:second|minute|hour|day|week|month|year)\s+ago'
    r'|just\s+now'
    r'|\d+[smhd])\s*$',
    re.IGNORECASE
)
_NON_PROFILE_PATHS = ("/posts/", "/groups/", "/events/", "/pages/",
                      "/photo", "/video", "/reel", "/watch")

class FacebookScraper:
    def __init__(self, email, password):
        self.email    = email
        self.password = password
        self.driver   = None

    def initialize_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--window-size=1280,900")

        if platform.system() == "Linux":
            # On Streamlit Cloud / Debian containers, apt installs Chromium,
            # not Google Chrome — these are versioned independently, so the
            # driver must be fetched as the Chromium flavor to match.
            chromium_path = (
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or "/usr/bin/chromium"
            )
            options.binary_location = chromium_path
            driver_path = ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
        else:
            # Windows/Mac local dev: regular Google Chrome
            driver_path = ChromeDriverManager().install()

        service = Service(driver_path)
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def simulate_human_typing(self, element, text):
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))

    def login(self):
        self.driver.get("https://www.facebook.com/login")
        email_input = WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        self.simulate_human_typing(email_input, self.email)
        password_input = self.driver.find_element(By.NAME, "pass")
        self.simulate_human_typing(password_input, self.password)
        password_input.send_keys(Keys.RETURN)
        time.sleep(15)  # Increased wait for login

    def navigate_to_post(self, post_url):
        self.driver.get(post_url)
        time.sleep(8)

    def _count_comment_articles(self):
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        return len([
            d for d in soup.find_all("div", {"role": "article"})
            if d.get("aria-label", "").startswith("Comment by")
        ])

    def load_comments(self, max_comments=10):
        """Scroll and click 'View more comments'"""
        for i in range(30):  # Increased attempts
            self.driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(2)
            try:
                # More robust button detection
                btns = self.driver.find_elements(
                    By.XPATH,
                    "//span[contains(text(), 'View more comments') or contains(text(), 'view more comments') or contains(text(), 'Voir plus de commentaires') or contains(text(), 'Ver más comentarios')]"
                )
                for btn in btns:
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(0.5)
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(3)
                    except Exception:
                        pass
            except Exception:
                pass
            
            found = self._count_comment_articles()
            if found >= max_comments:
                break
        time.sleep(4)

    def get_actual_comment_count(self):
        """Read the comment count from the post's reaction bar."""
        try:
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            for span in soup.find_all("span"):
                text = span.get_text(strip=True)
                # Matches "9 comments", "9 Comments", "1 comment" etc.
                m = re.match(r'^(\d+)\s+comments?$', text, re.IGNORECASE)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None  # couldn't find it, proceed with user's max

    @staticmethod
    def _parse_aria(aria_label):
        raw = aria_label.replace("Comment by ", "", 1).strip()
        m   = _TIME_RE.search(raw)
        if m:
            timestamp = m.group(0).strip()
            name      = raw[: m.start()].strip()
        else:
            name      = raw
            timestamp = ""
        return name, timestamp

    @staticmethod
    def _profile_url(href):
        if not href:
            return ""
        if href.startswith("https://web.facebook.com"):
            href = href.replace("https://web.facebook.com", "https://www.facebook.com", 1)
        elif href.startswith("/"):
            href = "https://www.facebook.com" + href
        if not href.startswith("https://www.facebook.com"):
            return ""
        base = href.split("?")[0].rstrip("/")
        path = base.replace("https://www.facebook.com", "")
        if any(p in path for p in _NON_PROFILE_PATHS):
            return ""
        if not path or path == "/":
            return ""
        return base

    def extract_comments(self, max_comments=10):
        soup           = BeautifulSoup(self.driver.page_source, "html.parser")
        comments_data  = []
        all_articles   = soup.find_all("div", {"role": "article"})
        comment_blocks = [
            d for d in all_articles
            if d.get("aria-label", "").startswith("Comment by")
        ]
        for block in comment_blocks:
            if len(comments_data) >= max_comments:
                break
            aria      = block.get("aria-label", "")
            name, ts  = self._parse_aria(aria)
            profile_url = ""
            for a in block.find_all("a", href=True):
                url = self._profile_url(a["href"])
                if url:
                    profile_url = url
                    break
            text = ""
            try:
                parts = []
                for el in block.find_all("div", {"dir": "auto"}):
                    t = el.get_text(strip=True)
                    if t and t != name and len(t) > 1:
                        parts.append(t)
                seen, unique = set(), []
                for p in parts:
                    if p not in seen:
                        seen.add(p); unique.append(p)
                text = " ".join(unique)
            except Exception:
                pass
            if text:
                comments_data.append({
                    "commenter_name": name,
                    "profile_url":    profile_url,
                    "comment_text":   text,
                    "comment_time":   ts,
                })
        return comments_data
    
    def close(self):
        if self.driver:
            self.driver.quit()

if __name__ == "__main__":
    if not all([POST_URL, EMAIL, PASSWORD]):
        print("Missing required arguments", file=sys.stderr)
        sys.exit(1)
    
    scraper = FacebookScraper(EMAIL, PASSWORD)
    try:
        scraper.initialize_driver()
        scraper.login()
        scraper.navigate_to_post(POST_URL)

        # Cap MAX to actual comment count if we can read it
        actual = scraper.get_actual_comment_count()
        print(actual)

        if actual is not None and actual < MAX:
            print(f"Post only has {actual} comments, adjusting max from {MAX} to {actual}")
            MAX = actual
        
        scraper.load_comments(max_comments=MAX)
        comments = scraper.extract_comments(max_comments=MAX)
        
        if comments:
            df = pd.DataFrame(comments)
            df.drop_duplicates(inplace=True)
            df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
            print(f"Successfully scraped {len(comments)} comments to {OUTPUT_CSV}")
        else:
            print("No comments found", file=sys.stderr)
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)
    finally:
        scraper.close()
