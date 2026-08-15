"""Plane REST route construction."""


def workspace(workspace_slug: str, *parts: str) -> str:
    """Build a path under one Plane workspace."""
    return "/".join(["/api/v1/workspaces", workspace_slug, *parts])


def projects(workspace_slug: str) -> str:
    """Return the project collection path."""
    return workspace(workspace_slug, "projects/")


def project(workspace_slug: str, project_id: str) -> str:
    """Return one project path."""
    return workspace(workspace_slug, "projects", project_id, "")


def project_resource(workspace_slug: str, project_id: str, resource: str) -> str:
    """Return a project-scoped collection path."""
    return workspace(workspace_slug, "projects", project_id, f"{resource}/")


def work_item(workspace_slug: str, project_id: str, issue_id: str) -> str:
    """Return one project work-item path."""
    return workspace(workspace_slug, "projects", project_id, "work-items", issue_id, "")


def comments(workspace_slug: str, project_id: str, issue_id: str) -> str:
    """Return the current work-item comment collection path."""

    return workspace(
        workspace_slug,
        "projects",
        project_id,
        "work-items",
        issue_id,
        "comments/",
    )
