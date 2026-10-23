from m0.doctor import GIB, kernel_version, parse_size, wslconfig_checks
from m0.output import OK, WARN


def by_name(checks):
    return {c.name: c for c in checks}


def test_parse_size():
    assert parse_size("12GB") == 12 * GIB
    assert parse_size("512MB") == 512 * 1024**2
    assert parse_size("12582912000") == 12582912000
    assert parse_size("0") == 0
    assert parse_size("lots") is None


def test_kernel_version():
    assert kernel_version("6.18.54.1-microsoft-standard-WSL2") == (6, 18)
    assert kernel_version("6.1.0-amzn") == (6, 1)
    assert kernel_version("weird") == (0, 0)


def test_wslconfig_recommended_with_inline_comment_and_crlf():
    text = (
        "[wsl2]\r\nmemory=12GB\r\nprocessors=12\r\n"
        "swap=0              # benchmark runs only\r\n"
        "[experimental]\r\nautoMemoryReclaim=disabled"
    )
    checks = by_name(wslconfig_checks(text))
    assert all(c.status == OK for c in checks.values()), checks
    assert checks["wsl.swap"].detail == "0"


def test_wslconfig_defaults_warn():
    checks = by_name(wslconfig_checks("[wsl2]\nmemory=8GB\n"))
    assert checks["wsl.memory"].status == WARN
    assert checks["wsl.processors"].status == WARN
    assert checks["wsl.swap"].status == WARN
    assert checks["wsl.autoMemoryReclaim"].status == WARN
