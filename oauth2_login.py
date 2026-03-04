#!/usr/bin/env python3
import argparse
import json
import os
import secrets
import time
import urllib.parse
import webbrowser

import requests

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
DEFAULT_SCOPES = "r_liteprofile w_member_social"
DEFAULT_REDIRECT_URI = "https://callback-omega-one.vercel.app/callback/linkedin"
DEFAULT_TOKEN_FILE = "~/.linkedin/oauth2_token.json"


def _env(name, fallback=None):
    value = os.getenv(name)
    if value:
        return value
    if fallback:
        return os.getenv(fallback)
    return None


def _default_client_secret():
    return (
        _env("LINKEDIN_CLIENT_SECRET")
        or _env("LINKEDIN_PRIMARY_CLIENT_SECRET")
        or _env("LINKEDIN_SECONDARY_CLIENT_SECRET")
        or _env("CLIENT_SECRET")
    )


def _build_authorize_url(client_id, redirect_uri, scopes, state):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _extract_code_from_callback_input(value):
    raw = (value or "").strip()
    if not raw:
        return None, None, None
    if "://" not in raw:
        return raw, None, None
    parsed = urllib.parse.urlparse(raw)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [None])[0]
    state = (query.get("state") or [None])[0]
    error = (query.get("error") or [None])[0]
    return code, state, error


def _exchange_code_for_token(client_id, client_secret, redirect_uri, code):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"Token exchange failed ({response.status_code}): {response.text.strip()}"
        )
    token = response.json()
    expires_in = int(token.get("expires_in") or 0)
    if expires_in > 0:
        token["expires_at"] = int(time.time()) + expires_in
    return token


def _save_token(token_file, payload):
    token_path = os.path.expanduser(token_file)
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return token_path


def main():
    parser = argparse.ArgumentParser(
        description="Authenticate with LinkedIn OAuth 2.0 and save a user access token."
    )
    parser.add_argument(
        "--client-id",
        default=_env("LINKEDIN_CLIENT_ID", "CLIENT_ID"),
        help="OAuth2 Client ID (defaults to LINKEDIN_CLIENT_ID/CLIENT_ID).",
    )
    parser.add_argument(
        "--client-secret",
        default=_default_client_secret(),
        help=(
            "OAuth2 Client Secret (defaults to LINKEDIN_CLIENT_SECRET, "
            "LINKEDIN_PRIMARY_CLIENT_SECRET, LINKEDIN_SECONDARY_CLIENT_SECRET, or CLIENT_SECRET)."
        ),
    )
    parser.add_argument(
        "--redirect-uri",
        default=_env("LINKEDIN_OAUTH2_REDIRECT_URI", "REDIRECT_URI")
        or DEFAULT_REDIRECT_URI,
        help="Redirect URI configured in your LinkedIn app settings.",
    )
    parser.add_argument(
        "--scopes",
        default=_env("LINKEDIN_OAUTH2_SCOPES") or DEFAULT_SCOPES,
        help="Space-delimited scopes.",
    )
    parser.add_argument(
        "--token-file",
        default=_env("LINKEDIN_OAUTH2_TOKEN_FILE") or DEFAULT_TOKEN_FILE,
        help="Where to store token JSON.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open browser automatically; print URL only.",
    )
    args = parser.parse_args()

    if not args.client_id:
        entered = input("Enter LinkedIn OAuth2 Client ID: ").strip()
        if not entered:
            raise SystemExit(
                "Missing Client ID. Set LINKEDIN_CLIENT_ID or pass --client-id."
            )
        args.client_id = entered

    if not args.client_secret:
        entered = input("Enter LinkedIn OAuth2 Client Secret: ").strip()
        if not entered:
            raise SystemExit(
                "Missing Client Secret. Set LINKEDIN_CLIENT_SECRET or pass --client-secret."
            )
        args.client_secret = entered

    state = secrets.token_urlsafe(24)
    authorize_url = _build_authorize_url(
        args.client_id, args.redirect_uri, args.scopes, state
    )

    print("Open this URL to authorize the app:")
    print(authorize_url)
    if not args.no_open:
        webbrowser.open(authorize_url)

    pasted = input("Paste the full redirect URL (or only the 'code' value): ").strip()
    code, callback_state, callback_error = _extract_code_from_callback_input(pasted)

    if callback_error:
        raise SystemExit(f"Authorization failed: {callback_error}")

    if callback_state and callback_state != state:
        raise SystemExit("State mismatch in callback URL; aborting for safety.")

    if not code:
        raise SystemExit("No authorization code received.")

    token = _exchange_code_for_token(
        args.client_id,
        args.client_secret,
        args.redirect_uri,
        code,
    )

    payload = {
        "created_at": int(time.time()),
        "client_id": args.client_id,
        "redirect_uri": args.redirect_uri,
        "scopes": args.scopes.split(),
        "token": token,
    }
    token_path = _save_token(args.token_file, payload)

    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    print("")
    print(f"Saved OAuth2 token to: {token_path}")
    if access_token:
        print("Export for current shell:")
        print(f'export LINKEDIN_USER_ACCESS_TOKEN="{access_token}"')
    if refresh_token:
        print(f'export LINKEDIN_OAUTH2_REFRESH_TOKEN="{refresh_token}"')


if __name__ == "__main__":
    main()
