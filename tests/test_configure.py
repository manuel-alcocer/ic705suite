"""ic705suite --configure / --show-config: the settings asked for by the installers."""

import sys
import tomllib

import pytest

from ic705suite import configure
from ic705suite.config import load_config, set_values, write_template


class Script(configure.Questions):
    """Answers the questions in order and keeps what was said."""

    def __init__(self, *answers: str, secrets: tuple[str, ...] = ()):
        self.answers, self.secrets, self.said, self.asked = list(answers), list(secrets), [], []
        super().__init__(ask=self._answer, secret=self._secret, write=self.said.append)

    def _answer(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0)

    def _secret(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.secrets.pop(0) if self.secrets else ""      # Enter


def test_set_values_keeps_the_rest_of_the_file():
    text = ('# top\n[rig]\n# How\nconnection = "lan"\nhost = "1.2.3.4"\n# password = "x"\n\n'
            '[keyer]\nwpm = 20\n')
    new = set_values(text, {("rig", "host"): "10.0.0.9", ("rig", "password"): 'p"q',
                            ("keyer", "tx_enabled"): True, ("ui", "language"): "es"})
    assert new.startswith('# top\n[rig]\n# How\nconnection = "lan"\nhost = "10.0.0.9"\n')
    data = tomllib.loads(new)
    assert data["rig"]["password"] == 'p"q' and data["keyer"] == {"wpm": 20, "tx_enabled": True}
    assert data["ui"] == {"language": "es"}


def test_a_new_setup_asks_and_writes_the_template(tmp_path):
    path = tmp_path / "config.toml"
    script = Script("es", "ea7xyz/p", "lan", "192.168.1.84", "manuel", "", "light", "s",
                    secrets=("secreto", "arriba"))
    assert configure.configure(path, script, language="en") == 0
    config = load_config(path)
    assert (config.language, config.callsign, config.connection) == ("es", "EA7XYZ/P", "lan")
    assert (config.host, config.username, config.password) == ("192.168.1.84", "manuel",
                                                                 "secreto")
    assert config.tx_enabled is False and config.theme == "light"
    assert config.swr_remote_password == "arriba"     # the phone may switch on the remote
    text = path.read_text(encoding="utf-8")
    assert "# Antenna tuning page" in text and "{token}" not in text   # the whole template
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0        # it holds the radio's password
    # After the language, the questions are in Spanish.
    assert any("Tu indicativo" in prompt for prompt in script.asked)


def test_enter_keeps_the_current_values(tmp_path):
    path = write_template(tmp_path / "config.toml")
    path.write_text(path.read_text(encoding="utf-8").replace(
        'callsign = "N0CALL"', 'callsign = "EA7KLX"'), encoding="utf-8")
    before = load_config(path)
    script = Script("", "", "", "", "", "", "", "", secrets=("",))
    assert configure.configure(path, script, language="en") == 0
    after = load_config(path)
    assert (after.callsign, after.host, after.username, after.password, after.tx_enabled) == \
        (before.callsign, before.host, before.username, before.password, before.tx_enabled)
    # The current settings were shown first, password hidden.
    shown = "\n".join(script.said)
    assert "EA7KLX" in shown and "already set up" in shown
    assert before.password not in shown and configure.MASK in shown
    assert script.asked[1] == "Your callsign [EA7KLX]: "


def test_tx_enabled_stays_where_cwic705_put_it(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[rig]\nconnection = "sim"\n\n[station]\ncallsign = "EA7KLX"\n\n'
                    '[keyer]\nwpm = 20\ntx_enabled = false\n', encoding="utf-8")
    assert configure.configure(path, Script("en", "", "", "y", "", ""), language="en") == 0
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["keyer"]["tx_enabled"] is True and "tx_enabled" not in data["rig"]
    assert "host" not in data["rig"]          # the simulator asks no network questions


def test_a_wrong_answer_is_asked_again(tmp_path):
    path = tmp_path / "config.toml"
    script = Script("fr", "en", "not a call", "EA7XYZ", "usb", "sim", "maybe", "n", "pink",
                    "dark", "y")
    assert configure.configure(path, script, language="en") == 0
    assert load_config(path).connection == "sim" and load_config(path).theme == "dark"
    assert any("Choose one of" in line for line in script.said)
    assert any("does not look like a callsign" in line for line in script.said)


@pytest.mark.parametrize("answer", ["n", KeyboardInterrupt, EOFError])
def test_nothing_is_written_unless_confirmed(tmp_path, answer):
    path = write_template(tmp_path / "config.toml")
    text = path.read_text(encoding="utf-8")

    def ask(prompt):
        if prompt.startswith("Save them"):
            if isinstance(answer, str):
                return answer
            raise answer
        return ""

    questions = configure.Questions(ask=ask, secret=lambda prompt: "", write=lambda line: None)
    code = configure.configure(path, questions, language="en")
    assert code == (0 if answer == "n" else 130)
    assert path.read_text(encoding="utf-8") == text


def test_show_config(tmp_path):
    path = tmp_path / "config.toml"
    lines = []
    assert configure.show_config(path, lines.append, language="en") == 1
    assert "No settings yet" in lines[0]
    write_template(path)
    lines.clear()
    assert configure.show_config(path, lines.append, language="en") == 0
    shown = "\n".join(lines)
    assert "N0CALL" in shown and "192.168.1.50" in shown and "password" not in shown


def test_without_a_terminal_it_does_not_ask(tmp_path, monkeypatch, capsys):
    from ic705suite.cli import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["--configure", "--config", str(tmp_path / "c.toml"), "--lang", "en"]) == 1
    assert "No terminal" in capsys.readouterr().err
    assert not (tmp_path / "c.toml").exists()
