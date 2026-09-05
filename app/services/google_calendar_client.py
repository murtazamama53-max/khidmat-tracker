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


class SyncTokenExpiredError(RuntimeError):
    """
    Raised when Google rejects a stored syncToken (HTTP 410 Gone) --
    happens if the token is too old, the calendar's sharing settings
    changed, or Google simply invalidated it. The only valid recovery per
    Google's own docs is to drop the token and do a full resync.
    """


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
    refresh_token: str,
    client_id: str,
    client_secret: str,
    calendar_id: str,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    sync_token: Optional[str] = None,
) -> Tuple[List[dict], Optional[str]]:
    """
    Real network call. Returns (events, next_sync_token).

    Two mutually exclusive modes, matching Google's own API constraints
    (a request cannot mix a time window with a syncToken):
      - Full sync: pass time_min/time_max (sync_token=None). Used for the
        very first sync, and as the automatic fallback whenever a stored
        sync_token is rejected as expired.
      - Incremental sync: pass sync_token (time_min/time_max=None).
        Returns only what changed (including deletions, as
        status="cancelled" items) since that token was issued.

    Either mode returns a next_sync_token (only present on the final
    page) to store for the next incremental sync. Raises
    SyncTokenExpiredError on HTTP 410, which is Google's documented
    signal that the token is no longer valid and a full resync is
    required -- callers should catch this, clear the stored token, and
    retry as a full sync.

    Uses singleEvents=True + showDeleted=True in both modes (required for
    showDeleted to appear in incremental diffs, and kept consistent
    between full and incremental requests per Google's guidance that
    changing these parameters between the two invalidates the sync
    relationship).
    """
    import google.auth.transport.requests
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    if sync_token and (time_min or time_max):
        raise ValueError("fetch_events: sync_token and time_min/time_max are mutually exclusive.")

    credentials = _build_credentials(refresh_token, client_id, client_secret)
    credentials.refresh(google.auth.transport.requests.Request())  # access token stays in-memory only

    service = build("calendar", "v3", credentials=credentials)
    events: List[dict] = []
    next_sync_token: Optional[str] = None
    page_token = None
    while True:
        list_kwargs = {
            "calendarId": calendar_id,
            "singleEvents": True,
            "showDeleted": True,
            "pageToken": page_token,
        }
        if sync_token:
            list_kwargs["syncToken"] = sync_token
        else:
            list_kwargs["timeMin"] = time_min
            list_kwargs["timeMax"] = time_max
            list_kwargs["orderBy"] = "startTime"

        try:
            result = service.events().list(**list_kwargs).execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status == 410 and sync_token:
                raise SyncTokenExpiredError(
                    "Google's sync token has expired; a full resync is required."
                ) from e
            raise

        events.extend(result.get("items", []))
        next_sync_token = result.get("nextSyncToken", next_sync_token)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return events, next_sync_token


def create_watch_channel(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    calendar_id: str,
    channel_id: str,
    channel_token: str,
    webhook_url: str,
) -> Tuple[str, Optional[int]]:
    """
    Real network call: registers a push-notification channel with Google
    for this calendar (events().watch()). Returns (resource_id,
    expiration_epoch_ms_or_None).

    channel_id is our own generated UUID identifying this channel.
    channel_token is a locally-generated secret Google will echo back on
    every notification via the X-Goog-Channel-Token header -- this is
    Google's own documented verification mechanism, not a substitute for
    it: routes/calendar.py's webhook handler rejects any request whose
    token doesn't match exactly.

    Google requires webhook_url to be a public HTTPS address; will raise
    if given a plain http:// or localhost URL (i.e. this will not work
    against a local dev server).
    """
    import google.auth.transport.requests
    from googleapiclient.discovery import build

    credentials = _build_credentials(refresh_token, client_id, client_secret)
    credentials.refresh(google.auth.transport.requests.Request())

    service = build("calendar", "v3", credentials=credentials)
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": channel_token,
    }
    result = service.events().watch(calendarId=calendar_id, body=body).execute()
    resource_id = result["resourceId"]
    expiration = result.get("expiration")  # epoch milliseconds, as a string
    return resource_id, (int(expiration) if expiration else None)


def stop_watch_channel(
    refresh_token: str, client_id: str, client_secret: str, channel_id: str, resource_id: str
) -> None:
    """Real network call: tells Google to stop sending notifications for
    this channel (used on disconnect, and when replacing an old channel
    with a freshly-renewed one). Safe to call even if the channel has
    already expired -- Google returns a 404, which is swallowed here
    since the end state (no active channel) is the same either way."""
    import google.auth.transport.requests
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    credentials = _build_credentials(refresh_token, client_id, client_secret)
    credentials.refresh(google.auth.transport.requests.Request())

    service = build("calendar", "v3", credentials=credentials)
    try:
        service.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status not in (404, 410):
            raise
