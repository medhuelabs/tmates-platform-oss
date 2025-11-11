"""Gmail integration utilities for Dana agent and FastAPI routes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from urllib.parse import urlparse
import ipaddress

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

from app.auth import decrypt_token, encrypt_token
from app.db import load_user_agent_settings, save_user_agent_settings


class GmailAuthError(RuntimeError):
    """Raised when the Gmail OAuth configuration is invalid or missing."""


class GmailCredentialsError(RuntimeError):
    """Raised when stored Gmail credentials are missing or invalid."""


DEFAULT_GMAIL_SCOPES: Tuple[str, ...] = (
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "openid",
)


def _uses_private_redirect(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
        host = parsed.hostname
        if not host:
            return False
        if host in {"localhost", "127.0.0.1"}:
            return False
        ip_addr = ipaddress.ip_address(host)
        return ip_addr.is_private
    except (ValueError, ipaddress.AddressValueError):
        return False


def _build_device_id(user_id: str, nonce: str) -> str:
    digest = hashlib.sha256(f"{user_id}:{nonce}".encode("utf-8")).hexdigest()
    return digest[:32]


def _build_device_name(user_id: str) -> str:
    suffix = hashlib.md5(user_id.encode("utf-8")).hexdigest()[:6]  # noqa: S324 - non-crypto usage
    return f"Tmates Local {suffix}"


@dataclass(frozen=True, slots=True)
class GmailConfig:
    """Static OAuth configuration loaded from environment variables."""

    client_id: str
    client_secret: str
    redirect_uri: str
    token_uri: str = "https://oauth2.googleapis.com/token"
    authorization_uri: str = "https://accounts.google.com/o/oauth2/auth"
    scopes: Tuple[str, ...] = field(default=DEFAULT_GMAIL_SCOPES)

    @classmethod
    def from_env(cls) -> "GmailConfig":
        """Load OAuth configuration from environment variables."""

        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")

        if not client_id or not client_secret or not redirect_uri:
            raise GmailAuthError(
                "Google OAuth environment variables are not fully configured. "
                "Please set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI.",
            )

        raw_scopes = os.getenv("GOOGLE_GMAIL_SCOPES")
        scopes: Tuple[str, ...]
        if raw_scopes:
            parsed = tuple(scope.strip() for scope in raw_scopes.split(",") if scope.strip())
            scopes = parsed or DEFAULT_GMAIL_SCOPES
        else:
            scopes = DEFAULT_GMAIL_SCOPES

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
        )

    def to_google_client_config(self) -> Dict[str, Dict[str, Any]]:
        """Return the structure expected by google-auth for the OAuth client."""

        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": self.authorization_uri,
                "token_uri": self.token_uri,
                "redirect_uris": [self.redirect_uri],
            },
        }


@dataclass(frozen=True, slots=True)
class GmailOAuthState:
    """Encoded state passed through OAuth flows."""

    user_id: str
    nonce: str

    @classmethod
    def issue(cls, user_id: str) -> "GmailOAuthState":
        """Create a new randomised OAuth state for the provided user."""

        return cls(user_id=user_id, nonce=secrets.token_urlsafe(24))

    def encode(self) -> str:
        """Encode the state payload as a URL-safe base64 string without padding."""

        payload = json.dumps({"u": self.user_id, "n": self.nonce}).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("utf-8")
        return encoded.rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "GmailOAuthState":
        """Decode a base64 encoded state string."""

        if not value:
            raise GmailAuthError("Missing OAuth state parameter.")

        padding = "=" * (-len(value) % 4)
        try:
            raw_bytes = base64.urlsafe_b64decode(f"{value}{padding}")
            decoded = raw_bytes.decode("utf-8")
            payload = json.loads(decoded)
        except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
            raise GmailAuthError("Malformed OAuth state value.") from exc

        user_id = payload.get("u")
        nonce = payload.get("n")
        if not user_id or not nonce:
            raise GmailAuthError("Malformed OAuth state value.")

        return cls(user_id=str(user_id), nonce=str(nonce))


class GmailProfile(BaseModel):
    """User profile information returned by Gmail."""

    email_address: str = Field(..., description="Primary email address for the connected Gmail account.")
    threads_total: int = Field(default=0, description="Number of threads accessible to the user.")
    messages_total: int = Field(default=0, description="Number of messages accessible to the user.")


class EmailSummary(BaseModel):
    """Lightweight summary representation of a Gmail message."""

    id: str = Field(..., description="Unique Gmail message ID.")
    thread_id: str = Field(..., description="Thread identifier for grouping related messages.")
    subject: Optional[str] = Field(default=None, description="Subject line extracted from message headers.")
    sender: Optional[str] = Field(default=None, description="Sender display name and email.")
    recipient: Optional[str] = Field(default=None, description="Primary recipient string from headers.")
    date: Optional[str] = Field(default=None, description="RFC 2822 Date header value.")
    snippet: Optional[str] = Field(default=None, description="Short text snippet supplied by Gmail.")
    labels: List[str] = Field(default_factory=list, description="Label identifiers attached to the message.")


class EmailMessage(EmailSummary):
    """Extended representation including decoded body content."""

    plain_text_body: Optional[str] = Field(default=None, description="Decoded plain text body when available.")
    html_body: Optional[str] = Field(default=None, description="Decoded HTML body when available.")
    headers: Dict[str, str] = Field(default_factory=dict, description="Map of message headers for advanced use.")


def _decode_base64_payload(data: Optional[str]) -> Optional[str]:
    if not data:
        return None
    padding = "=" * (-len(data) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{data}{padding}")
        return decoded.decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return None


def _index_headers(headers: Optional[Iterable[Mapping[str, str]]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not headers:
        return result
    for header in headers:
        name = header.get("name")
        value = header.get("value")
        if name and isinstance(value, str):
            result[name.lower()] = value
    return result


def _extract_body(payload: Optional[Mapping[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """Extract plain text and HTML body content from a Gmail payload."""

    if not payload:
        return None, None

    mime_type = payload.get("mimeType")
    body = payload.get("body")
    parts = payload.get("parts") or []

    if mime_type == "text/plain":
        return _decode_base64_payload((body or {}).get("data")), None

    if mime_type == "text/html":
        return None, _decode_base64_payload((body or {}).get("data"))

    if "multipart" in (mime_type or "") and isinstance(parts, list):
        plain: Optional[str] = None
        html: Optional[str] = None
        for part in parts:
            p_type = part.get("mimeType")
            p_body, p_html = _extract_body(part)
            if p_type == "text/plain" and p_body:
                plain = p_body
            if p_type == "text/html" and p_html:
                html = p_html
            if plain and html:
                break
        return plain, html

    return _decode_base64_payload((body or {}).get("data")), None


class GmailService:
    """High-level Gmail helper that manages OAuth and API interactions."""

    agent_key: str = "dana"

    def __init__(self, config: Optional[GmailConfig] = None) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------
    def _config_or_raise(self) -> GmailConfig:
        if self._config is None:
            self._config = GmailConfig.from_env()
        return self._config

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_agent_settings(self, user_id: str) -> Dict[str, Any]:
        settings = load_user_agent_settings(user_id, self.agent_key)
        return dict(settings) if isinstance(settings, MutableMapping) else {}

    def _persist_settings(self, user_id: str, settings: Dict[str, Any]) -> bool:
        return save_user_agent_settings(user_id, self.agent_key, settings)

    def generate_authorization_url(self, user_id: str) -> Tuple[str, GmailOAuthState]:
        """
        Create a Google OAuth authorization URL for the specified user.

        Returns the URL and the state object used to validate the callback.
        """

        config = self._config_or_raise()
        state = GmailOAuthState.issue(user_id)

        flow = Flow.from_client_config(
            config.to_google_client_config(),
            scopes=list(config.scopes),
            state=state.encode(),
        )
        flow.redirect_uri = config.redirect_uri

        additional_params: Dict[str, str] = {}
        if _uses_private_redirect(config.redirect_uri):
            additional_params["device_id"] = _build_device_id(user_id, state.nonce)
            additional_params["device_name"] = _build_device_name(user_id)

        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            **additional_params,
        )

        current_settings = self._load_agent_settings(user_id)
        oauth_state = current_settings.get("oauth", {})
        oauth_state["pending_nonce"] = state.nonce
        oauth_state["issued_at"] = self._iso_now()
        current_settings["oauth"] = oauth_state
        self._persist_settings(user_id, current_settings)

        return authorization_url, state

    def exchange_authorization_code(
        self,
        state_value: str,
        code: str,
        expected_user_id: Optional[str] = None,
    ) -> GmailProfile:
        """
        Exchange the Google OAuth authorization code for tokens.

        The encoded state contains the user identifier and nonce, allowing us to
        locate the pending OAuth session without requiring an authenticated request.
        """

        if not code:
            raise GmailAuthError("Missing authorization code.")

        state = GmailOAuthState.decode(state_value)
        if expected_user_id and state.user_id != expected_user_id:
            raise GmailAuthError("OAuth state does not match the authenticated user.")
        config = self._config_or_raise()

        settings = self._load_agent_settings(state.user_id)
        oauth_state = settings.get("oauth") or {}
        pending_nonce = oauth_state.get("pending_nonce")
        if not pending_nonce or pending_nonce != state.nonce:
            raise GmailAuthError("OAuth state verification failed or expired.")

        flow = Flow.from_client_config(
            config.to_google_client_config(),
            scopes=list(config.scopes),
            state=state_value,
        )
        flow.redirect_uri = config.redirect_uri
        flow.fetch_token(code=code)

        credentials = flow.credentials
        profile = self._fetch_profile(credentials)

        self._store_credentials(state.user_id, credentials, profile)
        return profile

    def get_connection_status(self, user_id: str) -> Dict[str, Any]:
        """Return connection metadata for the current user."""

        settings = self._load_agent_settings(user_id)
        credentials = settings.get("credentials")
        if not credentials:
            return {
                "connected": False,
                "email": None,
                "updated_at": None,
                "scopes": None,
            }

        return {
            "connected": True,
            "email": credentials.get("email"),
            "updated_at": credentials.get("updated_at"),
            "scopes": credentials.get("scopes"),
        }

    def revoke_credentials(self, user_id: str) -> bool:
        """Remove stored Gmail credentials for a user."""

        settings = self._load_agent_settings(user_id)
        if "credentials" in settings:
            settings.pop("credentials", None)
            settings.pop("oauth", None)
            return self._persist_settings(user_id, settings)
        return True

    # ------------------------------------------------------------------
    # Gmail API helpers
    # ------------------------------------------------------------------
    def _build_credentials(self, user_id: str) -> Credentials:
        config = self._config_or_raise()
        settings = self._load_agent_settings(user_id)
        stored = settings.get("credentials") or {}

        refresh_token_enc = stored.get("refresh_token")
        if not refresh_token_enc:
            raise GmailCredentialsError(
                "No Gmail refresh token stored for this user. Please reconnect the account.",
            )

        token_enc = stored.get("access_token")
        expiry_raw = stored.get("expiry")
        scopes = stored.get("scopes") or list(config.scopes)

        refresh_token = decrypt_token(refresh_token_enc) if refresh_token_enc else ""
        token = decrypt_token(token_enc) if token_enc else None

        credentials = Credentials(
            token=token,
            refresh_token=refresh_token,
            token_uri=config.token_uri,
            client_id=config.client_id,
            client_secret=config.client_secret,
            scopes=scopes,
        )

        if expiry_raw:
            try:
                credentials.expiry = datetime.fromisoformat(str(expiry_raw))
            except ValueError:
                credentials.expiry = None

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self._store_credentials(user_id, credentials)

        if not credentials.valid:
            raise GmailCredentialsError("Stored Gmail credentials are invalid after refresh.")

        return credentials

    def _build_service(self, credentials: Credentials):
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _fetch_profile(self, credentials: Credentials) -> GmailProfile:
        service = self._build_service(credentials)
        response = service.users().getProfile(userId="me").execute()
        return GmailProfile(
            email_address=response.get("emailAddress", ""),
            threads_total=response.get("threadsTotal", 0),
            messages_total=response.get("messagesTotal", 0),
        )

    def _store_credentials(
        self,
        user_id: str,
        credentials: Credentials,
        profile: Optional[GmailProfile] = None,
    ) -> None:
        settings = self._load_agent_settings(user_id)
        stored_profile = profile or settings.get("profile")
        timestamp = self._iso_now()

        if profile:
            settings["profile"] = profile.dict()
        elif stored_profile and isinstance(stored_profile, Mapping):
            settings["profile"] = dict(stored_profile)

        settings["credentials"] = {
            "access_token": encrypt_token(credentials.token) if credentials.token else "",
            "refresh_token": encrypt_token(credentials.refresh_token or ""),
            "token_uri": credentials.token_uri,
            "scopes": list(credentials.scopes or []),
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "updated_at": timestamp,
            "email": (profile.email_address if profile else (stored_profile or {}).get("email_address")),
        }

        settings.setdefault("metadata", {})
        metadata = settings["metadata"]
        metadata["connected_at"] = metadata.get("connected_at") or timestamp
        metadata["last_updated_at"] = timestamp
        settings["metadata"] = metadata

        settings.pop("oauth", None)
        self._persist_settings(user_id, settings)

    # ------------------------------------------------------------------
    # Public Gmail operations
    # ------------------------------------------------------------------
    def list_messages(
        self,
        user_id: str,
        *,
        query: Optional[str] = None,
        label_ids: Optional[Sequence[str]] = None,
        max_results: int = 20,
    ) -> List[EmailSummary]:
        credentials = self._build_credentials(user_id)
        service = self._build_service(credentials)

        try:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    labelIds=list(label_ids) if label_ids else None,
                    maxResults=max(1, min(max_results, 50)),
                )
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - depends on external API
            raise GmailCredentialsError(f"Gmail API error while listing messages: {exc}") from exc

        message_refs = response.get("messages") or []
        summaries: List[EmailSummary] = []
        for ref in message_refs:
            message_id = ref.get("id")
            if not message_id:
                continue
            summaries.append(self.get_message_summary(credentials, message_id))
        return summaries

    def get_message_summary(self, credentials: Credentials, message_id: str) -> EmailSummary:
        service = self._build_service(credentials)
        try:
            message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"],
                )
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - depends on external API
            raise GmailCredentialsError(f"Gmail API error when fetching message metadata: {exc}") from exc

        headers = _index_headers(message.get("payload", {}).get("headers"))
        return EmailSummary(
            id=message.get("id", ""),
            thread_id=message.get("threadId", ""),
            subject=headers.get("subject"),
            sender=headers.get("from"),
            recipient=headers.get("to"),
            date=headers.get("date"),
            snippet=message.get("snippet"),
            labels=list(message.get("labelIds") or []),
        )

    def get_message(self, user_id: str, message_id: str) -> EmailMessage:
        credentials = self._build_credentials(user_id)
        service = self._build_service(credentials)
        try:
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - depends on external API
            raise GmailCredentialsError(f"Gmail API error when fetching message: {exc}") from exc

        payload = message.get("payload") or {}
        headers = _index_headers(payload.get("headers"))
        plain_text, html_body = _extract_body(payload)

        return EmailMessage(
            id=message.get("id", ""),
            thread_id=message.get("threadId", ""),
            subject=headers.get("subject"),
            sender=headers.get("from"),
            recipient=headers.get("to"),
            date=headers.get("date"),
            snippet=message.get("snippet"),
            labels=list(message.get("labelIds") or []),
            plain_text_body=plain_text,
            html_body=html_body,
            headers=headers,
        )

    def send_message(
        self,
        user_id: str,
        *,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Optional[Sequence[str]] = None,
        bcc: Optional[Sequence[str]] = None,
        reply_to: Optional[str] = None,
        html: bool = False,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        credentials = self._build_credentials(user_id)
        service = self._build_service(credentials)

        from email.message import EmailMessage as MIMEEmailMessage

        message = MIMEEmailMessage()
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        if reply_to:
            message["Reply-To"] = reply_to
        message["Subject"] = subject

        if html:
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)

        if bcc:
            # BCC recipients are included in the payload but not in headers sent to recipients.
            message["Bcc"] = ", ".join(bcc)

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        body_payload: Dict[str, Any] = {"raw": raw_message}
        if thread_id:
            body_payload["threadId"] = thread_id

        try:
            response = service.users().messages().send(userId="me", body=body_payload).execute()
            return response
        except HttpError as exc:  # pragma: no cover - depends on external API
            raise GmailCredentialsError(f"Gmail API error when sending message: {exc}") from exc
