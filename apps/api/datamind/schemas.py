from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Signup(Strict):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    mode: Literal["personal", "enterprise"] = "personal"
    workspace: str = Field(default="My workspace", min_length=1, max_length=100)
    capacity: int = Field(default=5, ge=1, le=1000)


class Login(Strict):
    email: EmailStr
    password: str = Field(max_length=128)


class ConnectionConfig(Strict):
    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="", max_length=100)
    username: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=512)
    path: str = Field(default="", max_length=255)
    tls: bool = False
    auth_database: str = "admin"


class ConnectionInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["postgres", "mysql", "sqlite", "mongo"]
    config: ConnectionConfig
    resources: list[str] = Field(default_factory=list, max_length=100)
    catalog: dict[str, Any] = Field(default_factory=dict)


class QueryInput(Strict):
    sql: str = Field(min_length=1, max_length=12000)


class MongoQuery(Strict):
    collection: str = Field(min_length=1, max_length=100)
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int] = Field(default_factory=dict)
    sort: dict[str, Literal[-1, 1]] = Field(default_factory=dict)
    pipeline: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=100, ge=1, le=500)
    skip: int = Field(default=0, ge=0, le=10000)


class ToolCall(Strict):
    tool: Literal["sql", "mongo", "search_documents", "web"]
    source_id: str = ""
    sql: str = ""
    mongo: MongoQuery | None = None
    query: str = Field(default="", max_length=2000)
    depends_on: list[int] = Field(default_factory=list, max_length=6)


class Plan(Strict):
    clarification: str = ""
    category: Literal["general", "rag", "sql", "mongo", "hybrid", "web"]
    steps: list[ToolCall] = Field(default_factory=list, max_length=6)


class Chart(Strict):
    kind: Literal["table", "bar", "line", "pie", "scatter", "kpi"] = "table"
    title: str = Field(default="Result", max_length=120)
    x: str = ""
    y: str = ""
    result_index: int = Field(default=0, ge=0)


class ChatInput(Strict):
    question: str = Field(min_length=1, max_length=8000)
    source_ids: list[str] = Field(default_factory=list, max_length=200)
    web_enabled: bool = True
    web_query: str = Field(default="", max_length=1000)
    regenerate: bool = False


class Widget(Strict):
    message_id: str
    result_index: int = Field(default=0, ge=0)
    chart: Chart = Field(default_factory=Chart)


class DashboardInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    widgets: list[Widget] = Field(default_factory=list, max_length=30)


class InviteInput(Strict):
    email: EmailStr


class WorkspaceInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(ge=1, le=1000)


class ProfileInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    appearance: Literal["light", "dark", "system"] = "system"
    concise: bool = False
    custom_instructions: str = Field(default="", max_length=3000)
    about_me: str = Field(default="", max_length=1500)
    personalization_enabled: bool = True


class ConversationInput(Strict):
    pinned: bool | None = None
    source_ids: list[str] | None = Field(default=None, max_length=200)
    web_enabled: bool | None = None


class CatalogInput(Strict):
    resources: list[str] = Field(max_length=100)
    catalog: dict[str, Any]


class AcceptInvite(Strict):
    token: str = Field(min_length=20, max_length=100)


class GrantInput(Strict):
    user_id: str
    allowed: bool


class RoleInput(Strict):
    role: Literal["admin", "member"]


class SystemInput(Strict):
    global_daily_token_limit: int = Field(ge=1000, le=100_000_000)
    user_daily_token_limit: int = Field(ge=1000, le=10_000_000)


class FinalAnswer(Strict):
    answer: str
    citations: list[str] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list, max_length=5)

    @field_validator("answer")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty answer")
        return value
