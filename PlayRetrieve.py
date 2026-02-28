import argparse
import json
import base64
import os
import re
import time
import zipfile
import glob

from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from tqdm import tqdm
from datetime import datetime

import cloudscraper
import requests  # only for exceptions

# ==============================
# Cloudflare-aware Session
# ==============================
session = cloudscraper.create_scraper(
    browser={
        "browser": "chrome",
        "platform": "windows",
        "mobile": False
    }
)

# --- API Configuration ---
TOKEN_URL = "https://token.mi9.com/"
API_URL = "https://api.mi9.com/get"
GET_VERSION_URL = "https://api.mi9.com/get-version"
DEFAULT_SDK = 30

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

HEADERS_TOKEN = {**COMMON_HEADERS, "Accept": "*/*", "Content-Type": "application/json"}
HEADERS_API_GET = {**COMMON_HEADERS, "Accept": "text/event-stream"}
HEADERS_GET_VERSION = {**COMMON_HEADERS, "Accept": "*/*", "Content-Type": "application/json"}

PLAY_URL = "https://play.google.com/store/apps/details?id="
OUTPUT_BASE_DIR = "apk_downloads"

# ==============================
# Utility Functions
# ==============================

def extract_package_id(play_store_url):
    parsed_url = urlparse(play_store_url)
    query_params = parse_qs(parsed_url.query)
    return query_params.get('id', [None])[0]

# ==============================
# TOKEN REQUEST
# ==============================

def get_api_token(package_id, device="phone", arch="arm64-v8a", vc="0", sdk_version_arg=None):
    sdk_to_use = sdk_version_arg if sdk_version_arg else DEFAULT_SDK

    payload = {
        "package": package_id,
        "device": device,
        "arch": arch,
        "vc": vc,
        "device_id": "",
        "sdk": sdk_to_use
    }

    try:
        print(f"[*] Requesting token for {package_id} (vc:{vc}, sdk:{sdk_to_use})...")
        response = session.post(TOKEN_URL, headers=HEADERS_TOKEN, json=payload, timeout=30)

        data = response.json()
        if data.get("success"):
            print("[+] Token obtained.")
            return data["token"], data["timestamp"], sdk_to_use
        else:
            print(f"[!] Token error: {data}")
            return None, None, None

    except requests.exceptions.RequestException as e:
        print(f"[!] Network error: {e}")
        return None, None, None

# ==============================
# EVENT STREAM
# ==============================

def process_api_event_stream(token, package_id, timestamp, device, arch, vc, sdk_val):
    data_payload = {
        "hl": "en",
        "package": package_id,
        "device": device,
        "arch": arch,
        "vc": vc,
        "device_id": "",
        "sdk": sdk_val,
        "timestamp": timestamp
    }

    encoded_data = base64.urlsafe_b64encode(
        json.dumps(data_payload, separators=(',', ':')).encode()
    ).decode()

    params = {"token": token, "data": encoded_data}

    try:
        with session.get(API_URL, headers=HEADERS_API_GET, params=params, stream=True, timeout=60) as response:
            response.raise_for_status()
            full_event_data = ""
            last_json = None

            for line in response.iter_lines():
                if line:
                    decoded = line.decode()
                    if decoded.startswith("data: "):
                        full_event_data += decoded[6:]
                        try:
                            last_json = json.loads(full_event_data)
                            full_event_data = ""
                        except json.JSONDecodeError:
                            pass

            return last_json

    except requests.exceptions.RequestException as e:
        print(f"[!] Event stream error: {e}")
        return None

# ==============================
# DOWNLOAD
# ==============================

def download_file(session, url, output_dir, filename=None):
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Fallback filename if missing
    if not filename or filename.strip() == "":
        filename = "download.apk"

    # Prevent path traversal
    filename = os.path.basename(filename)

    filepath = os.path.join(output_dir, filename)

    # If somehow filepath resolves to directory, fix it
    if os.path.isdir(filepath):
        filepath = os.path.join(output_dir, "download.apk")

    response = session.get(url, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    chunk_size = 8192

    with open(filepath, "wb") as f, tqdm(
        desc=filename,
        total=total_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    ) as progress_bar:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                progress_bar.update(len(chunk))

    print(f"[+] Download complete: {filepath}")

# ==============================
# MAIN FLOW (Minimal Example)
# ==============================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()

    package_id = extract_package_id(args.url)
    if not package_id:
        print("[!] Invalid Play URL.")
        return

    token, timestamp, sdk_used = get_api_token(package_id)
    if not token:
        print("[!] Could not get token.")
        return

    result = process_api_event_stream(
        token,
        package_id,
        timestamp,
        "phone",
        "arm64-v8a",
        "0",
        sdk_used
    )

    if not result:
        print("[!] No download info.")
        return

    html_content = result.get("html", "")
    soup = BeautifulSoup(html_content, "html.parser")

    links = soup.select("div.apk_files_item a[href]")

    if not links:
        print("[!] No APK links found.")
        return

    for index, link in enumerate(links, start=1):
        url = link["href"]

        # Force structured filename
        filename = f"{package_id}_part{index}.apk"

        print(f"[*] Downloading {filename} ...")

        download_file(
            session,
            url,
            os.path.join(OUTPUT_BASE_DIR, package_id),
            filename
        )      

if __name__ == "__main__":
    main()
