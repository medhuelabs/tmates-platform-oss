"""Pydantic schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class BillingLimits(BaseModel):
    max_agents: Optional[int] = None
    monthly_actions: Optional[int] = None
    monthly_tokens: Optional[int] = None
    max_members: Optional[int] = None


class BillingUsage(BaseModel):
    actions_used: int = 0
    actions_quota: Optional[int] = None
    actions_remaining: Optional[int] = None
    tokens_used: int = 0
    tokens_quota: Optional[int] = None
    tokens_remaining: Optional[int] = None


class BillingPlanResponse(BaseModel):
    plan_key: str
    plan_name: str
    category: Optional[str] = None
    status: str
    billing_interval: str
    provider: str = Field(default="stripe")
    limits: BillingLimits
    usage: BillingUsage
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    cancel_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    billing_enabled: bool = False


class CheckoutSessionRequest(BaseModel):
    plan_key: str = Field(..., min_length=1)
    billing_interval: str = Field(default="monthly")
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None
    quantity: int = Field(default=1, ge=1)

    @field_validator("billing_interval", mode="before")
    @classmethod
    def normalize_interval(cls, value: Optional[str]) -> str:
        candidate = (value or "monthly").strip().lower()
        if candidate in {"monthly"}:
            return "monthly"
        if candidate in {"annual", "annually", "yearly"}:
            return "annual"
        raise ValueError("billing_interval must be monthly or annual")


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


class BillingPortalRequest(BaseModel):
    return_url: Optional[str] = None


class BillingPortalResponse(BaseModel):
    url: str


class PublicPricingPlan(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    category: str
    stripe_product_id: Optional[str] = None
    stripe_price_monthly_id: Optional[str] = None
    stripe_price_yearly_id: Optional[str] = None
    limits: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class PublicPricingResponse(BaseModel):
    plans: List[PublicPricingPlan]


class OrganizationSummary(BaseModel):
    id: str
    name: Optional[str] = None
    type: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class SubscriptionSummary(BaseModel):
    organization_id: str
    plan_key: Optional[str] = None
    status: Optional[str] = None
    billing_interval: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    provider: Optional[str] = None


class BillingOnboardingRequest(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    organization_name: Optional[str] = None
    plan_key: Optional[str] = None
    billing_interval: str = Field(default="monthly")

    @field_validator("billing_interval", mode="before")
    @classmethod
    def normalize_billing_interval(cls, value: Optional[str]) -> str:
        candidate = (value or "monthly").strip().lower()
        if candidate in {"annual", "annually", "yearly"}:
            return "annual"
        return "monthly"


class BillingOnboardingResponse(BaseModel):
    profile: UserProfile
    organization: Optional[OrganizationSummary] = None
    subscription: Optional[SubscriptionSummary] = None


class AgentInfo(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None


class AgentJobCreate(BaseModel):
    agent_key: str = Field(..., min_length=1)
    task: Optional[str] = None
    cli_args: Dict[str, Any] = Field(default_factory=dict)
    env_overrides: Dict[str, str] = Field(default_factory=dict)
    extra_args: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task", mode="before")
    @classmethod
    def normalize_task(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
        return None

    @field_validator("extra_args", mode="before")
    @classmethod
    def ensure_extra_args_are_strings(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]



class AgentJob(BaseModel):
    id: str
    auth_user_id: Optional[str] = None
    agent_key: str
    status: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    progress: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @field_validator("created_at", "updated_at", "started_at", "finished_at", mode="before")
    @classmethod
    def parse_datetime(cls, value: Any) -> Optional[datetime]:
        if value in (None, "", 0):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        if isinstance(value, str):
            try:
                # Support both ISO strings and legacy local (SQLite) timestamps
                normalised = value.replace(" ", "T")
                return datetime.fromisoformat(normalised)
            except ValueError:
                return None
        return None

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "AgentJob":
        return cls.parse_obj(record)


class PinboardAttachment(BaseModel):
    url: str
    label: Optional[str] = None
    type: Optional[str] = None


class PinboardSource(BaseModel):
    url: str
    label: Optional[str] = None


PinboardPriority = Literal["low", "normal", "high", "urgent"]


class PinboardPost(BaseModel):
    id: str
    title: str
    slug: str
    excerpt: Optional[str] = None
    content_md: Optional[str] = None
    author_agent_key: Optional[str] = None
    author_display: Optional[str] = None
    cover_url: Optional[str] = None
    priority: PinboardPriority = "normal"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    attachments: List[PinboardAttachment] = Field(default_factory=list)
    sources: List[PinboardSource] = Field(default_factory=list)


class Teammate(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    detail_url: Optional[str] = None
    settings_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatMessageAttachment(BaseModel):
    uri: str
    type: Optional[str] = None
    name: Optional[str] = None
    relative_path: Optional[str] = None
    download_url: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[ChatMessageAttachment] = Field(default_factory=list)
    session_id: Optional[str] = None  # For agent conversation memory


class ChatThreadSummary(BaseModel):
    id: str
    title: str
    kind: str
    agent_keys: List[str] = Field(default_factory=list)
    last_message_preview: Optional[str] = None
    last_activity: Optional[datetime] = None
    unread_count: int = 0
    active_session_id: Optional[str] = None  # Current conversation session


class ChatThread(ChatThreadSummary):
    messages: List[ChatMessage] = Field(default_factory=list)


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[ChatMessageAttachment] = Field(default_factory=list)
    session_id: Optional[str] = None  # Optional session to continue


class ChatSessionResetResponse(BaseModel):
    session_id: str
    message: ChatMessage


class FileEntry(BaseModel):
    name: str
    relative_path: str
    size: int
    size_display: str
    modified: datetime
    modified_display: str
    modified_iso: Optional[str] = None
    download_url: str


class FileListing(BaseModel):
    files: List[FileEntry] = Field(default_factory=list)
    total_count: int = 0
    total_size: int = 0
    total_size_display: Optional[str] = None
    has_more: bool = False
    limit: int = 0


class UploadedFile(BaseModel):
    file_name: str
    relative_path: str
    download_url: str
    mime_type: str
    size: int


class FileUploadResponse(BaseModel):
    success: bool = True
    file: UploadedFile


class UserProfile(BaseModel):
    id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None


class UserProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[AnyHttpUrl] = None
    email: Optional[EmailStr] = None

    @field_validator("display_name", mode="before")
    @classmethod
    def normalise_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def normalise_avatar(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @model_validator(mode="after")
    def ensure_payload(self) -> "UserProfileUpdateRequest":
        if (
            self.display_name is None
            and self.avatar_url is None
            and self.email is None
        ):
            raise ValueError("At least one field must be provided")
        return self


class MobileSettings(BaseModel):
    allow_notifications: bool = True
    mentions: bool = True
    direct_messages: bool = True
    team_messages: bool = True
    usage_analytics: bool = True
    crash_reports: bool = True
    theme_preference: Literal["system", "light", "dark"] = "system"


class MobileSettingsUpdateRequest(BaseModel):
    allow_notifications: Optional[bool] = None
    mentions: Optional[bool] = None
    direct_messages: Optional[bool] = None
    team_messages: Optional[bool] = None
    usage_analytics: Optional[bool] = None
    crash_reports: Optional[bool] = None
    theme_preference: Optional[Literal["system", "light", "dark"]] = None

    @field_validator("theme_preference", mode="before")
    @classmethod
    def normalise_theme(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        candidate = str(value).strip().lower()
        if candidate in {"system", "light", "dark"}:
            return candidate
        raise ValueError("theme_preference must be system, light, or dark")

    @model_validator(mode="after")
    def ensure_payload(self) -> "MobileSettingsUpdateRequest":
        if (
            self.allow_notifications is None
            and self.mentions is None
            and self.direct_messages is None
            and self.team_messages is None
            and self.usage_analytics is None
            and self.crash_reports is None
            and self.theme_preference is None
        ):
            raise ValueError("At least one field must be provided")
        return self
