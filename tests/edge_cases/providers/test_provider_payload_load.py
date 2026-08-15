"""Bulk, no-network payload checks against every shipped provider adapter."""

from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

from pykantui.providers.jira import JiraProvider
from pykantui.providers.monday import MondayProvider
from pykantui.tracker.base import Provider
from pykantui.tracker.models import IssueDraft, IssueEdit, IssueType, RemoteIssue
from pykantui.tracker.registry import get, specs

from .load_fixtures import PROVIDER_NAMES, MatrixProvider, project_for

ADAPTER_LOAD_COUNT = 250


class CaptureHttp:
    """A no-network transport that records requests from real adapters."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, object]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: object = None,
        body: object = None,
        **_: object,
    ) -> dict[str, object]:
        self.calls.append((method, path, params, body))
        return {}

    def get(self, path: str, params: object = None, **kwargs: object) -> dict[str, object]:
        return self.request("GET", path, params=params, **kwargs)

    def put(
        self,
        path: str,
        body: object = None,
        params: object = None,
        **kwargs: object,
    ) -> dict[str, object]:
        return self.request("PUT", path, params=params, body=body, **kwargs)

    def post(
        self,
        path: str,
        body: object = None,
        params: object = None,
        **kwargs: object,
    ) -> dict[str, object]:
        return self.request("POST", path, params=params, body=body, **kwargs)

    def patch(
        self,
        path: str,
        body: object = None,
        params: object = None,
        **kwargs: object,
    ) -> dict[str, object]:
        return self.request("PATCH", path, params=params, body=body, **kwargs)

    def delete(self, path: str, params: object = None, **kwargs: object) -> dict[str, object]:
        return self.request("DELETE", path, params=params, **kwargs)

    def graphql(
        self,
        query: str,
        variables: object = None,
        *,
        path: str = "",
    ) -> dict[str, object]:
        operation = query.strip().split("\n", maxsplit=1)[0]
        self.calls.append(("GRAPHQL", path or operation, variables, None))
        return {}

    def close(self) -> None:
        return None


def real_provider(name: str) -> Provider:
    """Instantiate one shipped adapter with inert, structurally valid data."""
    provider_type = get(name)
    values = {
        "base_url": "https://provider.invalid",
        "email": "load@example.invalid",
        "token": "test-token",
        "key": "test-key",
        "repo": "acme/load",
        "workspace": "workspace",
        "project_id": "project",
        "project_key": "LOAD",
        "team_id": "team",
        "board_id": "1",
        "list_id": "list",
        "workflow_id": "1",
        "status_column": "status",
        "description_column": "description",
        "assignee_column": "assignee",
        "type_column": "type",
        "priority_column": "priority",
        "labels_column": "labels",
        "due_column": "due",
    }
    config = {field.name: values.get(field.name, f"test-{field.name}") for field in provider_type.spec.config_fields}
    secrets = {
        field.name: str(values.get(field.name, f"test-{field.name}"))
        for field in provider_type.spec.auth_fields
    }
    return provider_type(config, secrets)


def move_column(provider_name: str) -> str:
    return {
        "github": "status:done",
        "jira": "done",
        "monday": "1",
        "shortcut": "2",
    }.get(provider_name, "done")


class RealProviderPayloadLoadTests(unittest.TestCase):
    def test_real_adapters_build_and_send_many_create_edit_and_move_payloads(self) -> None:
        """Exercise shipped provider code in bulk while every transport is inert."""
        provider_specs = specs()
        self.assertEqual(PROVIDER_NAMES, {spec.name for spec in provider_specs})

        for spec in provider_specs:
            with self.subTest(provider=spec.name):
                provider = real_provider(spec.name)
                self.assertIsNot(type(provider), MatrixProvider)
                capture = CaptureHttp()
                provider._http = capture  # type: ignore[assignment]  # noqa: SLF001 - no-network contract
                if isinstance(provider, MondayProvider):
                    provider._axis = ("status", {"1": "Done"})  # noqa: SLF001 - supplied discovery result

                resolver = (
                    patch.object(
                        provider,
                        "resolve_issue_type",
                        return_value=IssueType(type_id="10001", name="Task", default=True),
                    )
                    if isinstance(provider, JiraProvider)
                    else nullcontext()
                )
                payloads: list[dict[str, object]] = []
                with resolver:
                    for index in range(ADAPTER_LOAD_COUNT):
                        title = f"Create {spec.label} load item {index:04d}"
                        payload = provider.build_create_payload(
                            project_for(spec).project_id,
                            IssueDraft(
                                title=title,
                                body=f"Body {index:04d}",
                                column_id=move_column(spec.name),
                                issue_type="Task" if isinstance(provider, JiraProvider) else "",
                                due_date=date(2026, 9, 1),
                            ),
                        )
                        self.assertIn(title, json.dumps(payload))
                        payloads.append(dict(payload))

                self.assertEqual(ADAPTER_LOAD_COUNT, len(payloads))

                for index in range(ADAPTER_LOAD_COUNT):
                    issue = RemoteIssue(
                        issue_id=f"{spec.name}-{index:04d}",
                        key=f"LOAD-{index:04d}",
                        title=f"Original {index:04d}",
                        column_id="todo",
                        labels=("bug", "status:todo"),
                        extra={"number": index + 1},
                    )
                    if isinstance(provider, JiraProvider):
                        transitions = provider._transitions  # noqa: SLF001 - supplied transition discovery
                        transitions[issue.key] = {"done": f"transition-{index}"}
                    provider.update_issue(
                        issue,
                        IssueEdit(
                            title=f"Edited {spec.label} item {index:04d}",
                            column_id=move_column(spec.name),
                        ),
                    )

                self.assertGreaterEqual(len(capture.calls), ADAPTER_LOAD_COUNT)
                provider.close()


if __name__ == "__main__":
    unittest.main()
