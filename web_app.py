import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import json
import re
import subprocess

# --- CACHE PLAYWRIGHT INSTALLATION ---
@st.cache_resource
def install_playwright():
    with st.spinner("Initializing headless browser backend..."):
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
            viewport={'width': 1280, 'height': 900},
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

            # --- 1. GET TOP 10 TRACKS ---
            see_more = page.locator('button', has_text="See more").first
            if see_more.is_visible():
                see_more.click(force=True)
                page.wait_for_timeout(1000)

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                lines = [l.strip() for l in row.inner_text().split('\n') if l.strip()]
                if len(lines) >= 2:
                    name = "Unknown"
                    for line in lines:
                        if not line.isdigit() and line != 'E':
                            name = line
                            break
                            
                    streams = "Unknown"
                    for line in lines:
                        clean_num = line.replace(',', '')
                        if clean_num.isdigit() and len(clean_num) >= 5:
                            streams = line
                            break
                            
                    tracks.append({'name': name, 'streams': streams})

            # --- 2. EXTRACT CITIES (METHOD A: RAW HTML DATA MINING) ---
            # This instantly pulls the cities from Spotify's hidden pre-loaded data object
            content = page.content()
            try:
                wpl_match = re.search(r'"wherePeopleListen"\s*:\s*\{\s*"cities"\s*:\s*(\[.*?\])\s*\}', content)
                if wpl_match:
                    cities_list = json.loads(wpl_match.group(1))
                    for c in cities_list[:5]:
                        if "city" in c and "listeners" in c:
                            # Format listeners with commas for readability
                            cities_data.append({"City": c["city"], "Listeners": f"{c['listeners']:,}"})
            except Exception as e:
                print(f"Data mining extraction failed: {e}")

            # --- 3. EXTRACT CITIES (METHOD B: VIDEO UI FALLBACK) ---
            # If the data isn't in the code, perform the exact manual clicks from your video
            if not cities_data:
                about_section = page.locator('section[data-testid="about"]')
                
                # Scroll exactly to the About card
                for _ in range(10):
                    if about_section.is_visible():
                        break
                    page.keyboard.press("PageDown")
                    page.wait_for_timeout(500)

                if about_section.is_visible():
                    about_section.scroll_into_view_if_needed()
                    page.wait_for_timeout(1000)

                    # Click the image perfectly in the center
                    box = about_section.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 4)
                    else:
                        about_section.click(force=True)

                    page.wait_for_timeout(2000)

                    # Wait for the popup and scroll down INSIDE the popup using Javascript
                    dialog = page.locator('[role="dialog"]')
                    if dialog.is_visible():
                        dialog.evaluate("node => node.scrollBy(0, 1000)")
                        page.wait_for_timeout(1500)
                        
                        body_text = dialog.inner_text()
                        lines = [l.strip() for l in body_text.split('\n') if l.strip()]
                        
                        for i, line in enumerate(lines):
                            if line.endswith("listeners") and "monthly" not in line.lower() and i > 0:
                                city = lines[i-1]
                                count = line.replace("listeners", "").strip()
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
    with st.spinner("Extracting hidden demographic data..."):
        results, cities, err = perform_search(query)
        if err:
            st.error(f"Error: {err}")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader("Top 10 Tracks")
                if results:
                    st.dataframe(pd.DataFrame(results), width='stretch', hide_index=True)
                else:
                    st.warning("Could not pull tracks.")
            with c2:
                st.subheader("Top 5 Cities")
                if cities:
                    for c in cities:
                        st.write(f"**{c['City']}**")
                        st.write(f"{c['Listeners']} listeners")
                        st.divider()
                else:
                    st.warning("Could not locate city data. Check the debug image below.")
                    if os.path.exists("debug_screenshot.png"):
                        st.image("debug_screenshot.png")
