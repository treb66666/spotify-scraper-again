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
    """Ensures environment has browser binaries."""
    with st.spinner("Initializing headless browser backend..."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def get_spotify_insights(artist_id):
    # Using the exact open.spotify.com domain
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # LOAD AND CLEAN COOKIES
        if os.path.exists("cookies.json"):
            try:
                with open("cookies.json", "r", encoding="utf-8") as f:
                    cookies = json.load(f, strict=False)
                    for cookie in cookies:
                        # Clean domains to strictly match Spotify
                        if "spotify" in cookie.get("domain", ""):
                            cookie["domain"] = ".spotify.com"
                        if cookie.get("sameSite") in ["no_restriction", None, "unspecified", "None"]:
                            cookie["sameSite"] = "None"
                    context.add_cookies(cookies)
            except Exception as e:
                st.sidebar.error(f"Cookie Load Error: {e}")

        page = context.new_page()

        # --- ADVANCED INTERCEPTOR LOGIC ---
        def capture_api_data(response):
            nonlocal cities_data
            # Broaden interceptor to catch any potential GraphQL or JSON payload
            if "json" in response.headers.get("content-type", "") or "graphql" in response.url.lower() or "query" in response.url.lower():
                try:
                    payload = response.json()
                    # Safely walk down the tree to bypass dynamic URL naming
                    if isinstance(payload, dict) and 'data' in payload:
                        top_cities = payload.get('data', {}).get('artistUnion', {}).get('stats', {}).get('topCities', {}).get('items', [])
                        
                        if top_cities and not cities_data:
                            for item in top_cities[:5]:
                                cities_data.append({
                                    "City": item.get('city', 'Unknown'), 
                                    "Listeners": f"{item.get('numberOfListeners', 0):,}"
                                })
                except Exception:
                    pass

        page.on("response", capture_api_data)

        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            # --- SCRAPE TOP TRACKS ---
            try:
                see_more = page.locator('button:has-text("See more")').first
                if see_more.is_visible(timeout=2000):
                    see_more.click(force=True)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            for row in rows[:10]:
                text_content = [t.strip() for t in row.inner_text().split('\n') if t.strip()]
                if len(text_content) >= 2:
                    name = next((t for t in text_content if not t.isdigit() and t != 'E'), "Unknown")
                    streams = next((t for t in text_content if t.replace(',', '').isdigit() and len(t.replace(',', '')) >= 5), "Unknown")
                    tracks.append({'name': name, 'streams': streams})

            # --- TRIGGER ABOUT DATA ---
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(1000)

            about_trigger = page.locator('[data-testid="artist-about-card"], section[data-testid="about"]').first
            if about_trigger.is_visible(timeout=5000):
                about_trigger.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                about_trigger.click(force=True)
                page.wait_for_timeout(2000) 
                
                # --- SCROLL INSIDE THE MODAL ---
                dialog = page.locator('[role="dialog"]')
                if dialog.is_visible():
                    # Force aggressive JS scroll to the bottom of the modal specifically
                    dialog.evaluate("node => node.scrollTo(0, node.scrollHeight)")
                    page.wait_for_timeout(3000) # Buffer for interceptor

                # --- DOM SCRAPING FALLBACK ---
                if not cities_data and dialog.is_visible():
                    lines = [l.strip() for l in dialog.inner_text().split('\n') if l.strip()]
                    for i, line in enumerate(lines):
                        if "listeners" in line.lower() and "monthly" not in line.lower() and i > 0:
                            city = lines[i-1]
                            count = line.replace("listeners", "").replace(",", "").strip()
                            if count.isdigit() and len(cities_data) < 5:
                                cities_data.append({"City": city, "Listeners": f"{int(count):,}"})

            if not cities_data:
                page.screenshot(path="debug_screenshot.png")
            elif os.path.exists("debug_screenshot.png"):
                os.remove("debug_screenshot.png")

        except Exception as e:
            st.error(f"Playwright Execution Error: {e}")
        finally:
            browser.close()

    return tracks, cities_data

def perform_analysis(artist_query):
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id="1d7660677d5b4567b86bfa2d730eacd7",
        client_secret="37a4d9cd968e43ad851074944d2df8e7"
    ))

    try:
        if "artist/" in artist_query:
            artist_id = artist_query.split("artist/")[1].split("?")[0]
        else:
            search_res = sp.search(q=artist_query, type='artist', limit=1)
            if not search_res['artists']['items']:
                return None, None, "Artist not found."
            artist_id = search_res['artists']['items'][0]['id']
        
        artist_obj = sp.artist(artist_id)
        
        tracks_raw, cities = get_spotify_insights(artist_id)
        
        enriched_tracks = []
        for t in tracks_raw:
            try:
                search = sp.search(q=f"artist:{artist_obj['name']} track:{t['name']}", type='track', limit=1)
                date = search['tracks']['items'][0]['album']['release_date'] if search['tracks']['items'] else "Unknown"
            except Exception:
                date = "Unknown"
            
            enriched_tracks.append({
                "Track Name": t['name'], 
                "Release Date": date, 
                "Total Streams": t['streams']
            })
            
        return enriched_tracks, cities, None
    except Exception as e:
        return None, None, str(e)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Spotify Artist Insights", layout="wide")
st.title("🎧 Spotify Artist Insights")
st.markdown("Extracting real-time streaming and demographic data via network interception.")

user_input = st.text_input("Enter Artist Name or Spotify URL", placeholder="e.g. Drake")

if st.button("Generate Report"):
    if user_input:
        with st.spinner("Intercepting Spotify API traffic..."):
            data, locations, error = perform_analysis(user_input)
            
            if error:
                st.error(f"Error: {error}")
            else:
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.subheader("📊 Top 10 Tracks")
                    if data:
                        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
                    else: 
                        st.warning("No track data retrieved.")
                
                with col2:
                    st.subheader("🌍 Demographic Reach (Top 5 Cities)")
                    if locations:
                        for loc in locations:
                            st.metric(loc['City'], f"{loc['Listeners']} listeners")
                            st.divider()
                    else:
                        st.warning("City data unavailable. Checking debug info...")
                        if os.path.exists("debug_screenshot.png"):
                            st.image("debug_screenshot.png", caption="What the scraper saw (Check if logged out or blocked)")
    else:
        st.warning("Please enter an artist name or link.")
