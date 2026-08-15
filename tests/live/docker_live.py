"""Check every configured provider from inside the container.

Not part of the unittest suite: it needs real credentials and a network, and a
test that silently passes when neither is present is worse than no test. Run it
with ``docker compose run --rm live``.

The point is not to re-test the providers -- that has been done from Windows --
but to find what differs *here*: TLS trust from a fresh Ubuntu, DNS, an encoding
that no longer happens to be cp1252, and paths with the other separator.
"""

from __future__ import annotations

import sys

from pykantui.api.redaction import redact
from pykantui.providers import builtin_providers
from pykantui.tracker import ProviderError, get
from pykantui.workspace.project import missing_required, resolve_fields


def main() -> int:
    print(f"python  : {sys.version.split()[0]}")
    print(f"platform: {sys.platform}")
    print(f"stdout  : {sys.stdout.encoding}")
    print()

    configured, skipped, failed = 0, 0, 0
    print(
        f"{'provider':10} {'auth':6} {'columns':8} {'issues':7} "
        f"{'refresh':8} {'comments':8} detail"
    )
    print("-" * 94)

    for name in sorted(builtin_providers()):
        config, secrets = resolve_fields(name, {})
        absent = missing_required(name, config, secrets)
        creds = {field.name for field in get(name).spec.auth_fields}
        if creds & set(absent):
            skipped += 1
            print(
                f"{name:10} {'-':6} {'-':8} {'-':7} {'-':8} {'-':8} "
                "no credentials in this environment"
            )
            continue

        configured += 1
        try:
            provider = get(name)(config, secrets)
            with provider:
                who = provider.verify()

                field = next((f for f in provider.spec.config_fields if f.kind.value == "choice"), None)
                target = str(config.get(field.name, "")) if field else ""
                if not target:
                    found = provider.list_projects()
                    target = found[0].project_id if found else ""

                columns = len(provider.columns(target)) if target else 0
                found_issues = list(provider.iter_issues(target)) if target else []
                refreshed = provider.get_issue(target, found_issues[0]) if found_issues else None
                refresh_status = "ok" if refreshed is not None else "empty" if not found_issues else "missing"
                comment_count = (
                    len(list(provider.iter_comments(target, found_issues[0])))
                    if found_issues and provider.spec.capabilities.read_comments
                    else 0
                )
                # These are memoized structural reads. They cover cache and
                # response-shape compatibility without mutating the account.
                provider.issue_types(target) if target else []
                provider.components(target) if target else []
                print(
                    f"{name:10} {'ok':6} {columns:<8} {len(found_issues):<7} "
                    f"{refresh_status:<8} {comment_count:<8} as {who.label()}"
                )
        except ProviderError as error:
            failed += 1
            detail = redact(error, secrets.values())[:44]
            print(f"{name:10} {'FAIL':6} {'-':8} {'-':7} {'-':8} {'-':8} {detail}")
        except Exception as error:  # noqa: BLE001 - a survey records everything
            failed += 1
            detail = redact(error, secrets.values())[:44]
            print(
                f"{name:10} {'ERROR':6} {'-':8} {'-':7} {'-':8} {'-':8} "
                f"{type(error).__name__}: {detail}"
            )

    print()
    print(f"  {configured} configured, {skipped} without credentials, {failed} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
