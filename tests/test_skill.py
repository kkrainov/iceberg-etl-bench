"""The skill must only reference commands, actions, flags and exit codes that exist."""

import argparse
import re
from pathlib import Path

import yaml

from m0.cli import build_parser
from m0.output import Exit
from m0.paths import REPO_ROOT

SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "m0-bench"
SKILL_FILES = sorted(SKILL_DIR.rglob("*.md"))
# `m0 <command> [<action>] [--flags…]` inside inline code or code blocks.
# `docker compose -p m0 …` names the Compose project, not the CLI.
INVOCATION = re.compile(r"(?<!-p )\bm0 ([a-z][a-z-]*)((?: [a-z][a-z-]*)?)([^`\n]*)")


def subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("no subcommands")


def code_fragments(text: str) -> list[str]:
    blocks = re.findall(r"```.*?```", text, re.DOTALL)
    rest = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return blocks + re.findall(r"`([^`\n]+)`", rest)


def invocations() -> list[tuple[Path, str, str, list[str]]]:
    found = []
    for path in SKILL_FILES:
        for fragment in code_fragments(path.read_text()):
            for line in fragment.splitlines():
                for m in INVOCATION.finditer(line):
                    flags = re.findall(r"(--[a-z][a-z-]*)", m.group(3))
                    found.append((path, m.group(1), m.group(2).strip(), flags))
    return found


def test_skill_files_exist():
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_skill_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "m0-bench"
    assert len(front["description"]) > 50


def test_skill_invocations_exist():
    subs = subcommands()
    problems = []
    assert invocations(), "skill mentions no m0 commands"
    for path, command, action, flags in invocations():
        where = path.relative_to(REPO_ROOT)
        if command not in subs:
            problems.append(f"{where}: unknown command `m0 {command}`")
            continue
        sub = subs[command]
        options = {s for a in sub._actions for s in a.option_strings}
        positional_choices = {c for a in sub._actions if not a.option_strings and a.choices for c in a.choices}
        if action and positional_choices and action not in positional_choices:
            problems.append(f"{where}: unknown action `m0 {command} {action}`")
        problems += [f"{where}: unknown flag `{f}` for `m0 {command}`" for f in flags if f not in options]
    assert not problems, "\n".join(problems)


def test_skill_exit_codes_exist():
    valid = {int(e) for e in Exit}
    for path in SKILL_FILES:
        for code in re.findall(r"exit code `?(\d+)`?", path.read_text(), re.IGNORECASE):
            assert int(code) in valid, f"{path.name}: exit code {code} does not exist"
