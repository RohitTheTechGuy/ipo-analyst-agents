"""launchd install/uninstall (ticket 23). Never touches the real LaunchAgents dir or launchctl."""
import plistlib
import subprocess

from scheduler import install


class RecordingRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, args):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, self.returncode, stdout="", stderr="")


def test_install_writes_a_weekday_1030_plist_and_bootstraps_it(tmp_path):
    agents, repo, runner = tmp_path / "LaunchAgents", tmp_path / "repo", RecordingRunner()

    install.install(repo=repo, agents_dir=agents, runner=runner, uid=501)

    path = agents / "com.ipoagents.daily.plist"
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == "com.ipoagents.daily"
    assert plist["ProgramArguments"] == [str(repo / ".venv/bin/python"), str(repo / "run_daily.py")]
    assert plist["WorkingDirectory"] == str(repo)
    assert plist["StartCalendarInterval"] == [{"Weekday": d, "Hour": 10, "Minute": 30} for d in range(1, 6)]
    env = plist["EnvironmentVariables"]
    assert env["DYLD_FALLBACK_LIBRARY_PATH"] == "/opt/homebrew/lib"
    assert env["PATH"].split(":")[:4] == ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    assert plist["StandardOutPath"].startswith(str(repo / "logs") + "/")
    assert plist["StandardErrorPath"].startswith(str(repo / "logs") + "/")
    assert ["launchctl", "bootstrap", "gui/501", str(path)] in runner.calls


def test_install_escapes_paths_for_xml(tmp_path):
    repo = tmp_path / "R&D <ipo>"

    install.install(repo=repo, agents_dir=tmp_path / "LA", runner=RecordingRunner(), uid=501)

    plist = plistlib.loads((tmp_path / "LA" / "com.ipoagents.daily.plist").read_bytes())
    assert plist["WorkingDirectory"] == str(repo)


def test_uninstall_boots_out_and_removes_the_plist(tmp_path):
    agents, runner = tmp_path / "LaunchAgents", RecordingRunner()
    install.install(repo=tmp_path / "repo", agents_dir=agents, runner=runner, uid=501)
    path = agents / "com.ipoagents.daily.plist"

    install.uninstall(agents_dir=agents, runner=runner, uid=501)

    assert runner.calls[-1] == ["launchctl", "bootout", "gui/501", str(path)]
    assert not path.exists()
