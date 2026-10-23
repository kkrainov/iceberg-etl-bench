import pytest

from m0 import cli
from m0.output import Exit


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit) as e:
        cli.main(["no-such-command"])
    assert e.value.code == 2


def test_purge_requires_yes(capsys):
    assert cli.main(["env", "down", "--purge"]) == Exit.GUARD_REFUSED
    assert "--yes" in capsys.readouterr().out


def test_exit_codes_are_stable():
    # The skill and docs refer to these numbers; changing them is a breaking change.
    assert [int(e) for e in Exit] == [0, 1, 2, 3, 4]
