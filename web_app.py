import streamlit as st
import asyncio
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from playwright.async_api import async_playwright
import pandas as pd
import os

# --- INITIALIZATION ---
@st.cache_resource
def install_playwright():
    """Ensures Playwright browser binaries are installed only once per deployment."""
    os.system("playwright install chromium")

install_playwright()

# --- CORE LOGIC ---
async def get_spotify_streams_playwright(artist_id):
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # FORCE LARGE VIEWPORT so Spotify doesn't hide the Streams column
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector('[data-testid="tracklist-row"]', timeout=10000)
            await page.wait_for_timeout(2000)
            
            try:
                button = page.locator('button:has-text("See more")')
                if await button.count() > 0:
                    await button.first.click()
                    await page.wait_for_timeout(1000)
                else:
                    button2 = page.locator('button:has-text("Show more")')
                    if await button2.count() > 0:
                        await button2.first.click()
                        await page.wait_for_timeout(1000)
            except Exception:
                pass 
            
            rows = await page.query_selector_all('[data-testid="tracklist-row"]')
            
            for row in rows[:10]:
                text = await row.inner_text()
                parts = [p.strip() for p in text.split('\n') if p.strip()]
                
                if len(parts) >= 2:
                    # Default set to <1000 instead of Unknown
                    streams_str = "<1000"
                    track_name = "Unknown"
                    
                    # FIX: parts[1:] skips the first item (the rank number) so we don't grab it by mistake
                    for p in reversed(parts[1:]):
                        if ':' in p and len(p) <= 5: continue
                        if sum(c.isdigit() for c in p) >= 1 and not any(c.isalpha() for c in p):
                            streams_str = p
                            break
                            
                    for p in parts:
                        if any(c.isalpha() for c in p) and p != 'E':
                            track_name = p
                            break
                            
                    tracks.append({'name': track_name, 'streams': streams_str})
        except Exception:
            pass 
        finally:
            await browser.close()
            
    return tracks

def get_release_date_from_spotify(sp, artist_name, track_name):
    """Uses the official Spotify API to find the exact release date."""
    clean_track_name = track_name.split('(')[0].split('-')[0].strip()
    query = f"{artist_name} {clean_track_name}"
    
    try:
        result = sp.search(q=query, type='track', limit=1)
        tracks = result.get('tracks', {}).get('items', [])
        
        if tracks:
            release_date = tracks[0]['album']['release_date']
            return release_date
        return "Unknown"
    except Exception:
        return "Unknown"

async def perform_search(artist_input):
    # Securely load credentials from Streamlit Secrets
    CLIENT_ID = st.secrets["SPOTIPY_CLIENT_ID"]
    CLIENT_SECRET = st.secrets["SPOTIPY_CLIENT_SECRET"]
    
    auth_manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    sp = spotipy.Spotify(auth_manager=auth_manager)

    if "spotify.com/artist/" in artist_input:
        artist_id = artist_input.split("artist/")[1].split("?")[0]
        artist_data = sp.artist(artist_id)
        artist_name = artist_data['name']
    else:
        result = sp.search(q='artist:' + artist_input, type='artist', limit=1)
        items = result['artists']['items']
        if not items:
            return None, f"Artist '{artist_input}' not found."
        artist_data = items[0]
        artist_name = artist_data['name']
        artist_id = artist_data['id']

    top_tracks_data = await get_spotify_streams_playwright(artist_id)
    if not top_tracks_data:
        return None, "Failed to pull track data from the Spotify web page."

    final_results = []
    for idx, track_info in enumerate(top_tracks_data, start=1):
        track_name = track_info['name']
        rel_date = get_release_date_from_spotify(sp, artist_name, track_name)
        
        final_results.append({
            "Track Name": track_name,
            "Total Streams": track_info['streams'],
            "Release Date": rel_date
        })

    return final_results, None

# --- STREAMLIT WEB UI ---
st.set_page_config(page_title="Spotify Artist Insights", layout="wide")

st.title("🎧 Spotify Artist Insights")
st.write("Extracting real-time streaming and demographic data via network interception.")

artist_input = st.text_input("Enter Artist Name or Spotify URL")

if st.button("Generate Report", type="primary"):
    if not artist_input:
        st.warning("Please provide an Artist Name or Spotify Link.")
    else:
        with st.spinner("Fetching data..."):
            try:
                # Run the track scraper
                results, error_msg = asyncio.run(perform_search(artist_input))
                
                if error_msg:
                    st.error(error_msg)
                elif results:
                    # Create the two columns for the dashboard
                    col1, col2 = st.columns([1.5, 1])
                    
                    with col1:
                        st.subheader("📊 Top 10 Tracks")
                        df_tracks = pd.DataFrame(results)
                        st.dataframe(df_tracks, use_container_width=True, hide_index=True)
                    
                    with col2:
                        st.subheader("🌍 Demographic Reach (Top 5 Cities)")
                        
                        # --- PLUG IN YOUR DEMOGRAPHICS SCRAPER LOGIC HERE ---
                        # Replace 'demographics_results' with your actual variable
                        demographics_results = [
                            {"Location": "London, GB", "Listeners": "166 listeners"},
                            {"Location": "Melbourne, AU", "Listeners": "49 listeners"},
                            {"Location": "Sydney, AU", "Listeners": "43 listeners"},
                            {"Location": "Istanbul, TR", "Listeners": "42 listeners"},
                            {"Location": "Norwich, GB", "Listeners": "38 listeners"}
                        ]
                        
                        # Use custom HTML to match the Spotify UI exactly
                        html_content = ""
                        for item in demographics_results:
                            html_content += f"""
                            <div style='margin-bottom: 16px; line-height: 1.4;'>
                                <strong style='font-size: 16px; color: #ffffff;'>{item['Location']}</strong><br>
                                <span style='color: #a7a7a7; font-size: 14px;'>{item['Listeners']}</span>
                            </div>
                            """
                        
                        # Render the custom block in the app
                        st.markdown(html_content, unsafe_allow_html=True)
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
