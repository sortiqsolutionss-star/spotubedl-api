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

def get_spotify_tokens():
    secret = decode_secret(SECRET)
    
    try:
        server_time = requests.get(
            "https://open.spotify.com/api/server-time", timeout=5
        ).json()["serverTime"]
    except Exception as e:
        import time
        print(f"Warning: Failed to fetch server time from Spotify ({e}). Falling back to local system time.")
        server_time = int(time.time())

    totp = generate_totp(secret, server_time)
    totp_server = generate_totp(secret, server_time)
    
    r = requests.get(
        "https://open.spotify.com/api/token",
        params={
            "reason": "init",
            "productType": "web-player",
            "totp": totp,
            "totpServer": totp_server,
            "totpVer": "61",
        },
    )

    token_resp = r.json()
    client_id = token_resp["clientId"]
    access_token = token_resp["accessToken"]

    client_token_resp = session.post(
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

    ).json()
    
    client_token = client_token_resp["granted_token"]["token"]
    return access_token, client_token, token_resp, client_token_resp

if __name__ == "__main__":
    access_token, client_token, token_resp, client_token_resp = get_spotify_tokens()
    print("serverTime =", token_resp.get("accessTokenExpirationTimestampMs"))
    print("totp =", token_resp.get("accessToken"))
    
    print("=== TOKEN API ===")
    print(json.dumps(token_resp, indent=2))

    print("\n=== CLIENT TOKEN API ===")
    print(json.dumps(client_token_resp, indent=2))