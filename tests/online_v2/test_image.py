"""Optional image acceptance in the test-owned PostgreSQL network namespace."""

import json
import os
from http.client import RemoteDisconnected
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import pytest


@pytest.mark.skipif(
    not os.environ.get("PTCG_V2_TEST_IMAGE"),
    reason="Explicit newly built API image not configured",
)
def test_image_is_nonroot_api_only_and_runs_without_gui_assets(repo, tmp_path):
    image = os.environ["PTCG_V2_TEST_IMAGE"]
    report = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import os,json,importlib.metadata as m; from pathlib import Path; "
            "assert os.getuid()==10001; "
            "names={d.metadata['Name'].lower().replace('_','-') for d in m.distributions()}; "
            "assert not names & {'pillow','torch','numpy','ptcg-ai-runtime','ptcg-desktop-api'}; "
            "assert all(not Path(p).exists() for p in ('/source','/rules','/wheels')); "
            "from ptcg_online_server.api import create_app; "
            "print(json.dumps({'uid':os.getuid(),'packages':sorted(names)}))",
        ],
        text=True,
    )
    assert json.loads(report)["uid"] == 10001
    parent = os.environ["PTCG_V2_TEST_DB_CONTAINER"]
    # Only this fixture's fresh DB is used; the container shares its DB host network.
    secret = tmp_path / "container.env"
    secret.write_text(
        "\n".join(
            [
                "PTCG_ONLINE_V2_DATABASE_URL="
                + repo.settings.database_url.replace(":55484/", ":5432/"),
                "PTCG_ONLINE_V2_SERVER_ID=" + repo.settings.server_id,
                "PTCG_ONLINE_V2_PUBLIC_ORIGIN=http://127.0.0.1:8784",
                "PTCG_ONLINE_V2_ALLOW_LOOPBACK_HTTP=1",
                "",
            ]
        )
    )
    secret.chmod(0o600)
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull",
            "never",
            "--network",
            "container:" + parent,
            "--env-file",
            str(secret),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            image,
        ],
        text=True,
    ).strip()
    try:
        origin = "http://127.0.0.1:8784"
        for _ in range(80):
            try:
                with urlopen(origin + "/healthz", timeout=0.5) as r:
                    assert r.status == 200
                    break
            except (URLError, RemoteDisconnected, TimeoutError):
                time.sleep(0.1)
        else:
            raise AssertionError("Dedicated image did not become alive")
        from urllib.error import HTTPError

        with pytest.raises(HTTPError) as unready:
            urlopen(origin + "/readyz", timeout=5)
        assert json.loads(unready.value.read()) == {
            "ready": False,
            "database": True,
            "rules": False,
        }
        request = Request(
            origin + "/api/online/v2/sessions",
            data=b'{"protocolVersion":"2.0"}',
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5) as r:
            assert r.status == 201
            tokens = json.loads(r.read())
        request = Request(
            origin + "/api/online/v2/sessions/current",
            headers={"Authorization": "Bearer " + tokens["accessToken"]},
        )
        with urlopen(request, timeout=5) as r:
            assert json.loads(r.read())["userId"] == tokens["userId"]
        for path in ("/", "/api/battle/session", "/api/cards", "/api/collection"):
            with pytest.raises(HTTPError) as absent:
                urlopen(origin + path, timeout=5)
            assert absent.value.code == 404
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        secret.unlink(missing_ok=True)
