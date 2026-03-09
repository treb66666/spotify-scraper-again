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
        # Ensures the browser binaries are present on the host (Streamlit Cloud or Local)
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_data_via_interception(artist_id):
    """
    Uses Network Interception to catch the internal GraphQL/API calls 
    containing track and city listener data.
    """
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Apply cookies if available
        if os.path.exists("cookies.json"):
            with open("cookies.json", "r") as f:
                cookies = json.load(f)
                # Quick fix for the domain format in your specific cookies.json
                for cookie in cookies:
                    if "spotify.com" in cookie.get("domain", ""):
                        cookie["domain"] = ".spotify.com"
                try:
                    context.add_cookies(cookies)
                except:
                    pass
            
        page = context.new_page()

        # --- NETWORK INTERCEPTION LOGIC ---
        def handle_response(response):
            nonlocal cities_data
            # Spotify often uses GraphQL endpoints for "About" data
            if "queryArtistAbout" in response.url or "queryWherePeopleListen" in response.url:
                try:
                    data = response.json()
                    # Drill down to the top cities list
                    # Path: data -> artistUnion -> stats -> topCities -> items
                    items = data['data']['artistUnion']['stats']['topCities']['items']
                    if items and not cities_data:
                        for item in items[:5]:
                            cities_data.append({
                                "City": item['city'], 
                                "Listeners": f"{item['numberOfListeners']:,}"
                            })
                except Exception:
                    pass

        # Attach the listener
        page.on("response", handle_response)
        
        try:
            # 1. Load the page
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            # 2. Trigger the Top 10 tracks (DOM Scraping)
            see_more = page.locator('button', has_text="See more").first
            if see_more.is_visible():
                see_more.click()
                page.wait_for_timeout(1000)

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                lines = [l.strip() for l in row.inner_text().split('\n') if l.strip()]
                if len(lines) >= 2:
                    name = next((l for l in lines if not l.isdigit() and l != 'E'), "Unknown")
                    streams = next((l for l in lines if l.replace(',', '').isdigit() and len(l.replace(',', '')) >= 5), "Unknown")
                    tracks.append({'name': name, 'streams': streams})

            # 3. Trigger the "About" data (API Interception)
            # We scroll to the bottom to force Spotify to lazy-load the "About" stats
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000) # Wait for the network listener to catch the JSON

            # Fallback: If interception didn't catch it, try one manual click on the "About" card
            if not cities_data:
                about_card = page.locator('section[data-testid="about"]')
                if about_card.is_visible():
                    about_card.click()
                    page.wait_for_timeout(2000)

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
    # Your existing credentials
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
        
        # Call the new interception function
        tracks_raw, cities = get_spotify_data_via_interception(artist_id)
        
        final_results = []
        for t in tracks_raw:
            date = get_release_date(sp, artist_name, t['name'])
            final_results.append({"Track Name": t['name'], "Release Date": date, "Total Streams": t['streams']})
            
        return final_results, cities, None
    except Exception as e:
        return None, None, str(e)

# --- UI SETUP ---
st.set_page_config(page_title="Spotify Pro Scraper", layout="wide")
st.title("🎧 Spotify Artist Insights")
st.markdown("Extracting real-time streaming and demographic data via network interception.")

query = st.text_input("Enter Artist Name or Spotify URL", placeholder="e.g. Talwiinder or https://open.spotify.com/artist/...")

if st.button("Analyze Artist"):
    if query:
        with st.spinner("Intercepting Spotify API traffic..."):
            results, cities, err = perform_search(query)
            if err:
                st.error(f"Error: {err}")
            else:
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.subheader("📊 Top 10 Tracks")
                    if results:
                        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
                    else:
                        st.warning("No track data found.")
                with col2:
                    st.subheader("📍 Top 5 Cities")
                    if cities:
                        for c in cities:
                            st.metric(label=c['City'], value=f"{c['Listeners']} listeners")
                            st.divider()
                    else:
                        st.info("City data didn't load. Try again or check if the artist has a public 'About' section.")
    else:
        st.warning("Please enter an artist name.")
