"""Provider-neutral project discovery and selection rules."""

from __future__ import annotations

import unittest

from pykantui.commands.onboarding.projects import (
    ProjectMatch,
    choice_field,
    match_configured_project,
    normalize_projects,
    project_config,
    project_noun,
)
from pykantui.tracker.models import RemoteProject
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import FieldKind, ProviderField, ProviderSpec


class ProjectSelectionTests(unittest.TestCase):
    @staticmethod
    def spec(name: str = "example", label: str = "Example", field_label: str = "Project") -> ProviderSpec:
        return ProviderSpec(
            name=name,
            label=label,
            config_fields=(
                ProviderField(
                    name="repo" if field_label == "Repository" else "project_id",
                    label=field_label,
                    kind=FieldKind.CHOICE,
                ),
            ),
        )

    def test_container_noun_comes_from_the_provider_choice_field(self) -> None:
        expected = {
            "Project": ("project", "projects"),
            "Project key": ("project", "projects"),
            "Repository": ("repository", "repositories"),
            "List": ("list", "lists"),
            "Board": ("board", "boards"),
            "Team": ("team", "teams"),
            "Workflow": ("workflow", "workflows"),
        }

        for label, nouns in expected.items():
            with self.subTest(label=label):
                spec = self.spec(field_label=label)
                self.assertEqual(nouns[0], project_noun(spec, count=1))
                self.assertEqual(nouns[1], project_noun(spec, count=2))

    def test_discovered_projects_are_deduplicated_by_id_and_sorted(self) -> None:
        projects = [
            RemoteProject(project_id="2", key="Z", name="Zulu"),
            RemoteProject(project_id="1", key="A", name="Alpha"),
            RemoteProject(project_id="2", key="duplicate", name="Duplicate response"),
            RemoteProject(project_id="3", key="A", name="Alpha"),
        ]

        normalized = normalize_projects(projects)

        self.assertEqual(["1", "3", "2"], [project.project_id for project in normalized])
        self.assertEqual("Zulu", normalized[-1].name)

    def test_exact_id_is_the_safest_configured_match(self) -> None:
        projects = [
            RemoteProject(project_id="123", key="APP", name="Application"),
            RemoteProject(project_id="456", key="APP", name="Application"),
        ]

        match = match_configured_project("456", projects)

        self.assertEqual(ProjectMatch.EXACT_ID, match.kind)
        self.assertEqual("456", match.project.project_id if match.project else None)

    def test_ambiguous_key_or_name_never_selects_the_first_project(self) -> None:
        projects = [
            RemoteProject(project_id="123", key="APP", name="Application"),
            RemoteProject(project_id="456", key="APP", name="Application"),
        ]

        by_key = match_configured_project("APP", projects)
        by_name = match_configured_project("Application", projects)

        self.assertEqual(ProjectMatch.AMBIGUOUS, by_key.kind)
        self.assertIsNone(by_key.project)
        self.assertEqual(ProjectMatch.AMBIGUOUS, by_name.kind)
        self.assertIsNone(by_name.project)

    def test_selected_container_is_written_back_using_the_provider_field_semantics(self) -> None:
        jira = ProviderSpec(
            name="jira",
            label="Jira",
            config_fields=(
                ProviderField(name="project_key", label="Project key", kind=FieldKind.CHOICE),
            ),
        )
        github = self.spec(name="github", label="GitHub", field_label="Repository")
        project = RemoteProject(project_id="10001", key="APP", name="Application")

        self.assertEqual({"project_key": "APP"}, project_config(jira, {}, project))
        self.assertEqual({"repo": "10001"}, project_config(github, {}, project))

    def test_spec_without_a_dynamic_choice_uses_generic_project_language(self) -> None:
        spec = ProviderSpec(name="example", label="Example")

        self.assertIsNone(choice_field(spec))
        self.assertEqual("project", project_noun(spec, count=1))

    def test_every_builtin_uses_its_provider_native_container_name(self) -> None:
        expected = {
            "asana": "project",
            "clickup": "list",
            "github": "repository",
            "jira": "project",
            "linear": "team",
            "monday": "board",
            "plane": "project",
            "shortcut": "workflow",
            "trello": "board",
        }

        self.assertEqual(expected, {spec.name: project_noun(spec, count=1) for spec in specs()})


if __name__ == "__main__":
    unittest.main()
