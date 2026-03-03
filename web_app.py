import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import json
import subprocess

# --- CACHE PLAYWRIGHT INSTALLATION ---
@st.cache_resource
def install_playwright():
    with st.spinner("Initializing browser... this will just take a moment on the first run."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_streams_playwright(artist_id):
    # FIX 1: Point to the actual Spotify URL
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        if os.path.exists("cookies.json"):
            with open("cookies.json", "r") as f:
                cookies = json.load(f)
                # Ensure the cookies apply to the real Spotify domain
                for cookie in cookies:
                    if "domain" in cookie:
                        cookie["domain"] = ".spotify.com"
                try:
                    context.add_cookies(cookies)
                except Exception as e:
                    print(f"Cookie warning: {e}")
            
        page = context.new_page()
        
        try:
            # wait_until="domcontentloaded" is safer for Spotify to avoid timeout crashes
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            # --- FIX 2: SMARTER TRACK SCRAPING ---
            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                text = row.inner_text()
                parts = [p.strip() for p in text.split('\n') if p.strip()]
                
                if len(parts) >= 2:
                    # If the first item is the list number (1, 2, 3), the name is the next item.
                    name = parts[1] if parts[0].isdigit() else parts[0]
                    
                    streams = "Unknown"
                    for p in parts:
                        p_clean = p.replace(',', '')
                        # Identify stream count: digits only, at least 4 chars long to ignore duration/rank
                        if p_clean.isdigit() and len(p_clean) >= 4:
                            streams = p
                            break
                            
                    tracks.append({'name': name, 'streams': streams})

            # --- FIX 3: LAZY LOADING & CITY EXTRACTION ---
            # Focus the page and press PageDown to force Spotify to load the lower sections
            page.keyboard.press("Tab")
            for _ in range(6):
                page.keyboard.press("PageDown")
                page.wait_for_timeout(800)

            about_card = page.locator('[data-testid="about"]')
            if about_card.count() > 0:
                about_card.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                about_card.click(force=True)
                page.wait_for_timeout(3000) # Wait for the 'About' modal to open

                # The cities are usually inside the popup dialog
                dialog = page.locator('[role="dialog"]')
                body_text = dialog.inner_text() if dialog.count() > 0 else page.inner_text('body')

                if "Where people listen" in body_text:
                    block = body_text.split("Where people listen")[1]
                    lines = [l.strip() for l in block.split('\n') if l.strip()]
                    
                    for i, line in enumerate(lines):
                        if "listeners" in line.lower() and i > 0:
                            city = lines[i-1]
                            count = line.replace("listeners", "").strip()
                            if len(cities_data) < 5:
                                cities_data.append({"City": city, "Listeners": count})

            if not cities_data:
                page.screenshot(path="debug_screenshot.png")

        except Exception as e:
            st.error(f"Scraper encountered an issue: {e}")
        finally:
            browser.close()
            
    return tracks, cities_data

def get_release_date(sp, artist_name, track_name):
    try:
        res = sp.search(q=f"artist:{artist_name} track:{track_name}", type='track', limit=1)
        if res['tracks']['items']:
            return res['tracks']['items'][0]['album']['release_date']
    except: pass
    return "Unknown"

def perform_search(artist_input):
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id="1d7660677d5b4567b86bfa2d730eacd7",
        client_secret="37a4d9cd968e43ad851074944d2df8e7"
    ))

    try:
        if "artist/" in artist_input:
            artist_id = artist_input.split("artist/")[1].split("?")[0]
        else:
            search = sp.search(q=artist_input, type='artist', limit=1)
            artist_id = search['artists']['items'][0]['id']
        
        artist_name = sp.artist(artist_id)['name']
        
        tracks_raw, cities = get_spotify_streams_playwright(artist_id)
        
        final_results = []
        for t in tracks_raw:
            date = get_release_date(sp, artist_name, t['name'])
            final_results.append({"Track Name": t['name'], "Release Date": date, "Total Streams": t['streams']})
            
        return final_results, cities, None
    except Exception as e:
        return None, None, str(e)

# --- SIMPLE UI ---
st.set_page_config(page_title="Spotify Pro Scraper", layout="wide")
st.title("🎧 Spotify Artist Insights")

query = st.text_input("Enter Artist Name or URL")

if st.button("Get Data"):
    with st.spinner("Accessing Spotify..."):
        results, cities, err = perform_search(query)
        if err:
            st.error(f"Error: {err}")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader("Top 10 Tracks")
                st.dataframe(pd.DataFrame(results), use_container_width=True)
            with c2:
                st.subheader("Top 5 Cities")
                if cities:
                    for c in cities:
                        st.write(f"**{c['City']}**: {c['Listeners']} listeners")
                else:
                    st.warning("Could not locate city data. See debug image below.")
                    if os.path.exists("debug_screenshot.png"):
                        st.image("debug_screenshot.png")
