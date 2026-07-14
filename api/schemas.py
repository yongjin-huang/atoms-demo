"""Mirrors the API contract. camelCase on the wire, snake_case in Python."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    n: int
    prompt: str
    html: str
    model_id: str = Field(serialization_alias="modelId")
    created_at: datetime = Field(serialization_alias="createdAt")


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    role: str
    content: str
    reasoning: str | None = None
    model_id: str | None = Field(default=None, serialization_alias="modelId")
    version_id: str | None = Field(default=None, serialization_alias="versionId")
    created_at: datetime = Field(serialization_alias="createdAt")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    title: str
    created_at: datetime = Field(serialization_alias="createdAt")


class ProjectDetail(BaseModel):
    project: ProjectOut
    versions: list[VersionOut]
    messages: list[MessageOut]


class GenerateIn(BaseModel):
    prompt: str
    project_id: str | None = Field(default=None, validation_alias="projectId")
    model_id: str | None = Field(default=None, validation_alias="modelId")


class ModelOut(BaseModel):
    id: str
    label: str
    provider: str


class ModelsOut(BaseModel):
    models: list[ModelOut]
    default: str | None


class KeyStatus(BaseModel):
    deepseek: bool
    openai: bool
    anthropic: bool
    openrouter: bool


class SettingsOut(BaseModel):
    default_model_id: str | None = Field(default=None, serialization_alias="defaultModelId")
    configured: bool
    keys: KeyStatus
    models: list[ModelOut]


class SettingsIn(BaseModel):
    default_model_id: str | None = Field(default=None, validation_alias="defaultModelId")
    deepseek_api_key: str | None = Field(default=None, validation_alias="deepseekApiKey")
    openai_api_key: str | None = Field(default=None, validation_alias="openaiApiKey")
    anthropic_api_key: str | None = Field(default=None, validation_alias="anthropicApiKey")
    openrouter_api_key: str | None = Field(default=None, validation_alias="openrouterApiKey")
