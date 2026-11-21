#!/usr/bin/env python3
"""Interactive OAuth 2.0 PKCE Bootstrap Script for Caliber MCP.

Run this script once to obtain the initial CALIBER_REFRESH_TOKEN:
    python scripts/bootstrap_caliber_auth.py
"""

import base64
import hashlib
import os
import secrets
import urllib.parse
import httpx

DEFAULT_CLIENT_ID = "caliber-mcp"
DEFAULT_AUTH_URL = "https://id.caliberstrong.com/realms/caliber/protocol/openid-connect/auth"
DEFAULT_TOKEN_URL = "https://id.caliberstrong.com/realms/caliber/protocol/openid-connect/token"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def generate_code_verifier(length: int = 64) -> str:
    """Generate a high-entropy cryptographic code verifier."""
    token = secrets.token_urlsafe(length)
    return token[:length]


def generate_code_challenge(verifier: str) -> str:
    """Calculate the S256 code challenge from the code verifier."""
    sha256_digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(sha256_digest).decode("utf-8")
    return challenge.rstrip("=")


def main():
    print("=" * 70)
    print("Caliber MCP Initial OAuth 2.0 PKCE Bootstrap Tool")
    print("=" * 70)
    print("This utility generates your initial CALIBER_REFRESH_TOKEN.\n")

    client_id = os.getenv("CALIBER_CLIENT_ID", DEFAULT_CLIENT_ID)
    auth_url = os.getenv("CALIBER_AUTH_URL", DEFAULT_AUTH_URL)
    token_url = os.getenv("CALIBER_TOKEN_URL", DEFAULT_TOKEN_URL)

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "openid offline_access",
    }
    full_auth_url = f"{auth_url}?{urllib.parse.urlencode(params)}"

    print("1. Open the following URL in your browser to sign in to Caliber:")
    print(f"\n   {full_auth_url}\n")
    print("2. Approve access. You will be provided with an authorization code")
    print("   (or redirected to a URL containing '?code=...').")
    print("-" * 70)

    auth_input = input("Paste the authorization code (or full redirect URL) here: ").strip()
    if not auth_input:
        print("Error: No code provided. Exiting.")
        return

    # Extract code if full URL was pasted
    if "code=" in auth_input:
        parsed = urllib.parse.urlparse(auth_input)
        queries = urllib.parse.parse_qs(parsed.query)
        auth_code = queries.get("code", [auth_input])[0]
    else:
        auth_code = auth_input

    print("\nExchanging authorization code for tokens...")
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": auth_code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.post(token_url, data=payload)
            res.raise_for_status()
            tokens = res.json()
    except Exception as e:
        print(f"\n[FAIL] Token exchange failed: {e}")
        return

    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")

    if not refresh_token:
        print("\n[WARNING] Server returned access_token but no refresh_token.")
        print(f"Response: {tokens}")
        return

    print("\n" + "=" * 70)
    print("SUCCESS! Initial Tokens Acquired:")
    print("=" * 70)
    print(f"\nCALIBER_REFRESH_TOKEN:\n{refresh_token}\n")
    print("=" * 70)
    print("NEXT STEPS:")
    print("1. Add this token to your local .env file:")
    print(f"   CALIBER_REFRESH_TOKEN={refresh_token}")
    print("2. Add this token to your GitHub Repository Secrets:")
    print("   Repository Settings -> Secrets and variables -> Actions -> 'CALIBER_REFRESH_TOKEN'")
    print("=" * 70)


if __name__ == "__main__":
    main()
