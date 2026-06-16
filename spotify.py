import hmac
import hashlib
import struct
import requests
import uuid
import json

session = requests.Session()

SECRET = ',7/*F("rLJ2oxaKL^f+E1xvP@N'

def decode_secret(secret):
    arr = bytes(
        ord(ch) ^ ((i % 33) + 9)
        for i, ch in enumerate(secret)
    )
    hex_str = ''.join(str(x) for x in arr).encode().hex()
    return bytes.fromhex(hex_str)


def generate_totp(secret_bytes, timestamp, digits=6, period=30):
    counter = int(timestamp // period)
    msg = struct.pack(">Q", counter)
    h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (
        ((h[offset] & 0x7F) << 24)
        | ((h[offset + 1] & 0xFF) << 16)
        | ((h[offset + 2] & 0xFF) << 8)
        | (h[offset + 3] & 0xFF)
    )
    return str(code % (10**digits)).zfill(digits)


secret = decode_secret(SECRET)

def fetch_free_proxies():
    try:
        url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=yes&anonymity=all"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            proxies = [line.strip() for line in r.text.strip().split("\n") if line.strip()]
            return proxies
    except Exception as e:
        print(f"Failed to fetch proxies from proxyscrape: {e}")
    return []

def _fetch_tokens_with_session(s, secret):
    try:
        server_time = s.get(
            "https://open.spotify.com/api/server-time", timeout=5
        ).json()["serverTime"]
    except Exception as e:
        import time
        print(f"Warning: Failed to fetch server time from Spotify ({e}). Falling back to local system time.")
        server_time = int(time.time())

    totp = generate_totp(secret, server_time)
    totp_server = generate_totp(secret, server_time)
    
    r = s.get(
        "https://open.spotify.com/api/token",
        params={
            "reason": "init",
            "productType": "web-player",
            "totp": totp,
            "totpServer": totp_server,
            "totpVer": "61",
        },
        timeout=5
    )
    r.raise_for_status()

    token_resp = r.json()
    client_id = token_resp["clientId"]
    access_token = token_resp["accessToken"]

    client_token_resp = s.post(
        "https://clienttoken.spotify.com/v1/clienttoken",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://open.spotify.com",
            "referer": "https://open.spotify.com/",
            "user-agent": "Mozilla/5.0",
        },
        json={
            "client_data": {
                "client_version": "1.2.92.50.g97692e81",
                "client_id": client_id,
                "js_sdk_data": {
                    "device_brand": "unknown",
                    "device_model": "unknown",
                    "os": "linux",
                    "os_version": "unknown",
                    "device_id": str(uuid.uuid4()),
                    "device_type": "computer",
                },
            }
        },
        timeout=5
    ).json()
    
    client_token = client_token_resp["granted_token"]["token"]
    return access_token, client_token, token_resp, client_token_resp

def get_spotify_tokens():
    secret = decode_secret(SECRET)
    
    try:
        return _fetch_tokens_with_session(requests.Session(), secret)
    except Exception as e:
        print(f"Direct connection failed: {e}. Trying via free proxies...")
        
    proxy_ips = fetch_free_proxies()
    print(f"Found {len(proxy_ips)} potential proxies. Testing...")
    
    for proxy in proxy_ips[:15]:
        s = requests.Session()
        s.proxies = {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}"
        }
        try:
            print(f"Trying proxy: {proxy}")
            return _fetch_tokens_with_session(s, secret)
        except Exception as e:
            print(f"Proxy {proxy} failed: {e}")
            continue
            
    raise Exception("Failed to retrieve Spotify tokens after trying direct connection and multiple proxies.")

if __name__ == "__main__":
    access_token, client_token, token_resp, client_token_resp = get_spotify_tokens()
    print("serverTime =", token_resp.get("accessTokenExpirationTimestampMs"))
    print("totp =", token_resp.get("accessToken"))
    
    print("=== TOKEN API ===")
    print(json.dumps(token_resp, indent=2))

    print("\n=== CLIENT TOKEN API ===")
    print(json.dumps(client_token_resp, indent=2))