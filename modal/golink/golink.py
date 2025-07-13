import subprocess

import modal

app = modal.App(name="golinks")

vol = modal.Volume.from_name("golinks-data", create_if_missing=True)

image = modal.Image.from_registry(
    "golang:1.24.0-bookworm",
    add_python="3.10",
).run_commands(["go install -v github.com/tailscale/golink/cmd/golink@latest"])


@app.cls(
    image=image,
    secrets=[modal.Secret.from_name("golinks")],
    volumes={"/root/.config/golink": vol},
    min_containers=1,
    max_containers=1,
)
class Golinks:
    @modal.enter()
    def start_golinks(self):
        subprocess.Popen(
            [
                "golink",
                "-verbose",
                "--sqlitedb",
                "/root/.config/golink/golink.db",
                "--config-dir",
                "/root/.config/golink/tsconfig",
            ]
        )
