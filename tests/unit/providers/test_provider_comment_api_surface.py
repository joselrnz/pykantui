"""Official comment API surface invariants shared by the provider contract tests.

These checks deliberately stop at the transport boundary: fake credentials are
inspected locally and no request can leave the process.
"""

from __future__ import annotations

import unittest

import httpx

from pykantui.providers.asana import routes as asana_routes
from pykantui.providers.asana.client import AsanaClient
from pykantui.providers.clickup import routes as clickup_routes
from pykantui.providers.clickup.client import ClickUpClient
from pykantui.providers.github import routes as github_routes
from pykantui.providers.github.client import GitHubClient
from pykantui.providers.github.provider import API_VERSION as GITHUB_API_VERSION
from pykantui.providers.jira import routes as jira_routes
from pykantui.providers.jira.client import JiraClient
from pykantui.providers.linear import operations as linear_operations
from pykantui.providers.linear.client import LinearClient
from pykantui.providers.monday import operations as monday_operations
from pykantui.providers.monday.client import MondayClient
from pykantui.providers.monday.provider import API_VERSION as MONDAY_API_VERSION
from pykantui.providers.plane import routes as plane_routes
from pykantui.providers.plane.client import PlaneClient
from pykantui.providers.shortcut import routes as shortcut_routes
from pykantui.providers.shortcut.client import ShortcutClient
from pykantui.providers.trello import routes as trello_routes
from pykantui.providers.trello.client import TrelloClient


class CommentApiSurfaceTests(unittest.TestCase):
    """Pin the documented comment routes, auth styles, and stable API versions."""

    def test_rest_comment_routes_match_the_official_collections(self) -> None:
        self.assertEqual("/tasks/task-1/stories", asana_routes.task_stories("task-1"))
        self.assertEqual("/task/task-1/comment", clickup_routes.task_comments("task-1"))
        self.assertEqual("/comment/comment-1/reply", clickup_routes.comment_replies("comment-1"))
        self.assertEqual(
            "/repos/acme/widgets/issues/7/comments",
            github_routes.issue_comments("acme/widgets", 7),
        )
        self.assertEqual("/rest/api/3/issue/JPT-7/comment", jira_routes.comments("JPT-7"))
        self.assertEqual(
            "/api/v1/workspaces/acme/projects/P1/work-items/I1/comments/",
            plane_routes.comments("acme", "P1", "I1"),
        )
        self.assertEqual("/stories/7/comments", shortcut_routes.comments("7"))
        self.assertEqual("/cards/card-1/actions/comments", trello_routes.comments("card-1"))

    def test_graphql_documents_use_the_documented_comment_operations(self) -> None:
        self.assertIn("issue(id: $id)", linear_operations.COMMENTS_QUERY)
        self.assertIn("comments(first: 50, after: $cursor", linear_operations.COMMENTS_QUERY)
        self.assertIn("commentCreate(input:", linear_operations.CREATE_COMMENT_MUTATION)
        self.assertIn("updates(limit: 100, page: $page)", monday_operations.UPDATES_QUERY)
        self.assertIn("replies", monday_operations.UPDATES_QUERY)
        self.assertIn("create_update(item_id: $item, body: $body)", monday_operations.CREATE_UPDATE_MUTATION)

    def test_comment_transports_use_each_providers_documented_auth_style(self) -> None:
        token = "fake-token"
        clients = [
            AsanaClient.connect("https://asana.invalid", token),
            ClickUpClient.connect("https://clickup.invalid", token),
            GitHubClient.connect(
                "https://github.invalid", token, api_version=GITHUB_API_VERSION
            ),
            JiraClient.connect("https://jira.invalid", "person@example.invalid", token),
            LinearClient.connect("https://linear.invalid", token),
            MondayClient.connect(
                "https://monday.invalid", token, api_version=MONDAY_API_VERSION
            ),
            PlaneClient.connect("https://plane.invalid", token),
            ShortcutClient.connect("https://shortcut.invalid", token),
        ]
        for client in clients:
            self.addCleanup(client.close)

        asana, clickup, github, jira, linear, monday, plane, shortcut = clients
        self.assertEqual(f"Bearer {token}", asana._client.headers["Authorization"])
        self.assertEqual(token, clickup._client.headers["Authorization"])
        self.assertEqual(f"Bearer {token}", github._client.headers["Authorization"])
        self.assertEqual("application/vnd.github+json", github._client.headers["Accept"])
        self.assertEqual("2026-03-10", GITHUB_API_VERSION)
        self.assertEqual(GITHUB_API_VERSION, github._client.headers["X-GitHub-Api-Version"])

        # HTTPX applies Basic auth when building a request rather than storing
        # the resulting credential in the client's default header collection.
        self.assertIsInstance(jira._client.auth, httpx.BasicAuth)
        self.assertEqual(token, linear._client.headers["Authorization"])
        self.assertEqual(token, monday._client.headers["Authorization"])
        self.assertEqual("2026-07", MONDAY_API_VERSION)
        self.assertEqual(MONDAY_API_VERSION, monday._client.headers["API-Version"])
        self.assertEqual(token, plane._client.headers["X-API-Key"])
        self.assertEqual(token, shortcut._client.headers["Shortcut-Token"])

        self.assertEqual(
            {"key": "fake-key", "token": token, "filter": "commentCard"},
            TrelloClient.auth_params_for(
                "fake-key", token, filter="commentCard"
            ),
        )


if __name__ == "__main__":
    unittest.main()
