"""Google OAuth.

**The 7-day trap.** A Google Cloud project whose OAuth consent screen is in
"Testing" status issues refresh tokens that expire after seven days. Peter would
then stop reading your calendar every week until you re-authorised, usually
discovered at the worst moment.

Calendar and Tasks scopes are *sensitive*, not *restricted* — unlike Gmail, they
do not require a third-party security audit. So the fix is to move the consent
screen to **"In Production"**. You will see an "unverified app" warning once;
click Advanced, then continue. After that, refresh tokens persist indefinitely.

This module treats an expired refresh token as a first-class, expected outcome:
it raises an `AuthError` naming the exact command to fix it, rather than a
`RefreshError` traceback that means nothing spoken aloud.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError

log = logging.getLogger(__name__)

_lock = threading.Lock()

REAUTH_HINT = "Run: python -m peter.main --google-auth"


def _client_config(config: Config) -> dict:
    """The installed-app client blob, built from .env instead of a JSON file.

    Google's own docs hand you a `credentials.json` to drop in the project.
    Assembling it from environment variables keeps the client secret out of the
    working tree, where it would eventually be committed by accident.
    """
    return {
        "installed": {
            "client_id": config.secrets.google_client_id,
            "client_secret": config.secrets.google_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def load_credentials(config: Config, interactive: bool = False):
    """Return valid Google credentials, refreshing or re-authorising as needed.

    Args:
        config: The loaded configuration.
        interactive: True to open a browser when no valid token exists. False
            (the default) raises instead — a background job must never block on
            a consent screen nobody is sitting in front of.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not config.secrets.has_google:
        raise AuthError(
            "Google OAuth client credentials are missing",
            service="google",
            user_action=(
                "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in "
                ".env. Create them at console.cloud.google.com under "
                "APIs & Services, Credentials, OAuth client ID, Desktop app."
            ),
        )

    scopes = list(config.integrations.google.scopes)
    token_path = config.google_token_path

    with _lock:
        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), scopes)
            except (ValueError, json.JSONDecodeError) as exc:
                log.warning("stored Google token is unreadable (%s); discarding", exc)
                token_path.unlink(missing_ok=True)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save(token_path, creds)
                return creds
            except RefreshError as exc:
                # This is the 7-day expiry, or a revoked grant.
                log.info("Google refresh token rejected: %s", exc)
                token_path.unlink(missing_ok=True)
                if not interactive:
                    raise AuthError(
                        "Google authorisation expired",
                        service="google",
                        user_action=(
                            f"{REAUTH_HINT}. To stop this recurring every 7 days, "
                            "set your OAuth consent screen to 'In Production' in "
                            "the Google Cloud console."
                        ),
                    ) from exc

        if not interactive:
            raise AuthError(
                "Google account is not authorised yet",
                service="google",
                user_action=REAUTH_HINT,
            )

        return _run_flow(config, scopes, token_path)


def _run_flow(config: Config, scopes: list[str], token_path: Path):
    """Open the consent screen in a browser and store the resulting token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    try:
        flow = InstalledAppFlow.from_client_config(_client_config(config), scopes)
        creds = flow.run_local_server(
            port=config.integrations.google.oauth_port,
            prompt="consent",          # forces a refresh_token to be issued
            access_type="offline",
            authorization_prompt_message=(
                "\nOpening your browser to authorise Google Calendar and Tasks.\n"
                "If you see 'Google hasn't verified this app', click Advanced, "
                "then 'Go to Peter (unsafe)'. That warning is expected for a "
                "personal project.\n"
            ),
            success_message="Peter is authorised. You can close this tab.",
        )
    except Exception as exc:
        raise IntegrationError(
            f"Google authorisation failed: {exc}",
            service="google",
            recoverable=False,
            user_action="Check the OAuth client is of type 'Desktop app'.",
        ) from exc

    _save(token_path, creds)
    log.info("Google authorisation stored at %s", token_path)
    return creds


def _save(path: Path, creds) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        # The token grants access to your calendar — do not leave it group- or
        # world-readable on multi-user machines.
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def build_service(config: Config, api: str, version: str):
    """Construct a googleapiclient service for `api`."""
    from googleapiclient.discovery import build

    creds = load_credentials(config)
    return build(api, version, credentials=creds, cache_discovery=False)


def authorise_interactively(config: Config) -> str:
    """Entry point for `--google-auth`."""
    creds = load_credentials(config, interactive=True)
    return "Google authorisation complete." if creds else "Authorisation failed."
