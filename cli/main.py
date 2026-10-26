"""Entry point for the ``factory`` command."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.models.settings import load_env_file
from orchestrator.version import factory_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Tank Factory v1: install, operate and inspect.",
    )
    parser.add_argument("--version", action="store_true", help="print the factory version and exit")
    sub = parser.add_subparsers(dest="command")

    dev = sub.add_parser("dev", help="development helpers")
    dev_sub = dev.add_subparsers(dest="dev_command")
    seed = dev_sub.add_parser("seed", help="create a scratch product repository with ten specs")
    seed.add_argument("target", type=Path)
    seed.add_argument(
        "--with-violations", action="store_true", help="also write protected-edit scripts"
    )
    issues = dev_sub.add_parser("issues", help="open the seed issues on a GitHub repository")
    issues.add_argument("--org", required=True)
    issues.add_argument("--repo", required=True)
    issues.add_argument("--data-dir", type=Path)

    srv = sub.add_parser("serve", help="run the orchestrator against one GitHub repository")
    srv.add_argument("--repo-clone", type=Path, required=True, help="clone of the product repo")
    srv.add_argument("--org", required=True)
    srv.add_argument("--repo", required=True)
    srv.add_argument("--harness", choices=("roster", "fake"), default="roster")
    srv.add_argument("--scripts-dir", type=Path, help="fake scripts (with --harness fake)")
    srv.add_argument("--budget", type=float, default=6.0, help="per-task budget in USD")
    srv.add_argument("--data-dir", type=Path)
    srv.add_argument("--bind")
    srv.add_argument("--port", type=int)
    srv.add_argument("--no-poller", action="store_true")
    srv.add_argument("--poll-interval", type=float, help="seconds between polls")

    run = sub.add_parser("run-spec", help="run the build slice on one hand-written spec")
    run.add_argument("--repo", type=Path, required=True, help="product repository (a git checkout)")
    run.add_argument("--issue", type=int, required=True)
    run.add_argument(
        "--spec", help="spec path relative to the repository; default specs/<issue>-*.md"
    )
    run.add_argument(
        "--harness",
        choices=("fake", "claude_code", "codex"),
        help="override the roster's builder harness",
    )
    run.add_argument(
        "--scripts",
        type=Path,
        help="fake harness script file (default .factory-dev/scripts/<issue>.json)",
    )
    run.add_argument(
        "--data-dir",
        type=Path,
        help="trace and worktree directory (default <repo>/.factory-dev/data)",
    )
    run.add_argument("--org", default="dev")
    run.add_argument("--budget", type=float, default=6.0, help="per-task budget in USD")
    run.add_argument("--skip-self-test", action="store_true")

    im = sub.add_parser("image", help="the per-installation task image")
    im.add_argument("image_command", choices=("build",))
    im.add_argument("--repo-clone", type=Path, required=True)
    im.add_argument("--org", required=True)
    im.add_argument("--repo", required=True)
    im.add_argument("--force", action="store_true")
    im.add_argument("--data-dir", type=Path)

    px = sub.add_parser("proxy", help="the LiteLLM model proxy on Docker Desktop")
    px.add_argument("proxy_command", choices=("up", "down", "status"))
    px.add_argument("--data-dir", type=Path)

    tr = sub.add_parser("trace", help="print a task's events and cost")
    tr.add_argument("--task", required=True, help="task id, e.g. dev/demo#1")
    tr.add_argument("--phase", help="restrict to one phase id")
    tr.add_argument("--data-dir", type=Path)

    for name, text in (
        ("stop", "kill-switch: FACTORY_ENABLED=0; every live process ends within 60 s"),
        ("start", "re-enable the factory: a confirmed control that re-admits nothing"),
    ):
        sw = sub.add_parser(name, help=text)
        sw.add_argument("--reason", required=True, help="one line, recorded on the control")
        sw.add_argument("--by", help="login recorded on the control (default: the OS user)")
        sw.add_argument("--data-dir", type=Path)

    bud = sub.add_parser(
        "budget", help="raise one task's budget once, by at most 2x, with a reason"
    )
    bud.add_argument("--task", required=True, help="task id, e.g. acme/atlas-api#418")
    bud.add_argument(
        "--raise-to", dest="raise_to", type=float, required=True, help="new budget (USD)"
    )
    bud.add_argument("--reason", required=True, help="one line, recorded on the control")
    bud.add_argument("--by", help="login recorded on the control (default: the OS user)")
    bud.add_argument("--data-dir", type=Path)

    dry = sub.add_parser("dry-run", help="replay three issues and derive the per-task budget")
    dry.add_argument("--repo", type=Path, required=True, help="product repository (a git checkout)")
    dry.add_argument("--issues", type=int, nargs="+", required=True, help="three issue numbers")
    dry.add_argument("--harness", choices=("fake", "claude_code", "codex"))
    dry.add_argument("--data-dir", type=Path)
    dry.add_argument("--org", default="dev")
    dry.add_argument("--skip-self-test", action="store_true")

    prot = sub.add_parser(
        "protection", help="check that the base branch requires CODEOWNER review (install)"
    )
    prot.add_argument("--org", required=True)
    prot.add_argument("--repo", required=True)
    prot.add_argument("--branch", help="default: the repository's default branch")
    prot.add_argument("--data-dir", type=Path)

    inst = sub.add_parser("install", help="install the factory on this VM (TRD §2.4)")
    inst.add_argument("--repo-clone", type=Path, required=True)
    inst.add_argument("--org", required=True)
    inst.add_argument("--repo", required=True)
    inst.add_argument("--pin", help="the factory tag this installation runs")
    inst.add_argument("--harness", choices=("roster", "fake"), default="roster")
    inst.add_argument("--scripts-dir", type=Path, help="fake scripts (with --harness fake)")
    inst.add_argument("--etc-dir", type=Path, default=Path("/etc/factory"))
    inst.add_argument("--data-dir", type=Path, default=Path("/var/lib/factory"))
    inst.add_argument("--issues", type=int, nargs="*", default=[], help="three dry-run issues")
    inst.add_argument("--console-host", default="factory.internal")
    inst.add_argument("--public-host")
    inst.add_argument("--acme-email")
    inst.add_argument("--api-port", type=int, default=8080)
    inst.add_argument("--skip-image", action="store_true")
    inst.add_argument("--skip-dry-run", action="store_true")
    inst.add_argument("--skip-protection", action="store_true")
    inst.add_argument("--no-compose", action="store_true")

    upg = sub.add_parser("upgrade", help="upgrade to a pinned tag (release procedure)")
    upg.add_argument("--pin", required=True)
    upg.add_argument("--reason", required=True)
    upg.add_argument("--by")
    upg.add_argument("--factory-root", type=Path, default=Path("/opt/factory"))
    upg.add_argument("--etc-dir", type=Path, default=Path("/etc/factory"))
    upg.add_argument("--data-dir", type=Path, default=Path("/var/lib/factory"))
    upg.add_argument("--drain-minutes", type=int, default=30)

    ev = sub.add_parser("eval", help="the eval set: replay and score (PRD Capability 5)")
    ev_sub = ev.add_subparsers(dest="eval_command")
    ev_run = ev_sub.add_parser("run", help="replay a set on eval/scratch and score it")
    ev_run.add_argument("--repo-clone", type=Path, required=True)
    ev_run.add_argument("--org", default="dev")
    ev_run.add_argument("--repo", default="demo")
    ev_run.add_argument("--fixture", action="store_true", help="the 24-issue fixture set")
    ev_run.add_argument("--set", dest="set_name", help="a set recorded in the trace")
    ev_run.add_argument(
        "--trigger",
        choices=("install_dry_run", "config_pr", "factory_upgrade", "manual", "harness_upgrade"),
        default="manual",
    )
    ev_run.add_argument("--trigger-ref", help="the PR number or tag behind the run")
    ev_run.add_argument("--baseline", help="compare against this run id")
    ev_run.add_argument(
        "--reference-pass-rate",
        type=float,
        help="the pilot's pass rate this installation's gap is measured against",
    )
    ev_run.add_argument("--harness", choices=("roster", "fake"), default="roster")
    ev_run.add_argument("--data-dir", type=Path)

    pg = sub.add_parser("purge", help="null a task's payloads, or pseudonymise a login (TRD §4.4)")
    pg.add_argument("--task", help="task id whose payloads are nulled now")
    pg.add_argument("--login", help="GitHub login shown as user:<hash> from now on")
    pg.add_argument("--by", help="login recorded on the purge event (default: the OS user)")
    pg.add_argument("--data-dir", type=Path)

    ex = sub.add_parser("export", help="signed JSONL export of the events chain (TRD §4.3)")
    ex.add_argument("--task", help="one task; omit for a seq range of the installation")
    ex.add_argument("--from", dest="from_seq", type=int)
    ex.add_argument("--to", dest="to_seq", type=int)
    ex.add_argument("--out", type=Path, help="write here instead of stdout")
    ex.add_argument(
        "--key", type=Path, help="Ed25519 private key (default FACTORY_EXPORT_KEY_FILE)"
    )
    ex.add_argument("--by", help="login recorded on the export event (default: the OS user)")
    ex.add_argument("--data-dir", type=Path)

    ev_add = ev_sub.add_parser("add", help="record a set of closed issues from a JSON list")
    ev_add.add_argument("--set", dest="set_name", required=True, help="the set's name")
    ev_add.add_argument("--repo", required=True, help="the repository the issues belong to")
    ev_add.add_argument(
        "--items",
        type=Path,
        required=True,
        help='JSON list: [{"issue": N, "class": "bug|test|docs|feature", "merged_pr": P}]',
    )
    ev_add.add_argument("--reference-pass-rate", type=float, help="the pilot's pass rate")
    ev_add.add_argument("--data-dir", type=Path)

    oa = sub.add_parser("openapi", help="write or check the committed OpenAPI document")
    oa.add_argument("--out", type=Path, help="write here (default: openapi.json at the root)")
    oa.add_argument("--check", action="store_true", help="exit 1 when the file is stale")

    az = sub.add_parser("authz", help="generate authz.py and authz.ts from authz.yaml")
    az.add_argument("--check", action="store_true", help="exit 1 when a generated file is stale")

    ver = sub.add_parser("verify", help="verify the events hash chain")
    ver.add_argument("--from", dest="from_seq", type=int)
    ver.add_argument("--to", dest="to_seq", type=int)
    ver.add_argument("--data-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file(Path(os.environ.get("FACTORY_ENV_FILE", ".env")))
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version:
        print(f"factory {factory_version()}")
        return 0
    if args.command == "dev" and args.dev_command == "seed":
        from cli.seed import seed_repository

        specs = seed_repository(args.target, with_violations=args.with_violations)
        print(f"seeded {args.target} with {len(specs)} specs")
        return 0
    if args.command == "dev" and args.dev_command == "issues":
        from cli.issues import create_issues

        return create_issues(org=args.org, repo=args.repo, data_dir=args.data_dir)
    if args.command == "serve":
        from cli.serve import serve

        return serve(
            repo_clone=args.repo_clone,
            org=args.org,
            repo=args.repo,
            budget_usd=args.budget,
            harness=args.harness,
            scripts_dir=args.scripts_dir,
            data_dir=args.data_dir,
            bind=args.bind,
            port=args.port,
            with_poller=not args.no_poller,
            poll_interval_s=args.poll_interval,
        )
    if args.command == "run-spec":
        from cli.run_spec import run_spec

        return run_spec(
            repo=args.repo,
            issue=args.issue,
            spec=args.spec,
            harness=args.harness,
            scripts=args.scripts,
            data_dir=args.data_dir,
            org=args.org,
            budget_usd=args.budget,
            skip_self_test=args.skip_self_test,
        )
    if args.command == "image":
        from cli.image import build_image

        return build_image(
            repo_clone=args.repo_clone,
            org=args.org,
            repo=args.repo,
            force=args.force,
            data_dir=args.data_dir,
        )
    if args.command == "proxy":
        from cli.proxy import proxy

        return proxy(args.proxy_command, data_dir=args.data_dir)
    if args.command == "eval" and args.eval_command == "add":
        from cli.eval_cmd import eval_add

        return eval_add(
            set_name=args.set_name,
            repo=args.repo,
            items_file=args.items,
            reference_pass_rate=args.reference_pass_rate,
            data_dir=args.data_dir,
        )
    if args.command == "eval" and args.eval_command == "run":
        from cli.eval_cmd import eval_run

        return eval_run(
            repo_clone=args.repo_clone,
            org=args.org,
            repo=args.repo,
            fixture=args.fixture,
            set_name=args.set_name,
            trigger=args.trigger,
            trigger_ref=args.trigger_ref,
            baseline=args.baseline,
            data_dir=args.data_dir,
            harness_mode=args.harness,
            reference_pass_rate=args.reference_pass_rate,
        )
    if args.command == "install":
        from cli.install import InstallOptions, install

        return install(
            InstallOptions(
                repo_clone=args.repo_clone,
                org=args.org,
                repo=args.repo,
                pin=args.pin,
                harness=args.harness,
                scripts_dir=args.scripts_dir,
                etc_dir=args.etc_dir,
                data_dir=args.data_dir,
                issues=tuple(args.issues),
                skip_image=args.skip_image,
                skip_dry_run=args.skip_dry_run,
                skip_protection=args.skip_protection,
                no_compose=args.no_compose,
                console_host=args.console_host,
                public_host=args.public_host,
                acme_email=args.acme_email,
                api_port=args.api_port,
            )
        )
    if args.command == "upgrade":
        from cli.install import UpgradeOptions, upgrade

        return upgrade(
            UpgradeOptions(
                pin=args.pin,
                reason=args.reason,
                actor=args.by,
                factory_root=args.factory_root,
                etc_dir=args.etc_dir,
                data_dir=args.data_dir,
                drain_minutes=args.drain_minutes,
            )
        )
    if args.command == "protection":
        from cli.protection import check_protection

        return check_protection(
            org=args.org, repo=args.repo, branch=args.branch, data_dir=args.data_dir
        )
    if args.command == "budget":
        from cli.budget import budget

        return budget(
            task_id=args.task,
            raise_to=args.raise_to,
            reason=args.reason,
            actor=args.by,
            data_dir=args.data_dir,
        )
    if args.command == "dry-run":
        from cli.budget import dry_run

        return dry_run(
            repo=args.repo,
            issues=args.issues,
            harness=args.harness,
            data_dir=args.data_dir,
            org=args.org,
            skip_self_test=args.skip_self_test,
        )
    if args.command in ("stop", "start"):
        from cli.kill_switch import kill_switch

        return kill_switch(args.command, reason=args.reason, actor=args.by, data_dir=args.data_dir)
    if args.command == "trace":
        from cli.trace_cmd import trace

        return trace(task_id=args.task, phase_id=args.phase, data_dir=args.data_dir)
    if args.command == "purge":
        from cli.retention_cmd import purge

        return purge(task_id=args.task, login=args.login, by=args.by, data_dir=args.data_dir)
    if args.command == "export":
        from cli.retention_cmd import export

        return export(
            task_id=args.task,
            from_seq=args.from_seq,
            to_seq=args.to_seq,
            out=args.out,
            key=args.key,
            by=args.by,
            data_dir=args.data_dir,
        )
    if args.command == "openapi":
        from cli.openapi_cmd import openapi

        return openapi(out=args.out, check=args.check)
    if args.command == "authz":
        from cli.authz_cmd import authz

        return authz(check_only=args.check)
    if args.command == "verify":
        from cli.trace_cmd import verify

        return verify(from_seq=args.from_seq, to_seq=args.to_seq, data_dir=args.data_dir)
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
