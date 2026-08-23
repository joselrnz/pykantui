"""Pydantic models for Jira Cloud wire responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator
from pydantic import JsonValue as PydanticJsonValue


class JiraWireModel(BaseModel):
    """Base response model tolerant of additive Jira fields."""

    model_config = ConfigDict(extra="ignore")


class UserWire(JiraWireModel):
    """Jira Cloud account fragment."""

    accountId: str = ""  # noqa: N815 - provider wire key
    displayName: str = ""  # noqa: N815 - provider wire key
    emailAddress: str = ""  # noqa: N815 - provider wire key


class ProjectWire(JiraWireModel):
    """Jira project discovery record."""

    id: str = ""
    key: str = ""
    name: str = ""
    style: str = ""
    projectTypeKey: str = ""  # noqa: N815 - provider wire key


class BoardWire(JiraWireModel):
    """Jira agile board discovery record."""

    id: int | str = ""
    name: str = ""
    type: str = ""


class StatusCategoryWire(JiraWireModel):
    """Jira status-category fragment."""

    key: str = ""


class StatusWire(JiraWireModel):
    """Jira workflow status record."""

    id: str = ""
    name: str = ""
    statusCategory: StatusCategoryWire | None = None  # noqa: N815 - provider wire key


class BoardColumnWire(JiraWireModel):
    """Agile board column and its underlying statuses."""

    name: str = ""
    statuses: list[StatusWire] = Field(default_factory=list)


class ColumnConfigWire(JiraWireModel):
    """Agile board column configuration."""

    columns: list[BoardColumnWire] = Field(default_factory=list)


class BoardConfigurationWire(JiraWireModel):
    """Agile board configuration response."""

    columnConfig: ColumnConfigWire = Field(default_factory=ColumnConfigWire)  # noqa: N815


class IssueTypeStatusesWire(JiraWireModel):
    """Statuses grouped under one Jira issue type."""

    statuses: list[StatusWire] = Field(default_factory=list)


class ProjectStatusesWire(RootModel[list[IssueTypeStatusesWire]]):
    """Project-status response."""


class NamedWire(JiraWireModel):
    """Nested Jira record identified by name."""

    id: str = ""
    name: str = ""
    key: str = ""


class ComponentWire(JiraWireModel):
    """Project component or issue component fragment."""

    id: str = ""
    name: str = ""
    description: str = ""


class IssueFieldsWire(JiraWireModel):
    """Jira issue fields consumed by pykantui."""

    summary: str = ""
    description: PydanticJsonValue = None
    status: StatusWire = Field(default_factory=StatusWire)
    issuetype: NamedWire | None = None
    assignee: UserWire | None = None
    reporter: UserWire | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[ComponentWire] = Field(default_factory=list)
    priority: NamedWire | None = None
    created: str | None = None
    updated: str | None = None
    duedate: str | None = None
    parent: NamedWire | None = None
    resolutiondate: str | None = None


class IssueWire(JiraWireModel):
    """Jira issue response."""

    id: str = ""
    key: str = ""
    fields: IssueFieldsWire = Field(default_factory=IssueFieldsWire)


class IssueTypeWire(JiraWireModel):
    """Jira create metadata issue type."""

    id: str = ""
    name: str = ""
    subtask: bool = False
    hierarchyLevel: int | str | None = None  # noqa: N815 - provider wire key


class IssueTypesWire(JiraWireModel):
    """Issue-type response across old and current Jira variants."""

    issueTypes: list[IssueTypeWire] = Field(default_factory=list)  # noqa: N815
    values: list[IssueTypeWire] = Field(default_factory=list)


class JsonTypeWire(JiraWireModel):
    """Jira field data-type description."""

    type: str = ""
    items: str | None = None
    system: str | None = None
    custom: str | None = None
    customId: int | None = None  # noqa: N815 - provider wire key
    configuration: dict[str, PydanticJsonValue] = Field(default_factory=dict)


class FieldMetadataWire(JiraWireModel):
    """Capabilities and constraints for one editable Jira field."""

    key: str = ""
    name: str = ""
    required: bool = False
    operations: list[str] = Field(default_factory=list)
    field_schema: JsonTypeWire = Field(default_factory=JsonTypeWire, alias="schema")
    allowedValues: list[PydanticJsonValue] = Field(default_factory=list)  # noqa: N815
    hasDefaultValue: bool = False  # noqa: N815
    defaultValue: PydanticJsonValue = None  # noqa: N815
    autoCompleteUrl: str = ""  # noqa: N815
    configuration: dict[str, PydanticJsonValue] = Field(default_factory=dict)


class CreateFieldMetadataWire(FieldMetadataWire):
    """Create-screen metadata, which also identifies the field."""

    fieldId: str = ""  # noqa: N815 - provider wire key


class EditMetadataWire(JiraWireModel):
    """Field directory returned by an issue's edit-metadata route."""

    fields: dict[str, FieldMetadataWire] = Field(default_factory=dict)


class FieldWire(JiraWireModel):
    """System or custom field returned by Jira field search."""

    id: str = ""
    key: str = ""
    name: str = ""
    description: str = ""
    field_schema: JsonTypeWire = Field(default_factory=JsonTypeWire, alias="schema")
    isLocked: bool = False  # noqa: N815 - provider wire key
    isUnscreenable: bool = False  # noqa: N815 - provider wire key
    searcherKey: str = ""  # noqa: N815 - provider wire key


class PriorityWire(JiraWireModel):
    """One Jira issue priority."""

    id: str = ""
    name: str = ""
    description: str = ""
    isDefault: bool = False  # noqa: N815 - provider wire key
    iconUrl: str = ""  # noqa: N815 - provider wire key
    statusColor: str = ""  # noqa: N815 - provider wire key


class SprintWire(JiraWireModel):
    """One sprint visible through Jira Software's agile API."""

    id: int | str = ""
    name: str = ""
    state: str = ""
    goal: str = ""
    originBoardId: int | str = ""  # noqa: N815 - provider wire key
    startDate: str | None = None  # noqa: N815 - provider wire key
    endDate: str | None = None  # noqa: N815 - provider wire key
    completeDate: str | None = None  # noqa: N815 - provider wire key
    createdDate: str | None = None  # noqa: N815 - provider wire key
    self: str = ""


class TransitionTargetWire(JiraWireModel):
    """Destination status for a transition."""

    id: str = ""


class TransitionWire(JiraWireModel):
    """Jira workflow transition."""

    id: str = ""
    name: str = ""
    to: TransitionTargetWire = Field(default_factory=TransitionTargetWire)


class TransitionsWire(JiraWireModel):
    """Transition collection response."""

    transitions: list[TransitionWire] = Field(default_factory=list)


class CreatedIssueWire(JiraWireModel):
    """Minimal create-issue response."""

    id: str = ""
    key: str = ""


class CommentWire(JiraWireModel):
    """One Jira issue-comment response."""

    id: str
    body: PydanticJsonValue = None
    author: UserWire = Field(default_factory=UserWire)
    created: str | None = None
    updated: str | None = None
    self: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Reject records that cannot be tracked across later syncs."""
        if not value.strip():
            raise ValueError("comment id must not be blank")
        return value


class CommentsPageWire(JiraWireModel):
    """Offset-paged Jira issue comments."""

    startAt: int  # noqa: N815
    maxResults: int  # noqa: N815
    total: int
    comments: list[CommentWire]


__all__ = [
    "BoardConfigurationWire",
    "BoardWire",
    "CreatedIssueWire",
    "ComponentWire",
    "CommentWire",
    "CommentsPageWire",
    "CreateFieldMetadataWire",
    "EditMetadataWire",
    "FieldMetadataWire",
    "FieldWire",
    "IssueTypeWire",
    "IssueTypesWire",
    "IssueWire",
    "ProjectStatusesWire",
    "ProjectWire",
    "PriorityWire",
    "SprintWire",
    "StatusWire",
    "TransitionsWire",
    "UserWire",
]
