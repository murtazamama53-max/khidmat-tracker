"""
Thin wrapper around google-auth-oauthlib and googleapiclient. This is the
ONLY module in the app that talks to Google's servers -- every other
calendar module (calendar_sync.py, routes/calendar.py) depends on the
functions here, never on the Google SDKs directly. That isolation is
what lets tests exercise the full sync/reconciliation flow with realistic
fake event data instead of live network calls.

OAuth flow:
  1. build_flow() -- constructs a google_auth_oauthlib Flow from the
     app's configured client ID/secret/redirect URI (env vars). This is
     pure local URL construction, no network call.
  2. get_authorization_url(flow) -- returns the URL to send the owner to.
     Also pure/local.
  3. exchange_code_for_refresh_token(flow, authorization_response_url) --
     the one step that actually talks to Google (POST to the token
     endpoint). Returns (refresh_token, google_email).
  4. fetch_events(refresh_token, calendar_id, time_min, time_max) --
     refreshes an access token from the refresh token, then calls the
     Calendar API's events().list(). Also real network I/O.

Access tokens are only ever held in memory for the duration of a single
request; only the refresh token is persisted, and only encrypted
(services/token_crypto.py). Nothing here is ever returned to a template
or JSON response (blueprint section 12: "Never expose access tokens to
the frontend").
"""
from typing import List, Optional, Tuple

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "openid", "https://www.googleapis.com/auth/userinfo.email"]


class CalendarConfigError(RuntimeError):
    """Raised when GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI aren't configured."""


def _client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def build_flow(client_id: str, client_secret: str, redirect_uri: str):
    """Pure local construction -- no network call."""
    if not client_id or not client_secret or not redirect_uri:
        raise CalendarConfigError(
            "Google Calendar isn't configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "and GOOGLE_REDIRECT_URI in your .env file (see README for the Google Cloud setup steps)."
        )
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri
    )


def get_authorization_url(flow) -> Tuple[str, str]:
    """Pure local construction -- no network call. Returns (auth_url, state)."""
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return auth_url, state


def exchange_code_for_refresh_token(flow, authorization_response_url: str) -> Tuple[str, Optional[str]]:
    """
    Real network call: exchanges the authorization code for tokens.
    Returns (refresh_token, google_email_or_None). Raises if Google
    didn't grant a refresh token (e.g. re-consent needed).
    """
    flow.fetch_token(authorization_response=authorization_response_url)
    credentials = flow.credentials
    if not credentials.refresh_token:
        raise CalendarConfigError(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and try connecting again."
        )

    email = None
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token

        if credentials.id_token:
            info = id_token.verify_oauth2_token(credentials.id_token, google.auth.transport.requests.Request())
            email = info.get("email")
    except Exception:
        pass  # email is a nice-to-have for display only; never block the connection on it

    return credentials.refresh_token, email


def _build_credentials(refresh_token: str, client_id: str, client_secret: str):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def fetch_events(
    refresh_token: str, client_id: str, client_secret: str, calendar_id: str, time_min: str, time_max: str
) -> List[dict]:
    """
    Real network call. Returns raw Google Calendar API event dicts
    (the same shape services/calendar_sync.py expects), for the given
    ISO8601 time window. Uses singleEvents=True so recurring events are
    already expanded into individual occurrences with their own IDs.
    """
    import google.auth.transport.requests
    from googleapiclient.discovery import build

    credentials = _build_credentials(refresh_token, client_id, client_secret)
    credentials.refresh(google.auth.transport.requests.Request())  # access token stays in-memory only

    service = build("calendar", "v3", credentials=credentials)
    events: List[dict] = []
    page_token = None
    while True:
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                showDeleted=True,
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return events
