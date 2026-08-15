"""Asana REST route construction."""

CURRENT_USER = "/users/me"
WORKSPACES = "/workspaces"
PROJECTS = "/projects"
TASKS = "/tasks"


def project(project_id: str) -> str:
    """Return the route for one project."""
    return f"/projects/{project_id}"


def sections(project_id: str) -> str:
    """Return the sections collection for a project."""
    return f"/projects/{project_id}/sections"


def project_tasks(project_id: str) -> str:
    """Return the tasks collection for a project."""
    return f"/projects/{project_id}/tasks"


def task(task_id: str) -> str:
    """Return the route for one task."""
    return f"/tasks/{task_id}"


def task_stories(task_id: str) -> str:
    """Return the task activity stream used for provider comments."""
    return f"{task(task_id)}/stories"


def add_task(section_id: str) -> str:
    """Return the mutation route that puts a task in a section."""
    return f"/sections/{section_id}/addTask"


def workspace_users(workspace_id: str) -> str:
    """Return the user collection for a workspace."""
    return f"/workspaces/{workspace_id}/users"
