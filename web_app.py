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
    """Ensures playwright and chromium are installed on the environment."""
    with st.spinner("Initializing headless browser backend..."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_data_pro(artist_id):
    """
    Main scraping engine using Network Interception to capture 
    hidden demographic and track data.
    """
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        # Launching with no-sandbox for compatibility with cloud environments
        browser = p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Handle Cookies for Authentication
        if os.path.exists("cookies.json"):
            with open("cookies.json", "r") as f:
                cookies = json.load(f)
                for cookie in cookies:
                    # Clean up domain formatting for Playwright
                    if "spotify.com" in cookie.get("domain", ""):
                        cookie["domain"] = ".spotify.com"
                try:
                    context.add_cookies(cookies)
                except Exception:
                    pass
            
        page = context.new_page()

        # --- THE INTERCEPTOR ---
        # This catches the background API call containing the city data
        def handle_response(response):
            nonlocal cities_data
            if "queryArtistAbout" in response.url or "queryWherePeopleListen" in response.url:
                try:
                    data = response.json()
                    # Drill into Spotify's internal GraphQL structure
                    items = data['data']['artistUnion']['stats']['topCities']['items']
                    if items and not cities_data:
                        for item in items[:5]:
                            cities_data.append({
                                "City": item['city'], 
                                "Listeners": f"{item['numberOfListeners']:,}"
                            })
                except Exception:
                    pass

        page.on("response", handle_response)
        
        try:
            # Load the page and wait for the initial network to settle
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            # --- 1. EXTRACT TRACKS (DOM SCRAPING) ---
            # Attempt to click 'See more' if available to get full top 10
            see_more = page.locator('button', has_text="See more").first
            if see_more.is_visible():
                see_more.click(force=True)
                page.wait_for_timeout(1000)

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                lines = [l.strip() for l in row.inner_text().split('\n') if l.strip()]
                if len(lines) >= 2:
                    # Logic to identify track name vs stream count
                    name = next((l for l in lines if not l.isdigit() and l != 'E'), "Unknown")
                    streams = next((l for l in lines if l.replace(',', '').isdigit() and len(l.replace(',', '')) >= 5), "Unknown")
                    tracks.append({'name': name, 'streams': streams})

            # --- 2. TRIGGER CITY DATA (UI INTERACTION) ---
            # We scroll to and click the 'About' section to force the API call
            about_card = page.locator('section[data-testid="about"], [data-testid="artist-about-card"]').first
            if about_card.is_visible():
                about_card.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                # Clicking the card is the "Golden Trigger" for the city data packet
                about_card.click(force=True)
                # Brief pause to allow the interceptor to catch the packet
                page.wait_for_timeout(3000) 
            
            # Fallback scroll if click didn't fire
            if not cities_data:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

        except Exception as e:
            st.error(f"Extraction error: {e}")
        finally:
            browser.close()
            
    return tracks, cities_data

def get_release_date(sp, artist_name, track_name):
    """Fetches release date via official Spotipy API."""
    try:
        res = sp.search(q=f"artist:{artist_name} track:{track_name}", type='track', limit=1)
        if res['tracks']['items']:
            return res['tracks']['items'][0]['album']['release_date']
    except Exception:
        pass
    return "Unknown"

def perform_search(artist_input):
    """Coordinates Spotipy and Playwright scraping."""
    # Using your existing project credentials
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id="1d7660677d5b4567b86bfa2d730eacd7",
        client_secret="37a4d9cd968e43ad851074944d2df8e7"
    ))

    try:
        # Resolve Artist ID
        if "artist/" in artist_input:
            artist_id = artist_input.split("artist/")[1].split("?")[0]
        else:
            search = sp.search(q=artist_input, type='artist', limit=1)
            artist_id = search['artists']['items'][0]['id']
        
        artist_name = sp.artist(artist_id)['name']
        
        # Execute Scraper
        tracks_raw, cities = get_spotify_data_pro(artist_id)
        
        # Merge Scraped Data with API Data
        final_results = []
        for t in tracks_raw:
            date = get_release_date(sp, artist_name, t['name'])
            final_results.append({
                "Track Name": t['name'], 
                "Release Date": date, 
                "Total Streams": t['streams']
            })
            
        return final_results, cities, None
    except Exception as e:
        return None, None, str(e)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Spotify Insight Pro", layout="wide")
st.title("🎧 Spotify Artist Insights")
st.caption("Professional-grade data extraction via network interception.")

query = st.text_input("Enter Artist Name or Spotify URL", placeholder="e.g. Drake or https://open.spotify.com/artist/...")

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
                        st.warning("Could not retrieve track data.")
                
                with col2:
                    st.subheader("📍 Top 5 Cities")
                    if cities:
                        for c in cities:
                            st.metric(label=c['City'], value=f"{c['Listeners']} listeners")
                            st.divider()
                    else:
                        st.info("City data not found. This artist may not have enough monthly listeners to display city data, or the 'About' section failed to load.")
    else:
        st.warning("Please provide an artist name or link.")
