import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import json
import subprocess

# --- INITIALIZATION ---
@st.cache_resource
def install_playwright():
    """Ensures environment has browser binaries."""
    with st.spinner("Preparing headless browser..."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_insights(artist_id):
    """
    Leverages Network Interception and DOM interaction to pull
    top tracks and hidden city listener data.
    """
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Robust Cookie Integration
        if os.path.exists("cookies.json"):
            try:
                with open("cookies.json", "r", encoding="utf-8") as f:
                    cookies = json.load(f, strict=False)
                    # Normalize cookie fields for Playwright
                    for cookie in cookies:
                        if "domain" in cookie:
                            cookie["domain"] = ".spotify.com"
                        if cookie.get("sameSite") in ["no_restriction", None]:
                            cookie["sameSite"] = "None"
                    context.add_cookies(cookies)
            except Exception as e:
                st.sidebar.error(f"Cookie Load Error: {e}")

        page = context.new_page()

        # --- INTERCEPTOR LOGIC ---
        def capture_api_data(response):
            nonlocal cities_data
            if "queryArtistAbout" in response.url or "queryWherePeopleListen" in response.url:
                try:
                    payload = response.json()
                    top_cities = payload['data']['artistUnion']['stats']['topCities']['items']
                    if top_cities and not cities_data:
                        for item in top_cities[:5]:
                            cities_data.append({
                                "City": item['city'], 
                                "Listeners": f"{item['numberOfListeners']:,}"
                            })
                except:
                    pass

        page.on("response", capture_api_data)

        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            # --- SCRAPE TOP TRACKS ---
            # Try to expand list if 'See more' exists
            try:
                see_more = page.locator('button:has-text("See more")').first
                if see_more.is_visible():
                    see_more.click(force=True)
                    page.wait_for_timeout(1000)
            except: pass

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                text_content = [t.strip() for t in row.inner_text().split('\n') if t.strip()]
                if len(text_content) >= 2:
                    name = next((t for t in text_content if not t.isdigit() and t != 'E'), "Unknown")
                    streams = next((t for t in text_content if t.replace(',', '').isdigit() and len(t.replace(',', '')) >= 5), "Unknown")
                    tracks.append({'name': name, 'streams': streams})

            # --- TRIGGER ABOUT DATA ---
            # Click the About section to fire the background API call
            about_trigger = page.locator('[data-testid="artist-about-card"], section[data-testid="about"]').first
            if about_trigger.is_visible():
                about_trigger.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                about_trigger.click(force=True)
                page.wait_for_timeout(3000) # Buffer for interceptor

        except Exception as e:
            st.error(f"Interception failed: {e}")
        finally:
            browser.close()

    return tracks, cities_data

def perform_analysis(artist_query):
    """Bridge between Spotify API and Scraper."""
    # Your project credentials
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id="1d7660677d5b4567b86bfa2d730eacd7",
        client_secret="37a4d9cd968e43ad851074944d2df8e7"
    ))

    try:
        # Resolve Artist ID
        if "artist/" in artist_query:
            artist_id = artist_query.split("artist/")[1].split("?")[0]
        else:
            search_res = sp.search(q=artist_query, type='artist', limit=1)
            artist_id = search_res['artists']['items'][0]['id']
        
        artist_obj = sp.artist(artist_id)
        
        # Run the Playwright Engine
        tracks_raw, cities = get_spotify_insights(artist_id)
        
        # Build Final Results
        enriched_tracks = []
        for t in tracks_raw:
            # Quick lookup for release dates via API
            try:
                search = sp.search(q=f"artist:{artist_obj['name']} track:{t['name']}", type='track', limit=1)
                date = search['tracks']['items'][0]['album']['release_date'] if search['tracks']['items'] else "Unknown"
            except: date = "Unknown"
            
            enriched_tracks.append({
                "Track Name": t['name'], 
                "Release Date": date, 
                "Total Streams": t['streams']
            })
            
        return enriched_tracks, cities, None
    except Exception as e:
        return None, None, str(e)

# --- UI ---
st.set_page_config(page_title="Insight Scraper Pro", layout="wide")
st.title("🎧 Spotify Artist Insights")
st.markdown("---")

user_input = st.text_input("Artist Name or URL", placeholder="e.g. Talwiinder")

if st.button("Generate Report"):
    if user_input:
        with st.spinner("Processing network traffic..."):
            data, locations, error = perform_analysis(user_input)
            
            if error:
                st.error(f"Pipeline Error: {error}")
            else:
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.subheader("📊 Performance Data")
                    if data:
                        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
                    else: st.info("No track data retrieved.")
                
                with c2:
                    st.subheader("🌍 Demographic Reach")
                    if locations:
                        for loc in locations:
                            st.metric(loc['City'], f"{loc['Listeners']} listeners")
                            st.divider()
                    else:
                        st.warning("City data unavailable. Ensure your cookies are fresh.")
    else:
        st.warning("Please enter a query.")
