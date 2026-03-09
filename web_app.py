def get_spotify_streams_playwright(artist_id):
    url = f"https://open.spotify.com/artist/{artist_id}"
    tracks = []
    cities_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(user_agent="Mozilla/5.0 ...")
        
        page = context.new_page()

        # --- THE FIX: Listen for the internal Metadata API call ---
        def handle_response(response):
            if "queryArtistAbout" in response.url or "queryWherePeopleListen" in response.url:
                try:
                    json_data = response.json()
                    # Drill down into the Spotify GraphQL/Internal structure
                    # This varies slightly by region, but usually follows this path:
                    nodes = json_data['data']['artistUnion']['stats']['topCities']['items']
                    for node in nodes[:5]:
                        cities_data.append({
                            "City": node['city'], 
                            "Listeners": f"{node['numberOfListeners']:,}"
                        })
                except:
                    pass

        page.on("response", handle_response)
        
        try:
            # Go to the artist page and scroll to trigger the data load
            page.goto(url, wait_until="networkidle")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000) # Give the listener time to catch the packet
            
            # (Keep your existing Top 10 tracks scraping logic here)
            # ...
            
        except Exception as e:
            st.error(f"Scraper error: {e}")
        finally:
            browser.close()
            
    return tracks, cities_data
