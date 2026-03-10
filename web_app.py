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
async def scrape_spotify_data_playwright(artist_id):
    """Scrapes both track streams and demographic data in a single browser session."""
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    demographics = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # FORCE LARGE VIEWPORT so Spotify doesn't hide the Streams column
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector('[data-testid="tracklist-row"]', timeout=10000)
            await page.wait_for_timeout(2000)
            
            # --- 1. TRACKS SCRAPING ---
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
                    streams_str = "<1000"
                    track_name = "Unknown"
                    
                    # Skip the first item (the rank number) so we don't grab it by mistake
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
                    
            # --- 2. DEMOGRAPHICS SCRAPING ---
            try:
                # Click the 'About' card image to trigger the data load
                about_card = page.locator('[data-testid="about-author-image"]')
                if await about_card.count() > 0:
                    await about_card.first.click()
                    await page.wait_for_timeout(2000) # Wait for the modal/data to load
                    
                    # Extract the raw text from the popup containing the cities
                    about_content = await page.locator('[data-testid="about-dialog"], .Root__modal-window').inner_text()
                    lines = [line.strip() for line in about_content.split('\n') if line.strip()]
                    
                    # Look for the listeners pattern (e.g., "London, GB", "166 listeners")
                    for i, line in enumerate(lines):
                        if "listeners" in line.lower() and i > 0 and any(char.isdigit() for char in line):
                            city_name = lines[i-1]
                            # Only add if it looks like a valid city format (contains letters)
                            if any(c.isalpha() for c in city_name):
                                demographics.append({
                                    "Location": city_name,
                                    "Listeners": line
                                })
                            
                            # Break out once we hit 5 cities
                            if len(demographics) >= 5:
                                break
            except Exception:
                pass
                
        except Exception:
            pass 
        finally:
            await browser.close()
            
    return tracks, demographics

def get_release_date_from_spotify(sp, artist_name, track_name):
    """Uses the official Spotify API to find the exact release date with strict filtering."""
    clean_track_name = track_name.split('(')[0].split('-')[0].strip()
    
    # Use strict Spotify search filters to prevent fuzzy matching the wrong song
    query = f"track:{clean_track_name} artist:{artist_name}"
    
    try:
        result = sp.search(q=query, type='track', limit=1)
        tracks = result.get('tracks', {}).get('items', [])
        
        if tracks:
            return tracks[0]['album']['release_date']
            
        # If strict search fails, fall back to generic search but strictly VERIFY the artist
        fallback_query = f"{artist_name} {clean_track_name}"
        fallback_result = sp.search(q=fallback_query, type='track', limit=5)
        fallback_tracks = fallback_result.get('tracks', {}).get('items', [])
        
        for track in fallback_tracks:
            for artist in track['artists']:
                if artist_name.lower() in artist['name'].lower():
                    return track['album']['release_date']
                    
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
            return None, None, f"Artist '{artist_input}' not found."
        artist_data = items[0]
        artist_name = artist_data['name']
        artist_id = artist_data['id']

    # Pull both tracks and demographics from the unified Playwright function
    top_tracks_data, demographics_data = await scrape_spotify_data_playwright(artist_id)
    
    if not top_tracks_data:
        return None, None, "Failed to pull track data from the Spotify web page."

    final_tracks_results = []
    for idx, track_info in enumerate(top_tracks_data, start=1):
        track_name = track_info['name']
        rel_date = get_release_date_from_spotify(sp, artist_name, track_name)
        
        final_tracks_results.append({
            "Track Name": track_name,
            "Total Streams": track_info['streams'],
            "Release Date": rel_date
        })

    return final_tracks_results, demographics_data, None

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
                results, demographics, error_msg = asyncio.run(perform_search(artist_input))
                
                if error_msg:
                    st.error(error_msg)
                elif results:
                    col1, col2 = st.columns([1.5, 1])
                    
                    with col1:
                        st.subheader("📊 Top 10 Tracks")
                        df_tracks = pd.DataFrame(results)
                        st.dataframe(df_tracks, use_container_width=True, hide_index=True)
                    
                    with col2:
                        st.subheader("🌍 Demographic Reach (Top 5 Cities)")
                        
                        if demographics:
                            # Render the real data matching the Spotify UI exactly
                            html_content = ""
                            for item in demographics:
                                html_content += f"""
                                <div style='margin-bottom: 16px; line-height: 1.4;'>
                                    <strong style='font-size: 16px; color: #ffffff;'>{item['Location']}</strong><br>
                                    <span style='color: #a7a7a7; font-size: 14px;'>{item['Listeners']}</span>
                                </div>
                                """
                            st.markdown(html_content, unsafe_allow_html=True)
                        else:
                            st.info("No demographic data could be extracted for this artist.")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
