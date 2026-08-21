"""``kbn init`` — point a folder at a tracker and pull it down as markdown.

Works two ways from the same code:

* **With flags** — every answer supplied, nothing asked. What a script or CI
  run needs, and the only mode available when stdin is not a terminal.
* **With prompts** — anything missing is asked for. Field labels, help text and
  environment fallbacks all come from the provider's spec, so a new tracker
  gets a working wizard without a line of code here.

Nothing is written until the credentials have been checked against the tracker.
A folder full of scaffolding for a connection that never worked is worse than
no folder at all.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pykantui import git
from pykantui.commands.onboarding.projects import (
    ProjectMatch,
    choice_field,
    match_configured_project,
    normalize_projects,
    project_blurb,
    project_config,
    project_noun,
)
from pykantui.i18n import translate as _
from pykantui.pages import chooser, folder
from pykantui.tracker import ProviderError, get, names, specs
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteProject
from pykantui.tracker.spec import FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout
from pykantui.workspace import sync as sync_module
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.project import (
    Project,
    missing_required,
    resolve_fields,
    save_secrets,
)
from pykantui.workspace.registry import register_workspace


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("create a workspace from a tracker")
    parser = sub.add_parser("init", help=summary, description=summary)
    parser.add_argument("--type", dest="provider", default=None, help=_("which tracker; see --list-types"))
    parser.add_argument("--list-types", action="store_true", help=_("print the available trackers and exit"))
    parser.add_argument(
        "--list-ids",
        action="store_true",
        help=_("print the boards/projects this token can see, ready to paste into .env"),
    )
    parser.add_argument("--path", type=Path, default=None, help=_("where to create the workspace"))
    parser.add_argument("--name", default="", help=_("a name for the workspace"))
    parser.add_argument(
        "--no-browse",
        dest="browse",
        action="store_false",
        help=_("type the path instead of browsing for it"),
    )
    parser.add_argument(
        "--columns",
        choices=[style.value for style in ColumnStyle],
        default=ColumnStyle.SLUG.value,
        help=_("column folder naming (default: slug — lowercase, dashes)"),
    )
    parser.add_argument("--no-git", dest="use_git", action="store_false", help=_("do not create a git repository"))
    parser.add_argument("--no-sync", dest="do_sync", action="store_false", help=_("set up but do not pull yet"))
    parser.add_argument(
        "--no-open",
        dest="open_board",
        action="store_false",
        help=_("finish after setup instead of opening the new board"),
    )
    parser.add_argument("--yes", action="store_true", help=_("never prompt; fail instead"))

    # Every provider's fields become flags, derived from its spec rather than
    # listed here. Adding a tracker adds its flags for free.
    seen: set[str] = set()
    for spec in specs():
        for field in spec.all_fields():
            if field.name in seen:
                continue
            seen.add(field.name)
            parser.add_argument(field.cli_flag, dest=f"f_{field.name}", default=None, help=_flag_help(spec.name, field))


def _flag_help(provider: str, field: ProviderField) -> str:
    env = f" [${field.env_vars[0]}]" if field.env_vars else ""
    return f"{provider}: {field.label}{env}"


def run(args: argparse.Namespace) -> int:
    if args.list_types:
        return _list_types()
    interactive = not args.yes and sys.stdin.isatty()

    try:
        # Inside the try, so a missing token reports itself the way every other
        # failure here does rather than as a traceback.
        if getattr(args, "list_ids", False):
            return _list_ids(args)

        if interactive:
            from pykantui.commands.init_interactive import run_interactive  # noqa: PLC0415

            return run_interactive(args)

        provider_name = _pick_provider(args.provider, interactive)
        supplied = {name[2:]: value for name, value in vars(args).items() if name.startswith("f_") and value}
        config, secrets = resolve_fields(provider_name, supplied)

        if interactive:
            _ask_for_missing(provider_name, config, secrets)

        absent = missing_required(provider_name, config, secrets)
        if absent:
            flags = ", ".join(f"--{name.replace('_', '-')}" for name in absent)
            raise ProviderError(f"{provider_name} needs {', '.join(absent)}", hint=f"Pass {flags}.")

        provider = get(provider_name)(config, secrets)
        with provider:
            user = provider.verify()
            print(f"connected to {provider.spec.label} as {user.label()}")

            project = _pick_project(provider, config, interactive)
            config = project_config(provider.spec, config, project)
            workspace = _pick_path(
                args.path,
                project.key or project.project_id,
                interactive,
                browse=getattr(args, "browse", True),
            )
            _create(args, workspace, provider_name, project, config, secrets)
            result = _first_sync(
                args,
                workspace,
                provider,
                project,
                show_handoff=True,
            )
        return result

    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130


# ---- the questions -------------------------------------------------------


def _list_types() -> int:
    for spec in specs():
        mark = " " if spec.verified else "*"
        needs = ", ".join(field.name for field in spec.required_fields())
        print(f"{mark} {spec.name:<9} {spec.label:<12} needs: {needs}")
    print()
    print("* not yet verified against a live instance; treat its field mapping as a draft")
    return 0


def _extra_guidance(provider: str, field: ProviderField, secrets: dict[str, str]) -> None:
    """Print the one-off instructions a field needs, just before asking for it.

    Only Trello has any: its token comes from a URL you have to build out of
    the key you just entered, so the guidance is worthless until that moment
    and cannot live in static help text.
    """
    if provider != "trello":
        return
    if field.name == "key":
        print("  Trello keys live in a Power-Up now: https://trello.com/apps/admin")
        print("  Create one, open it, then the API Key tab.")
    elif field.name == "token" and secrets.get("key"):
        from pykantui.providers.trello import token_url_for  # noqa: PLC0415

        print("  Open this to authorise, then paste the token it shows:")
        print(f"    {token_url_for(secrets['key'])}")


def _list_ids(args: argparse.Namespace) -> int:
    """Print the boards this token can see, with the line to paste into .env.

    Closes a chicken-and-egg: ``.env`` needs a board id, the board id lives
    behind the API, and the only thing that listed them was the interactive
    wizard -- which you cannot run until ``.env`` is filled in. Several of these
    ids are also invisible in the tracker's own UI (Linear's team UUID, Plane's
    project UUID), so "just look it up in the web app" is not an answer either.
    """
    try:
        provider_name = _pick_provider(args.provider, interactive=False)
    except ProviderError:
        raise ProviderError(
            "--list-ids needs to know which tracker",
            hint="Pass --type, e.g. kbn init --type linear --list-ids",
        ) from None

    supplied = {name[2:]: value for name, value in vars(args).items() if name.startswith("f_") and value}
    config, secrets = resolve_fields(provider_name, supplied)

    absent = missing_required(provider_name, config, secrets)
    if absent:
        spec = get(provider_name).spec
        wanted = ", ".join(_env_name(spec, name) for name in absent)
        raise ProviderError(
            f"{provider_name} needs {', '.join(absent)} before it can list anything",
            hint=f"Set {wanted} in .env, or pass the matching --flag.",
        )

    provider = get(provider_name)(config, secrets)
    with provider:
        user = provider.verify()
        print(f"connected to {provider.spec.label} as {user.label()}")
        print()

        projects = provider.list_projects()
        if not projects:
            print("  nothing here yet — create a board in the tracker first")
            return 0

        width = max(len(item.key or "") for item in projects)
        for item in projects:
            print(f"  {(item.key or ''):<{width}}  {item.name:<24} {item.project_id}")

        field = _project_field(provider.spec)
        if field is not None and field.env_vars:
            # Which column to suggest depends on what the field *means*. Jira
            # asks for a project **key** ("JPT"); everything else asks for an
            # id. Suggesting a name where an id is wanted -- Asana takes a gid
            # and would reject "Study schedule" -- turns a helpful line into a
            # wrong one.
            first = projects[0]
            wanted = first.key if field.name.endswith("_key") else first.project_id
            print()
            print(f"  paste one of these into .env as {field.env_vars[0]}:")
            print(f"    {field.env_vars[0]}={wanted or first.project_id}")
    return 0


def _env_name(spec: ProviderSpec, field_name: str) -> str:
    """The .env variable behind a field, for an error that names what to set."""
    field = spec.field_named(field_name)
    if field is not None and field.env_vars:
        return field.env_vars[0]
    return field_name


def _project_field(spec: ProviderSpec) -> ProviderField | None:
    """The config field that selects a board, if the provider has one."""
    return choice_field(spec)


def _pick_provider(supplied: str | None, interactive: bool) -> str:
    if supplied:
        if supplied.lower() not in names():
            raise ProviderError(f"no tracker named {supplied!r}", hint=f"Available: {', '.join(names())}")
        return supplied.lower()
    if not interactive:
        raise ProviderError("no tracker given", hint="Pass --type; see --list-types.")

    available = specs()

    if chooser.can_run():
        picked = chooser.choose(
            [
                chooser.Choice(
                    value=spec.name,
                    label=spec.label,
                    detail=spec.name,
                    note="verified" if spec.verified else "not tested",
                    marker="●" if spec.verified else "○",
                    tone="green" if spec.verified else "yellow",
                    description=_tracker_blurb(spec),
                    keywords=(spec.name, *(f.name for f in spec.all_fields())),
                )
                for spec in available
            ],
            title="Which tracker?",
            filter_hint="type to filter — jira, linear, trello…",
        )
        if picked:
            return picked
        raise ProviderError("no tracker chosen", hint="Run kbn init again, or pass --type.")

    print("which tracker?")
    for index, spec in enumerate(available, start=1):
        mark = "" if spec.verified else "  (unverified)"
        print(f"  {index}. {spec.label}{mark}")
    return available[_ask_index("tracker", len(available))].name


def _tracker_blurb(spec: ProviderSpec) -> str:
    """What the panel says about a tracker before you pick it."""
    needs = ", ".join(field.label for field in spec.required_fields())
    state = "verified against a live account" if spec.verified else "written from docs, not yet tested live"
    return f"{spec.description}\nneeds: {needs}\n{state}"


def _ask_for_missing(provider: str, config: dict[str, object], secrets: dict[str, str]) -> None:
    """Prompt for anything still unset, using the spec's own labels."""
    from getpass import getpass  # noqa: PLC0415 - only needed interactively

    spec = get(provider).spec
    for field in spec.all_fields():
        if config.get(field.name) or secrets.get(field.name):
            continue
        # A choice needs the provider connected first; it is asked later.
        if field.kind is FieldKind.CHOICE:
            continue
        if not field.required:
            continue

        _extra_guidance(provider, field, secrets)

        hint = f" ({field.help})" if field.help else ""
        placeholder = f" [{field.placeholder}]" if field.placeholder else ""
        prompt = f"{field.label}{placeholder}{hint}: "
        value = getpass(prompt) if field.secret else input(prompt)
        value = value.strip()
        if not value and field.placeholder:
            value = field.placeholder
        if not value:
            continue
        if field.secret:
            secrets[field.name] = value
        else:
            config[field.name] = value

    if spec.token_url and not secrets:
        print(f"  tokens: {spec.token_url}")


def _pick_project(provider: Provider, config: dict[str, object], interactive: bool) -> RemoteProject:
    """The project to sync: from config if given, else asked, else the only one."""
    spec = provider.spec
    field = choice_field(spec)
    supplied = str(config.get(field.name, "")) if field else ""

    available = normalize_projects(provider.list_projects())
    if supplied:
        match = match_configured_project(supplied, available)
        if match.project is not None:
            return match.project
        if match.kind is ProjectMatch.AMBIGUOUS:
            raise ProviderError(
                f"configured value {supplied!r} matches more than one {project_noun(spec, count=1)}",
                hint=f"Pass an exact remote id with {field.cli_flag if field else '--project'}.",
            )
        raise ProviderError(
            f"configured {project_noun(spec, count=1)} {supplied!r} is not visible",
            hint="Check the account and token permissions, or choose one returned by --list-ids.",
        )

    if not available:
        raise ProviderError("that account can see no projects")
    if len(available) == 1:
        print(f"one project available: {available[0].label()}")
        return available[0]
    if not interactive:
        names_ = ", ".join(p.key or p.project_id for p in available[:10])
        raise ProviderError(
            f"more than one {project_noun(spec, count=1)} available",
            hint=f"Pass --{field.name.replace('_', '-') if field else 'project'}. Seen: {names_}",
        )

    if chooser.can_run():
        picked = chooser.choose(
            [
                chooser.Choice(
                    value=project.project_id,
                    label=project.name or project.key or project.project_id,
                    detail=project.key,
                    marker="▣",
                    tone="cyan",
                    description=project_blurb(project),
                    keywords=(project.project_id, project.key, project.name),
                )
                for project in available
            ],
            title=f"Which {spec.label} {project_noun(spec, count=1)}?",
            filter_hint="type to filter",
        )
        if picked:
            found = next((p for p in available if p.project_id == picked), None)
            if found is not None:
                return found
        raise ProviderError("no project chosen", hint="Run kbn init again.")

    print("which project?")
    for index, project in enumerate(available, start=1):
        print(f"  {index}. {project.label()}")
    return available[_ask_index("project", len(available))]


def _project_blurb(project: RemoteProject) -> str:
    """The panel text for one project: enough to tell two similar ones apart."""
    lines = [project.description.strip().splitlines()[0]] if project.description.strip() else []
    lines.append(f"id: {project.project_id}")
    if project.url:
        lines.append(project.url)
    return "\n".join(lines)


def _pick_path(supplied: Path | None, default_name: str, interactive: bool, *, browse: bool | None = None) -> Path:
    """Where the workspace goes: given, browsed for, or typed.

    The browser is the default on a real terminal because this is the answer
    people get wrong -- a mistyped path creates a workspace somewhere
    unexpected rather than failing, and you find out two commands later. It is
    skipped when there is no terminal to draw on, and ``--no-browse`` turns it
    off for anyone who would rather type.
    """
    if supplied is not None:
        return supplied.expanduser().resolve()
    if not interactive:
        raise ProviderError("no path given", hint="Pass --path.")

    suggested = Path.cwd() / default_name.lower()

    if browse is not False and folder.can_run():
        chosen = folder.choose(Path.cwd(), title=f"Where should {default_name} live?")
        if chosen is not None:
            # The picker answers with a parent directory; the workspace itself
            # is still named after the project, so two inits into the same
            # folder do not land on top of each other.
            return (chosen / default_name.lower()).resolve()
        print("  (cancelled the browser — type a path instead)")

    answer = input(f"where should it live? [{suggested}]: ").strip()
    return Path(answer).expanduser().resolve() if answer else suggested


def _ask_index(what: str, count: int) -> int:
    while True:
        answer = input(f"{what} [1-{count}]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        print(f"  enter a number between 1 and {count}")


# ---- doing it ------------------------------------------------------------


def _create(
    args: argparse.Namespace,
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    config: dict[str, object],
    secrets: dict[str, str],
    *,
    verbose: bool = True,
    save_credentials: bool = True,
) -> None:
    existing = layout.project_file(workspace)
    if existing.exists():
        raise ProviderError(f"{workspace} is already a workspace", hint=f"Delete {existing} to start again.")

    if args.use_git and not git.available():
        raise ProviderError(
            "Git is unavailable, so the requested local version history cannot be created",
            hint="Install Git or explicitly pass --no-git.",
        )

    workspace.mkdir(parents=True, exist_ok=True)
    if args.use_git and not git.init(workspace):
        raise ProviderError(
            "could not initialize local Git version history",
            hint="Check Git and workspace permissions, or explicitly pass --no-git.",
        )
    layout.meta_dir(workspace).mkdir(parents=True, exist_ok=True)

    record = Project(
        provider=provider_name,
        project_id=project.project_id,
        key=project.key,
        name=args.name or project.name,
        owner=project.owner,
        config=dict(config),
        column_style=ColumnStyle(args.columns),
    )
    record.save(workspace)
    if save_credentials:
        save_secrets(provider_name, secrets, config=config)
    register_workspace(workspace, record)

    _write_gitignore(workspace)
    readme_created = _write_readme(workspace, record)

    if verbose:
        print(f"created {workspace}")
        print(f"  {layout.project_file(workspace).relative_to(workspace)}")
        if save_credentials:
            print("  credentials saved separately, outside the workspace")
        else:
            print("  credentials were not saved")

    git_created = args.use_git
    if git_created:
        scaffold = [layout.project_file(workspace), workspace / ".gitignore"]
        if readme_created:
            scaffold.append(workspace / "README.md")
        try:
            dirty = git.is_dirty(workspace, paths=scaffold)
            committed = not dirty or git.commit(
                workspace,
                f"init({provider_name}/{project.slug()}): create workspace",
                paths=scaffold,
            )
        except git.GitCommandError as error:
            raise ProviderError(
                "could not inspect local Git status during setup",
                hint="Repair Git before the initial provider sync.",
            ) from error
        if not committed:
            raise ProviderError(
                "could not create the initial local workspace version",
                hint="Check the local Git repository permissions before syncing.",
            )
    if git_created and verbose:
        print("  git initialised")


def _write_gitignore(workspace: Path) -> None:
    from pykantui.config.paths import write_text_atomic  # noqa: PLC0415

    target = workspace / ".gitignore"
    if target.is_symlink():
        raise ProviderError("refusing to replace a symlinked workspace .gitignore")
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    entries = (
        f"{layout.META_DIR}/{layout.CACHE_DIR}/",
        ".env",
        ".env*",
        "auth.json",
        "credentials.json",
        f"{layout.META_DIR}/*.lock*",
    )
    present = set(existing.splitlines())
    missing = [entry for entry in entries if entry not in present]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    section = (
        "# pykantui: local caches, credentials, and runtime locks.\n"
        + "\n".join(missing)
        + "\n"
    )
    write_text_atomic(target, existing + prefix + section)


def _write_readme(workspace: Path, project: Project) -> bool:
    from pykantui.config.paths import write_text_atomic  # noqa: PLC0415

    target = workspace / "README.md"
    if target.exists() or target.is_symlink():
        return False
    write_text_atomic(
        target,
        f"# {project.name or project.key or project.provider}\n\n"
        f"Synced from **{project.provider}** by pykantui.\n\n"
        "One markdown file per issue. Edit them, then run `kbn sync` to send the\n"
        "changes back. Notes below the `pykantui:notes` marker are yours and are\n"
        "never overwritten.\n\n"
        "Moving a file between column folders moves the card.\n",
    )
    return True


def _first_sync(
    args: argparse.Namespace,
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    *,
    show_handoff: bool = True,
) -> int:
    if not args.do_sync:
        print("run `kbn sync` to pull the issues")
        return 0

    report = sync_module.sync(
        workspace,
        provider,
        project,
        push_edits=False,  # nothing local can have changed yet
        commit=args.use_git,
        column_style=ColumnStyle(args.columns),
    )
    print(f"pulled: {report.summary()}")
    if show_handoff:
        print()
        print(f"  cd {workspace}")
        print("  kbn sync      # pull changes, and send yours back")
    return 0


def _open_workspace(workspace: Path) -> None:
    """Replace interactive init with the board, attached to the same terminal.

    Init may already have run two short Textual apps (tracker and folder
    pickers). Reattaching the controlling terminal prevents their released
    input stream from looking like EOF to the board. ``execv`` then replaces
    init instead of creating a child that can return immediately to the shell.
    """
    try:
        os.chdir(workspace)
        _reattach_controlling_terminal()
        os.execv(sys.executable, [sys.executable, "-m", "pykantui"])
    except OSError as error:
        raise ProviderError(
            "the workspace was created, but the board could not be opened",
            hint=f"Run `cd {workspace}` then `kbn`.",
        ) from error


def _reattach_controlling_terminal() -> None:
    """Restore stdin/out/err after a short-lived Textual picker on POSIX."""
    if os.name != "posix":
        return
    try:
        terminal = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return
    try:
        for standard_stream in (0, 1, 2):
            os.dup2(terminal, standard_stream)
    finally:
        if terminal > 2:
            os.close(terminal)
