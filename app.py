import re
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from spotify import get_spotify_tokens
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="SpotiDownload API")

class UrlRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    selected_ids: list[str] = None

def parse_spotify_url(url):
    match_track = re.search(r'spotify:track:([a-zA-Z0-9]+)', url)
    if not match_track:
        match_track = re.search(r'track/([a-zA-Z0-9]+)', url)
        
    match_playlist = re.search(r'spotify:playlist:([a-zA-Z0-9]+)', url)
    if not match_playlist:
        match_playlist = re.search(r'playlist/([a-zA-Z0-9]+)', url)
        
    if match_track:
        return 'track', match_track.group(1)
    elif match_playlist:
        return 'playlist', match_playlist.group(1)
    return None, None

def get_track_metadata(track_id, access_token, client_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "client-token": client_token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://open.spotify.com",
        "Referer": "https://open.spotify.com/",
        "Content-Type": "application/json",
    }
    payload = {
        "operationName": "getTrack",
        "variables": {
            "uri": f"spotify:track:{track_id}"
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
            }
        }
    }
    resp = requests.post("https://api-partner.spotify.com/pathfinder/v1/query", headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch track metadata: {resp.text}")
    data = resp.json()
    track_union = data.get("data", {}).get("trackUnion", {})
    if not track_union:
        raise Exception("Track not found or invalid response")
        
    title = track_union.get("name")
    
    artists = []
    first_artist = track_union.get("firstArtist", {})
    for item in first_artist.get("items", []):
        artists.append(item.get("profile", {}).get("name"))
    other_artists = track_union.get("otherArtists", {})
    for item in other_artists.get("items", []):
        artists.append(item.get("profile", {}).get("name"))
        
    album_data = track_union.get("albumOfTrack", {})
    album_name = album_data.get("name")
    year = album_data.get("date", {}).get("year")
    
    cover_url = None
    sources = album_data.get("coverArt", {}).get("sources", [])
    if sources:
        sources = sorted(sources, key=lambda x: x.get("width", 0), reverse=True)
        cover_url = sources[0].get("url")
        
    return {
        "id": track_id,
        "title": title,
        "artists": artists,
        "album": album_name,
        "year": year,
        "cover_url": cover_url,
        "duration_ms": track_union.get("duration", {}).get("totalMilliseconds")
    }

def get_playlist_metadata(playlist_id, access_token, client_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "client-token": client_token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://open.spotify.com",
        "Referer": "https://open.spotify.com/",
        "Content-Type": "application/json",
    }
    
    offset = 0
    limit = 100
    all_tracks = []
    playlist_name = "Unknown Playlist"
    playlist_cover = None
    
    while True:
        payload = {
            "operationName": "fetchPlaylist",
            "variables": {
                "uri": f"spotify:playlist:{playlist_id}",
                "offset": offset,
                "limit": limit,
                "enableWatchFeedEntrypoint": False
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "7982b11e21535cd2594badc40030b745671b61a1fa66766e569d45e6364f3422"
                }
            }
        }
        resp = requests.post("https://api-partner.spotify.com/pathfinder/v1/query", headers=headers, json=payload)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch playlist page: {resp.text}")
        
        data = resp.json()
        playlist_data = data.get("data", {}).get("playlistV2")
        if not playlist_data:
            break
            
        if offset == 0:
            playlist_name = playlist_data.get("name")
            images = playlist_data.get("images", {}).get("items", [])
            if images:
                sources = images[0].get("sources", [])
                if sources:
                    sources = sorted(sources, key=lambda x: x.get("width", 0), reverse=True)
                    playlist_cover = sources[0].get("url")
                    
        content = playlist_data.get("content", {})
        items = content.get("items", [])
        if not items:
            break
            
        for item in items:
            item_v2 = item.get("itemV2", {})
            if item_v2.get("__typename") == "TrackResponseWrapper":
                t_data = item_v2.get("data", {})
                if t_data.get("__typename") == "Track":
                    t_uri = t_data.get("uri", "")
                    t_id = t_uri.split(":")[-1] if t_uri else ""
                    t_title = t_data.get("name")
                    t_artists = [a.get("profile", {}).get("name") for a in t_data.get("artists", {}).get("items", [])]
                    album = t_data.get("albumOfTrack", {})
                    t_album = album.get("name")
                    t_cover = None
                    sources = album.get("coverArt", {}).get("sources", [])
                    if sources:
                        sources = sorted(sources, key=lambda x: x.get("width", 0), reverse=True)
                        t_cover = sources[0].get("url")
                        
                    all_tracks.append({
                        "id": t_id,
                        "title": t_title,
                        "artists": t_artists,
                        "album": t_album,
                        "cover_url": t_cover,
                        "duration_ms": t_data.get("trackDuration", {}).get("playability", {}).get("duration") or t_data.get("duration", {}).get("totalMilliseconds") or 0
                    })
                    
        total_count = content.get("totalCount", 0)
        offset += len(items)
        if offset >= total_count or len(items) < limit:
            break
            
    return {
        "id": playlist_id,
        "name": playlist_name,
        "cover_url": playlist_cover,
        "tracks": all_tracks
    }

def get_converter_sanity_key():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://frame.y2meta-uk.com",
            "Referer": "https://frame.y2meta-uk.com/"
        }
        resp = requests.get("https://cnv.cx/v2/sanity/key", headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("key")
    except Exception as e:
        print(f"Failed to fetch sanity key: {e}")
        return None

def resolve_stream_url(t, sanity_key=None):
    import os
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'skip_download': True,
            'no_warnings': True,
        }
        query = f"scsearch:{', '.join(t['artists'])} - {t['title']}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info and info['entries']:
                video_info = info['entries'][0]
            else:
                video_info = info
            formats = video_info.get('formats', [])
            mp3_urls = [f['url'] for f in formats if f.get('url') and 'playlist.m3u8' not in f['url'] and ('.mp3' in f['url'] or 'mp3' in f.get('ext', '') or '128' in f.get('format_id', ''))]
            if mp3_urls:
                return mp3_urls[0]
            u = video_info.get('url')
            if u and 'playlist.m3u8' not in u:
                return u
            raise Exception("No direct MP3 format on SoundCloud")
    except Exception as e:
        print(f"SoundCloud stream resolution failed: {e}")
        
    try:
        search_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'skip_download': True,
            'no_warnings': True,
        }
        if os.path.exists("cookies.txt"):
            search_opts['cookiefile'] = "cookies.txt"
        
        query_yt = f"ytsearch:{', '.join(t['artists'])} - {t['title']} official audio"
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(query_yt, download=False)
            if 'entries' in info and info['entries']:
                video_info = info['entries'][0]
            else:
                video_info = info
            video_id = video_info['id']
            
        if video_id:
            if not sanity_key:
                sanity_key = get_converter_sanity_key()
            if sanity_key:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Origin": "https://frame.y2meta-uk.com",
                    "Referer": "https://frame.y2meta-uk.com/"
                }
                post_headers = headers.copy()
                post_headers.update({
                    "Content-Type": "application/x-www-form-urlencoded",
                    "accept": "*/*",
                    "key": sanity_key
                })
                post_data = {
                    "link": f"https://youtu.be/{video_id}",
                    "format": "mp3",
                    "audioBitrate": "320",
                    "videoQuality": "720",
                    "filenameStyle": "pretty",
                    "vCodec": "h264"
                }
                conv_resp = requests.post("https://cnv.cx/v2/converter", headers=post_headers, data=post_data, timeout=20)
                conv_resp.raise_for_status()
                download_url = conv_resp.json().get("url")
                if download_url:
                    return download_url
    except Exception as e:
        print(f"Converter API resolution failed: {e}")
        
    try:
        ydl_opts_fallback = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'skip_download': True,
            'no_warnings': True,
        }
        if os.path.exists("cookies.txt"):
            ydl_opts_fallback['cookiefile'] = "cookies.txt"
        query_yt = f"ytsearch:{', '.join(t['artists'])} - {t['title']} official audio"
        with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
            info = ydl.extract_info(query_yt, download=False)
            if 'entries' in info and info['entries']:
                video_info = info['entries'][0]
            else:
                video_info = info
            u = video_info['url']
            if u and 'playlist.m3u8' not in u:
                return u
    except Exception as e:
        print(f"Local YouTube resolution failed: {e}")
        
    raise Exception("Failed to resolve stream URL from all sources")

@app.post("/api/metadata")
def api_get_metadata(
    url_req: UrlRequest,
    request: Request,
    x_access_token: str | None = Header(None),
    x_client_token: str | None = Header(None)
):
    url = url_req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
        
    url_type, resource_id = parse_spotify_url(url)
    if not url_type:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL format. Must be a track or playlist link.")
        
    try:
        access_token = x_access_token
        client_token = x_client_token
        if not access_token or not client_token:
            access_token, client_token, _, _ = get_spotify_tokens()
            
        base_url = str(request.base_url).rstrip('/')
        
        if url_type == "track":
            meta = get_track_metadata(resource_id, access_token, client_token)
            meta["stream_url"] = f"{base_url}/api/stream/{meta['id']}"
            return {
                "type": "track",
                "name": meta["title"],
                "cover_url": meta["cover_url"],
                "creator": ", ".join(meta["artists"]),
                "tracks": [meta]
            }
        else:
            meta = get_playlist_metadata(resource_id, access_token, client_token)
            for track in meta["tracks"]:
                track["stream_url"] = f"{base_url}/api/stream/{track['id']}"
            return {
                "type": "playlist",
                "name": meta["name"],
                "cover_url": meta["cover_url"],
                "creator": "Spotify Playlist",
                "tracks": meta["tracks"]
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download")
def api_download(
    request: DownloadRequest,
    x_access_token: str | None = Header(None),
    x_client_token: str | None = Header(None)
):
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
        
    try:
        access_token = x_access_token
        client_token = x_client_token
        if not access_token or not client_token:
            access_token, client_token, _, _ = get_spotify_tokens()
            
        url_type, resource_id = parse_spotify_url(url)
        if not url_type:
            raise HTTPException(status_code=400, detail="Invalid Spotify URL format.")
            
        selected = request.selected_ids
        if selected:
            selected = [i for i in selected if i and i != "string" and i != "default"]
            
        tracks_to_resolve = []
        if url_type == "track":
            meta = get_track_metadata(resource_id, access_token, client_token)
            tracks_to_resolve.append(meta)
        else:
            playlist_meta = get_playlist_metadata(resource_id, access_token, client_token)
            for t in playlist_meta["tracks"]:
                if not selected or t["id"] in selected:
                    tracks_to_resolve.append(t)
                    
        if not tracks_to_resolve:
            raise HTTPException(status_code=400, detail="No tracks selected or found for download")
            
        resolved_tracks = []
        sanity_key = get_converter_sanity_key()
        
        def resolve_single(t):
            try:
                direct_url = resolve_stream_url(t, sanity_key=sanity_key)
                t_copy = t.copy()
                t_copy['direct_stream_url'] = direct_url
                return t_copy
            except Exception as err:
                import traceback
                traceback.print_exc()
                t_copy = t.copy()
                t_copy['direct_stream_url'] = f"ERROR: {str(err)}"
                return t_copy
                
        with ThreadPoolExecutor(max_workers=5) as executor:
            resolved_tracks = list(executor.map(resolve_single, tracks_to_resolve))
            
        return {
            "status": "success",
            "tracks": resolved_tracks
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream/{track_id}")
def stream_track(
    track_id: str,
    x_access_token: str | None = Header(None),
    x_client_token: str | None = Header(None)
):
    track_id = re.sub(r'[^a-zA-Z0-9]', '', track_id)
    if not track_id:
        raise HTTPException(status_code=400, detail="Invalid track ID")
        
    try:
        access_token = x_access_token
        client_token = x_client_token
        if not access_token or not client_token:
            access_token, client_token, _, _ = get_spotify_tokens()
            
        meta = get_track_metadata(track_id, access_token, client_token)
        direct_url = resolve_stream_url(meta)
        if not direct_url:
            raise Exception("Could not resolve streaming URL")
        return RedirectResponse(url=direct_url)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return RedirectResponse(url="/docs")
