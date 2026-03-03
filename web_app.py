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
        # Using a standard laptop viewport size
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
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
            page.wait_for_timeout(4000)

            # --- 1. GET 10 TRACKS ---
            see_more = page.locator('button', has_text="See more").first
            if see_more.is_visible():
                see_more.click(force=True)
                page.wait_for_timeout(1500)

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                lines = [l.strip() for l in row.inner_text().split('\n') if l.strip()]
                if len(lines) >= 2:
                    
                    # Grab Title (First item that isn't a number or the 'E' explicit tag)
                    name = "Unknown"
                    for line in lines:
                        if not line.isdigit() and line != 'E':
                            name = line
                            break
                            
                    # Grab Streams (First large number)
                    streams = "Unknown"
                    for line in lines:
                        clean_num = line.replace(',', '')
                        if clean_num.isdigit() and len(clean_num) >= 5:
                            streams = line
                            break
                            
                    tracks.append({'name': name, 'streams': streams})

            # --- 2. CAREFUL SCROLL TO ABOUT SECTION ---
            about_section = page.locator('section[data-testid="about"]')
            
            # Press PageDown sequentially until the About section is visible
            for _ in range(15):
                if about_section.is_visible():
                    break
                page.keyboard.press("PageDown")
                page.wait_for_timeout(600)

            if about_section.is_visible():
                about_section.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)

                # --- 3. CLICK THE IMAGE ---
                box = about_section.bounding_box()
                if box:
                    # Click inside the top quarter of the card (where the image is)
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 4)
                else:
                    about_section.click(force=True)

                page.wait_for_timeout(3000) # Wait for popup modal to fully open

                # --- 4. SCROLL INSIDE THE MODAL ---
                dialog = page.locator('[role="dialog"]')
                if dialog.is_visible():
                    # Click near the top-left edge of the dialog to focus it without hitting a link
                    dialog.click(position={"x": 10, "y": 10}) 
                    
                    # Scroll down inside the dialog
                    for _ in range(4):
                        page.keyboard.press("PageDown")
                        page.wait_for_timeout(500)
                        
                    body_text = dialog.inner_text()
                else:
                    body_text = page.inner_text('body')

                # --- 5. EXTRACT CITIES ---
                if "Where people listen" in body_text:
                    parts = body_text.split("Where people listen")
                    if len(parts) > 1:
                        lines = [l.strip() for l in parts[1].split('\n') if l.strip()]
                        for i, line in enumerate(lines):
                            if "listeners" in line.lower() and i > 0:
                                city = lines[i-1]
                                count = line.replace("listeners", "").strip()
                                # Prevent grabbing single-letter artifacts
                                if len(cities_data) < 5 and len(city) > 2:
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
    with st.spinner("Scraping Spotify (this takes ~15 seconds to safely scroll and parse)"):
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
