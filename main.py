import argparse
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests

try:
    from _version import __version__
except Exception:
    __version__ = "0.0.0"

INSTALL_URL = "https://raw.githubusercontent.com/ryangerardwilson/linkedin/main/install.sh"
LATEST_RELEASE_API = "https://api.github.com/repos/ryangerardwilson/linkedin/releases/latest"
DEFAULT_OAUTH2_TOKEN_FILE = "~/.linkedin/oauth2_token.json"
LINKEDIN_USERINFO_API = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_ME_API = "https://api.linkedin.com/v2/me"
LINKEDIN_UGC_POST_API = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_ASSETS_API = "https://api.linkedin.com/v2/assets"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def get_env(name, fallback_name=None):
    value = os.getenv(name)
    if value:
        return value
    if fallback_name:
        return os.getenv(fallback_name)
    return None


def get_user_access_token():
    env_token = (
        get_env("LINKEDIN_USER_ACCESS_TOKEN")
        or get_env("LINKEDIN_ACCESS_TOKEN")
        or get_env("LINKEDIN_BEARER_TOKEN")
    )
    if env_token:
        return env_token

    token_file = get_env("LINKEDIN_OAUTH2_TOKEN_FILE") or DEFAULT_OAUTH2_TOKEN_FILE
    token_file = os.path.expanduser(token_file)
    if not os.path.isfile(token_file):
        return None

    try:
        with open(token_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    token_obj = payload.get("token")
    if isinstance(token_obj, dict):
        expires_at = token_obj.get("expires_at")
        if isinstance(expires_at, (int, float)) and int(expires_at) <= int(time.time()):
            return None
        access_token = token_obj.get("access_token")
        if isinstance(access_token, str) and access_token.strip():
            return access_token.strip()

    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    return None


def _run_oauth2_login_helper():
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oauth2_login.py")
    if not os.path.isfile(helper):
        raise RuntimeError(f"Missing helper script: {helper}")
    return subprocess.call([sys.executable, helper])


def _retry_delay_seconds(response, attempt):
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return max(1, min(int(retry_after), 60))
        except ValueError:
            pass
    return min(2**attempt, 16)


def _request_with_retries(method, url, headers=None, retries=4, **kwargs):
    last_response = None
    for attempt in range(retries + 1):
        response = requests.request(method, url, headers=headers, timeout=45, **kwargs)
        last_response = response
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response
        if attempt == retries:
            return response
        time.sleep(_retry_delay_seconds(response, attempt))
    return last_response


def _raise_for_linkedin_error(response):
    if 200 <= response.status_code < 300:
        return
    request_id = response.headers.get("x-li-request-id") or response.headers.get("x-restli-id")
    body = response.text.strip()
    detail = f"LinkedIn API error {response.status_code}"
    if request_id:
        detail += f" (request id: {request_id})"
    if body:
        detail += f": {body}"
    raise RuntimeError(detail)


def _api_headers(access_token, json_content=True):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    api_version = get_env("LINKEDIN_API_VERSION")
    if api_version:
        headers["LinkedIn-Version"] = api_version
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def _detect_media_type(path):
    media_type, _ = mimetypes.guess_type(path)
    if media_type:
        return media_type

    extension = os.path.splitext(path)[1].lower()
    fallback_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/mp4",
    }
    media_type = fallback_types.get(extension)
    if media_type:
        return media_type

    raise RuntimeError(
        f"Unsupported media type for '{path}'. Supported types: JPG, JPEG, PNG, WEBP, GIF, MP4, MOV."
    )


def _owner_urn(access_token):
    headers = _api_headers(access_token, json_content=False)

    userinfo = _request_with_retries("GET", LINKEDIN_USERINFO_API, headers=headers)
    if 200 <= userinfo.status_code < 300:
        payload = userinfo.json()
        sub = payload.get("sub")
        if isinstance(sub, str) and sub.strip():
            value = sub.strip()
            if value.startswith("urn:li:person:"):
                return value
            return f"urn:li:person:{value}"

    me = _request_with_retries(
        "GET",
        LINKEDIN_ME_API,
        headers=headers,
        params={"projection": "(id)"},
    )
    _raise_for_linkedin_error(me)
    payload = me.json()
    member_id = payload.get("id")
    if not isinstance(member_id, str) or not member_id.strip():
        raise RuntimeError("Unable to resolve LinkedIn member id from /v2/me")
    return f"urn:li:person:{member_id.strip()}"


def _media_recipe_for_type(media_type):
    if media_type.startswith("image/"):
        return "urn:li:digitalmediaRecipe:feedshare-image", "IMAGE"
    if media_type.startswith("video/"):
        return "urn:li:digitalmediaRecipe:feedshare-video", "VIDEO"
    raise RuntimeError(f"Unsupported media type '{media_type}'")


def _register_upload(access_token, owner_urn, media_type):
    recipe, _ = _media_recipe_for_type(media_type)
    body = {
        "registerUploadRequest": {
            "recipes": [recipe],
            "owner": owner_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    response = _request_with_retries(
        "POST",
        f"{LINKEDIN_ASSETS_API}?action=registerUpload",
        headers=_api_headers(access_token),
        json=body,
    )
    _raise_for_linkedin_error(response)
    payload = response.json()
    value = payload.get("value") or {}
    upload_mechanism = value.get("uploadMechanism") or {}
    upload_request = upload_mechanism.get(
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ) or {}
    upload_url = upload_request.get("uploadUrl")
    asset = value.get("asset")

    if not upload_url or not asset:
        raise RuntimeError("LinkedIn registerUpload succeeded but uploadUrl/asset was missing")

    return upload_url, asset


def _extract_recipe_status(payload):
    if not isinstance(payload, dict):
        return None
    recipes = payload.get("recipes")
    if isinstance(recipes, list):
        for recipe in recipes:
            if isinstance(recipe, dict):
                status = recipe.get("status")
                if isinstance(status, str) and status.strip():
                    return status.strip().upper()
    status = payload.get("status")
    if isinstance(status, dict):
        value = status.get("status")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    if isinstance(status, str) and status.strip():
        return status.strip().upper()
    return None


def _asset_id_variants(asset):
    value = (asset or "").strip()
    if not value:
        return []
    variants = [value]
    if value.startswith("urn:li:digitalmediaAsset:"):
        variants.append(value.rsplit(":", 1)[-1])
    return variants


def _wait_for_asset_ready(access_token, asset, timeout=180):
    headers = _api_headers(access_token, json_content=False)
    start = time.time()
    asset_variants = _asset_id_variants(asset)
    if not asset_variants:
        raise RuntimeError("Missing LinkedIn asset id for status polling.")

    while True:
        payload = {}
        last_error = None
        for candidate in asset_variants:
            encoded_asset = urllib.parse.quote(candidate, safe="")
            response = _request_with_retries(
                "GET",
                f"{LINKEDIN_ASSETS_API}/{encoded_asset}",
                headers=headers,
                params={"projection": "(recipes,status)"},
            )
            if 200 <= response.status_code < 300:
                payload = response.json() if response.text.strip() else {}
                last_error = None
                break
            last_error = response

        if last_error is not None:
            _raise_for_linkedin_error(last_error)

        status = _extract_recipe_status(payload)
        if status == "AVAILABLE":
            return
        if status in {"INCOMPLETE", "FAILED"}:
            raise RuntimeError(f"LinkedIn asset processing failed with status: {status}")
        if time.time() - start > timeout:
            raise RuntimeError(
                "Timed out waiting for LinkedIn media processing to complete."
            )
        time.sleep(3)


def _upload_binary(upload_url, media_path, media_type, access_token, include_auth):
    with open(media_path, "rb") as handle:
        data = handle.read()

    headers = {"Content-Type": media_type}
    if include_auth:
        headers["Authorization"] = f"Bearer {access_token}"

    response = _request_with_retries("PUT", upload_url, headers=headers, data=data)
    if 200 <= response.status_code < 300:
        return

    # Some upload URLs reject auth headers while others require them; retry toggled.
    if include_auth:
        headers.pop("Authorization", None)
    else:
        headers["Authorization"] = f"Bearer {access_token}"
    response = _request_with_retries("PUT", upload_url, headers=headers, data=data)
    _raise_for_linkedin_error(response)


def upload_media(access_token, owner_urn, media_path):
    media_path = os.path.expanduser(media_path)
    if not os.path.isfile(media_path):
        raise RuntimeError(f"Media file not found: {media_path}")

    media_type = _detect_media_type(media_path)
    _, media_category = _media_recipe_for_type(media_type)

    upload_url, asset = _register_upload(access_token, owner_urn, media_type)
    # LinkedIn docs recommend no auth header for video upload URLs.
    include_auth = media_category != "VIDEO"
    _upload_binary(upload_url, media_path, media_type, access_token, include_auth=include_auth)
    _wait_for_asset_ready(access_token, asset)
    return asset, media_category


def post_linkedin(access_token, text, owner_urn, media_asset=None, media_category=None):
    share_content = {
        "shareCommentary": {"text": text},
        "shareMediaCategory": "NONE" if media_asset is None else (media_category or "IMAGE"),
    }
    if media_asset is not None:
        media_item = {
            "status": "READY",
            "media": media_asset,
        }
        if media_category == "VIDEO":
            media_item["title"] = {"text": "Video"}
        share_content["media"] = [
            media_item
        ]

    payload = {
        "author": owner_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content,
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    response = _request_with_retries(
        "POST",
        LINKEDIN_UGC_POST_API,
        headers=_api_headers(access_token),
        json=payload,
    )
    _raise_for_linkedin_error(response)

    response_payload = {}
    try:
        response_payload = response.json() if response.text.strip() else {}
    except ValueError:
        response_payload = {}

    post_id = (
        response.headers.get("x-restli-id")
        or response_payload.get("id")
        or response_payload.get("entity")
        or "unknown"
    )
    return str(post_id)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Post to LinkedIn from the command line."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Post text. If omitted, use -e to open Vim.",
    )
    parser.add_argument(
        "-m",
        "--media",
        help="Path to an image or video to attach.",
    )
    parser.add_argument(
        "-e",
        "--edit",
        action="store_true",
        help="Open Vim to compose the post.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show version and exit.",
    )
    parser.add_argument(
        "-u",
        "--upgrade",
        action="store_true",
        help="Upgrade to the latest version.",
    )
    return parser


def _version_tuple(version):
    if not version:
        return (0,)
    version = version.strip()
    if version.startswith("v"):
        version = version[1:]
    parts = []
    for segment in version.split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def _is_version_newer(candidate, current):
    cand_tuple = _version_tuple(candidate)
    curr_tuple = _version_tuple(current)
    length = max(len(cand_tuple), len(curr_tuple))
    cand_tuple += (0,) * (length - len(cand_tuple))
    curr_tuple += (0,) * (length - len(curr_tuple))
    return cand_tuple > curr_tuple


def _get_latest_version(timeout=5.0):
    try:
        request = Request(LATEST_RELEASE_API, headers={"User-Agent": "linkedin-updater"})
        with urlopen(request, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, TimeoutError):
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return None


def _run_upgrade():
    try:
        curl = subprocess.Popen(
            ["curl", "-fsSL", INSTALL_URL],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("Upgrade requires curl", file=sys.stderr)
        return 1

    try:
        bash = subprocess.Popen(["bash"], stdin=curl.stdout)
        if curl.stdout is not None:
            curl.stdout.close()
    except FileNotFoundError:
        print("Upgrade requires bash", file=sys.stderr)
        curl.terminate()
        curl.wait()
        return 1

    bash_rc = bash.wait()
    curl_rc = curl.wait()

    if curl_rc != 0:
        stderr = (
            curl.stderr.read().decode("utf-8", errors="replace") if curl.stderr else ""
        )
        if stderr:
            sys.stderr.write(stderr)
        return curl_rc

    return bash_rc


def read_from_vim():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        temp_path = tmp.name

    try:
        while True:
            editor = os.getenv("EDITOR", "vim").strip()
            editor_cmd = shlex.split(editor) if editor else ["vim"]
            if not editor_cmd:
                editor_cmd = ["vim"]
            try:
                subprocess.run(editor_cmd + [temp_path], check=False)
            except FileNotFoundError:
                raise SystemExit(f"Editor not found: {editor_cmd[0]}")
            with open(temp_path, "r", encoding="utf-8") as handle:
                text = handle.read().strip()

            if not text:
                raise SystemExit("No content; cancelled.")

            return text
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return

    if args.upgrade:
        if args.text or args.edit or args.media:
            raise SystemExit("Use -u by itself to upgrade.")

        latest = _get_latest_version()
        if latest is None:
            print(
                "Unable to determine latest version; attempting upgrade...",
                file=sys.stderr,
            )
            rc = _run_upgrade()
            sys.exit(rc)

        if (
            __version__
            and __version__ != "0.0.0"
            and not _is_version_newer(latest, __version__)
        ):
            print(f"Already running the latest version ({__version__}).")
            sys.exit(0)

        if __version__ and __version__ != "0.0.0":
            print(f"Upgrading from {__version__} to {latest}...")
        else:
            print(f"Upgrading to {latest}...")
        rc = _run_upgrade()
        sys.exit(rc)

    if args.edit:
        text_parts = list(args.text)
        media_path = args.media
        if media_path is None and text_parts and os.path.isfile(text_parts[-1]):
            media_path = text_parts.pop()
        if text_parts:
            raise SystemExit("Use either -e or provide text, not both.")
        text = read_from_vim()
    else:
        text_parts = list(args.text)
        media_path = args.media
        if media_path is None and len(text_parts) >= 2 and os.path.isfile(text_parts[-1]):
            media_path = text_parts.pop()
        text = " ".join(text_parts).strip()

    if not text:
        parser.print_help()
        return

    access_token = get_user_access_token()
    if not access_token:
        print(
            "No valid LinkedIn OAuth2 token found. Starting browser login...",
            file=sys.stderr,
        )
        rc = _run_oauth2_login_helper()
        if rc == 0:
            access_token = get_user_access_token()

    if not access_token:
        raise SystemExit(
            "Posting requires a valid LinkedIn OAuth 2.0 user access token. "
            "Automatic login did not produce a usable token."
        )

    owner_urn = _owner_urn(access_token)
    media_asset = None
    media_category = None
    if media_path:
        media_asset, media_category = upload_media(access_token, owner_urn, media_path)

    post_id = post_linkedin(
        access_token,
        text,
        owner_urn,
        media_asset=media_asset,
        media_category=media_category,
    )

    if media_asset:
        print(f"Posted to LinkedIn with media. id={post_id}")
    else:
        print(f"Posted to LinkedIn. id={post_id}")


if __name__ == "__main__":
    main()
