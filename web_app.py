import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import json

# Force Playwright to install the Chromium binary in the Python environment
os.system("playwright install chromium")

def get_spotify_streams_playwright(artist_id):
    # FIX 1: Use the correct Spotify URL format
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    # FIX 2: Switched to sync_playwright to prevent Streamlit event loop crashes
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        if os.path.exists("cookies.json"):
            with open("cookies.json", "r") as f:
                cookies = json.load(f)
                # FIX 3: Clean the mangled 'googleusercontent' domains so Playwright accepts them
                for cookie in cookies:
                    if "domain" in cookie and "googleusercontent" in cookie["domain"]:
                        cookie["domain"] = ".spotify.com"
                context.add_cookies(cookies)
            
        page = context.new_page()
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # --- TRACK SCRAPING ---
            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                text = row.inner_text()
                parts = [p.strip() for p in text.split('\n') if p.strip()]
                if len(parts) >= 2:
                    name = parts[0]
                    streams = "Unknown"
                    for p in parts:
                        if p.replace(',', '').isdigit() and len(p) > 3:
                            streams = p
                            break
                    tracks.append({'name': name, 'streams': streams})

            # --- LOCATION SCRAPING ---
            for _ in range(5):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(600)

            about_card = page.locator('section[data-testid="about"]')
            if about_card.count() > 0:
                about_card.
