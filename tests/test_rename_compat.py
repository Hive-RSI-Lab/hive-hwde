"""The ai-ee -> hwde rename must not strand an existing setup.

Brief: "anything that reads a tool/skill name from state, attestations,
waivers or learnings accepts both names". Concretely, every environment
variable the skill reads is now spelled HWDE_*, and every one of them is
still read under its pre-rename AIEE_* spelling, with HWDE_ winning when both
are set. Each case here sets its own variables and asserts BOTH the new name
and the old one resolve, so neither passes on the other's leftovers.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import distributors  # noqa: E402
import jlcapi  # noqa: E402
from lib import env  # noqa: E402

PINS = ("KICAD_CLI", "KICAD_ROOT", "JAVA", "FREEROUTING_JAR", "KRT_DIR",
        "NGSPICE_DLL", "PDFLATEX")


def _clear(monkeypatch, *suffixes):
    for s in suffixes:
        monkeypatch.delenv("HWDE_" + s, raising=False)
        monkeypatch.delenv("AIEE_" + s, raising=False)


def test_skill_env_prefers_hwde_and_falls_back_to_aiee(monkeypatch):
    _clear(monkeypatch, "KICAD_CLI")
    assert env.skill_env("KICAD_CLI") == (None, "HWDE_KICAD_CLI")

    monkeypatch.setenv("HWDE_KICAD_CLI", "/new")
    assert env.skill_env("KICAD_CLI") == ("/new", "HWDE_KICAD_CLI")

    # Pre-rename spelling alone still resolves, and names itself in the result
    # so an error message points at the variable the user actually set.
    _clear(monkeypatch, "KICAD_CLI")
    monkeypatch.setenv("AIEE_KICAD_CLI", "/old")
    assert env.skill_env("KICAD_CLI") == ("/old", "AIEE_KICAD_CLI")

    # Both set: HWDE_ wins.
    monkeypatch.setenv("HWDE_KICAD_CLI", "/new")
    assert env.skill_env("KICAD_CLI") == ("/new", "HWDE_KICAD_CLI")


def test_every_toolchain_pin_accepts_the_old_spelling(monkeypatch):
    for suffix in PINS:
        _clear(monkeypatch, suffix)
        monkeypatch.setenv("AIEE_" + suffix, "/legacy/" + suffix)
        assert env.skill_env(suffix) == ("/legacy/" + suffix, "AIEE_" + suffix)


def test_a_bad_legacy_pin_still_fails_loudly_naming_the_old_var(monkeypatch):
    _clear(monkeypatch, "JAVA")
    monkeypatch.setenv("AIEE_JAVA", "/nonexistent/java")
    with __import__("pytest").raises(env.EnvError, match="AIEE_JAVA"):
        env.find_java()


def test_jlcpcb_credentials_accept_either_spelling(monkeypatch):
    suffixes = ("JLCPCB_APPID", "JLCPCB_KEY", "JLCPCB_SECRET")
    _clear(monkeypatch, *suffixes)
    assert [v for v in jlcapi.PROBE_ENV if not jlcapi.cred_env(v)] \
        == list(jlcapi.PROBE_ENV)

    for s in suffixes:
        monkeypatch.setenv("AIEE_" + s, "old-" + s)
    assert [v for v in jlcapi.PROBE_ENV if not jlcapi.cred_env(v)] == []
    assert jlcapi.cred_env("HWDE_JLCPCB_KEY") == "old-JLCPCB_KEY"

    monkeypatch.setenv("HWDE_JLCPCB_KEY", "new-key")
    assert jlcapi.cred_env("HWDE_JLCPCB_KEY") == "new-key"


def test_distributor_credentials_accept_either_spelling(monkeypatch):
    suffixes = ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET",
                "MOUSER_API_KEY")
    _clear(monkeypatch, *suffixes)
    assert distributors.missing_credentials("digikey") \
        == list(distributors.DIGIKEY_ENV)
    assert distributors.missing_credentials("mouser") \
        == list(distributors.MOUSER_ENV)

    monkeypatch.setenv("AIEE_DIGIKEY_CLIENT_ID", "id")
    monkeypatch.setenv("AIEE_DIGIKEY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AIEE_MOUSER_API_KEY", "key")
    assert distributors.missing_credentials("digikey") == []
    assert distributors.missing_credentials("mouser") == []

    # A half-set legacy pair is still reported missing by its current name.
    monkeypatch.delenv("AIEE_DIGIKEY_CLIENT_SECRET")
    assert distributors.missing_credentials("digikey") \
        == ["HWDE_DIGIKEY_CLIENT_SECRET"]
