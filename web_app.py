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
    with st.spinner("Initializing browser..."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_streams_playwright(artist_id):
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
                for cookie in cookies:
                    if "domain" in cookie:
                        cookie["domain"] = ".spotify.com"
                try:
                    context.add_cookies(cookies)
                except:
                    pass
            
        page = context.new_page()
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            # --- 1. CLICK 'SEE MORE' FOR 10 TRACKS ---
            see_more_btn = page.locator('button', has_text="See more").first
            if see_more_btn.count() > 0:
                try:
                    see_more_btn.click(force=True)
                    page.wait_for_timeout(1500)
                except: pass

            # --- 2. EXTRACT TRACK NAMES AND STREAMS ---
            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                text = row.inner_text()
                parts = [p.strip() for p in text.split('\n') if p.strip()]
                
                if len(parts) >= 2:
                    # parts[0] is usually the row number (1, 2, 3...). parts[1] is the track title.
                    name = parts[1] if parts[0].isdigit() else parts[0]
                    
                    streams = "Unknown"
                    for p in parts:
                        p_clean = p.replace(',', '')
                        # Look for the large stream number
                        if p_clean.isdigit() and len(p_clean) >= 5:
                            streams = p
                            break
                            
                    tracks.append({'name': name, 'streams': streams})

            # --- 3. SCROLL DOWN AND OPEN ABOUT SECTION ---
            # Move mouse to center of screen and scroll wheel to trigger lazy loading
            page.mouse.move(640, 540)
            for _ in range(8):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(600)

            about_card = page.locator('[data-testid="about"]')
            if about_card.count() > 0:
                about_card.click(force=True)
                page.wait_for_timeout(2500) # Wait for popup modal to open

                # --- 4. SCROLL INSIDE THE MODAL ---
                dialog = page.locator('[role="dialog"]')
                if dialog.count() > 0:
                    dialog.hover() # Move mouse inside the popup
                    for _ in range(4):
                        page.mouse.wheel(0, 600)
                        page.wait_for_timeout(500)
                    
                    body_text = dialog.inner_text()
                else:
                    body_text = page.inner_text('body')

                # --- 5. EXTRACT CITIES ---
                if "Where people listen" in body_text:
                    block = body_text.split("Where people listen")[1]
                    lines = [l.strip() for l in block.split('\n') if l.strip()]
                    
                    for i, line in enumerate(lines):
                        if "listeners" in line.lower() and i > 0:
                            city = lines[i-1]
                            count = line.replace("listeners", "").strip()
                            # Prevent grabbing random UI text as a city
                            if len(cities_data) < 5 and len(city) > 2:
                                cities_data.append({"City": city, "Listeners": count})

            # Take a debug screenshot if it still fails to find cities
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
    with st.spinner("Accessing Spotify... (This takes about 15-20 seconds to fully scroll and scrape)"):
        results, cities, err = perform_search(query)
        if err:
            st.error(f"Error: {err}")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader("Top Tracks")
                if results:
                    st.dataframe(pd.DataFrame(results), use_container_width=True)
                else:
                    st.warning("Could not pull tracks.")
            with c2:
                st.subheader("Top 5 Cities")
                if cities:
                    for c in cities:
                        st.write(f"**{c['City']}**: {c['Listeners']} listeners")
                else:
                    st.warning("Could not locate city data. Check the debug image below.")
                    if os.path.exists("debug_screenshot.png"):
                        st.image("debug_screenshot.png")
