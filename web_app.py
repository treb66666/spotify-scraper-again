import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import subprocess

# --- CACHE PLAYWRIGHT INSTALLATION ---
@st.cache_resource
def install_playwright():
    with st.spinner("Initializing headless browser backend..."):
        subprocess.run(["playwright", "install", "chromium"])

install_playwright()

def extract_values(obj, target_key):
    """Recursively pulls all values of a specified key from nested JSON payloads."""
    arr = []
    def extract(obj, arr, key):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    arr.append(v)
                elif isinstance(v, (dict, list)):
                    extract(v, arr, key)
        elif isinstance(obj, list):
            for item in obj:
                extract(item, arr, key)
        return arr
    return extract(obj, arr, target_key)

def get_spotify_streams_playwright(artist_id):
    # Using the official URL to guarantee the background API endpoints fire correctly
    url = f"https://open.spotify.com/artist/{artist_id}"
    
    captured_tracks = []
    captured_cities = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # --- THE NETWORK INTERCEPTOR ---
        def handle_response(response):
            # Target Spotify's internal 'pathfinder' GraphQL endpoints
            if "pathfinder" in response.url and response.request.method != "OPTIONS":
                try:
                    # Only parse valid JSON responses
                    if "application/json" in response.headers.get("content-type", ""):
                        data = response.json()
                        
                        # 1. Intercept Top Tracks
                        for top_tracks in extract_values(data, "topTracks"):
                            if isinstance(top_tracks, dict) and "items" in top_tracks:
                                for item in top_tracks["items"]:
                                    track_obj = item.get("track", {})
                                    name = track_obj.get("name")
                                    streams = track_obj.get("playcount")
                                    if name and streams:
                                        captured_tracks.append({"name": name, "streams": f"{int(streams):,}"})

                        # 2. Intercept City Demographics
                        for wpl in extract_values(data, "wherePeopleListen"):
                            cities_list = wpl if isinstance(wpl, list) else wpl.get("cities", [])
                            for c in cities_list:
                                city = c.get("city")
                                listeners = c.get("listeners")
                                if city and listeners:
                                    captured_cities.append({"City": city, "Listeners": f"{int(listeners):,}"})
                except:
                    pass # Ignore incomplete background requests

        page = context.new_page()
        
        # Attach the listener before navigating so we don't miss the initial load
        page.on("response", handle_response)
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            # Scroll down to trigger any lazy-loaded network requests
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1000)

            # Do a blind click on the About section just to force the API request. 
            # If it misses, it won't crash the script.
            try:
                about_section = page.locator('[data-testid="about"]')
                if about_section.count() > 0:
                    about_section.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    about_section.click(force=True)
                    page.wait_for_timeout(2500) # Give the network a moment to receive the data
            except:
                pass

        except Exception as e:
            st.error(f"Navigation issue: {e}")
        finally:
            browser.close()
            
    # --- CLEAN UP & DEDUPLICATE ---
    # The interceptor catches everything, so we filter out duplicates here
    seen_tracks = set()
    final_tracks = []
    for t in captured_tracks:
        if t['name'] not in seen_tracks:
            seen_tracks.add(t['name'])
            final_tracks.append(t)
            
    seen_cities = set()
    final_cities = []
    for c in captured_cities:
        if c['City'] not in seen_cities:
            seen_cities.add(c['City'])
            final_cities.append(c)
            
    return final_tracks[:10], final_cities[:5]

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
    with st.spinner("Intercepting internal Spotify API data..."):
        results, cities, err = perform_search(query)
        if err:
            st.error(f"Error: {err}")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader("Top 10 Tracks")
                if results:
                    st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
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
                    st.warning("Could not locate city data.")
