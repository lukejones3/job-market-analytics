"""Airflow task client for the local, peer-authenticated Human Repo broker.

No Human Repo database URL, encryption key, or memory text enters Airflow.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys

SOCKET = "/tmp/human-repo-airflow/jobs.sock"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=("general", "lander", "wyloc", "metronome-arthritis"))
    parser.add_argument("stage")
    parser.add_argument("run_id")
    parser.add_argument("attempt", type=int)
    args = parser.parse_args()
    request = {"op": "run", "project": args.project, "stage": args.stage,
               "run_id": args.run_id, "attempt": args.attempt}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(7200)
        connection.connect(SOCKET)
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        with connection.makefile("r", encoding="utf-8") as stream:
            response = json.loads(stream.readline(8192))
    print(json.dumps({key: response.get(key) for key in
                      ("ok", "job_id", "project", "stage", "deduplicated",
                       "duration_seconds", "error") if key in response}, sort_keys=True))
    if not response.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
