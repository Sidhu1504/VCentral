"""Script variables: {{NAME}} in a script is replaced by a value chosen when the task is started.

Values are always inserted as ONE quoted literal for the target shell, so a value can never add commands:
    bash:        systemctl restart {{SERVICE}}   ->  systemctl restart 'nginx'
    PowerShell:  Restart-Service {{SERVICE}}     ->  Restart-Service 'W32Time'
Write the placeholder without quotes around it; vCentral adds them.
"""
import re

PATTERN = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]{0,31})\s*\}\}")
MAX_VALUE = 1000
# PowerShell treats these as single quotes too; inside a single-quoted string each is escaped by doubling it.
PS_QUOTES = "'‘’‚‛"


class VariableError(ValueError):
    pass


def find(*scripts: str | None) -> list[str]:
    """Variable names used in the scripts, in order of first use (case-insensitive, reported upper-case)."""
    seen: list[str] = []
    for text in scripts:
        for m in PATTERN.finditer(text or ""):
            name = m.group(1).upper()
            if name not in seen:
                seen.append(name)
    return seen


def quote_bash(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def quote_powershell(value: str) -> str:
    return "'" + "".join(c * 2 if c in PS_QUOTES else c for c in value) + "'"


def _clean(values: dict | None, needed: list[str]) -> dict[str, str]:
    given = {str(k).strip().upper(): ("" if v is None else str(v)) for k, v in (values or {}).items()}
    missing = [n for n in needed if not given.get(n, "").strip()]
    if missing:
        raise VariableError(f"Enter a value for: {', '.join(missing)}")
    for n in needed:
        v = given[n]
        if len(v) > MAX_VALUE:
            raise VariableError(f"{n} is too long (max {MAX_VALUE} characters)")
        if "\x00" in v:
            raise VariableError(f"{n} contains an invalid character")
    return {n: given[n] for n in needed}


def render(linux: str | None, windows: str | None, values: dict | None) -> tuple[str | None, str | None]:
    """Fill in variables. Raises VariableError if one is missing or invalid."""
    needed = find(linux, windows)
    if not needed:
        return linux, windows
    vals = _clean(values, needed)

    def fill(text: str | None, quote) -> str | None:
        if text is None:
            return None
        return PATTERN.sub(lambda m: quote(vals[m.group(1).upper()]), text)

    return fill(linux, quote_bash), fill(windows, quote_powershell)
