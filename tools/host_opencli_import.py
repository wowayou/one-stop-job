from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib import error, parse, request


def _load_json(url: str) -> dict:
    with request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _replace_opencli(command: list[str], opencli_path: str | None) -> list[str]:
    if not command:
        raise SystemExit("Source command is empty.")
    if opencli_path:
        return [opencli_path, *command[1:]] if Path(command[0]).name.lower().startswith("opencli") else command
    return command


def _is_placeholder_opencli(candidate: str) -> bool:
    normalized = candidate.replace("\\", "/").lower()
    return normalized.startswith("c:/path/to/") or "<path>" in normalized or "yourname" in normalized


def _resolve_opencli(cli_arg: str | None = None, command: list[str] | None = None) -> str | None:
    command = command or []
    command_name = command[0] if command else None
    candidates = [cli_arg, command_name, "opencli.cmd", "opencli"]
    for candidate in candidates:
        if not candidate:
            continue
        candidate = str(candidate).strip()
        if not candidate or _is_placeholder_opencli(candidate):
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if Path(candidate).exists():
            return candidate
    return None


def _ensure_csv(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise SystemExit(f"OpenCLI did not produce a CSV file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        try:
            rows = list(csv.reader(f))
        except csv.Error as exc:
            raise SystemExit(f"OpenCLI output is not valid CSV: {exc}") from exc
    if len(rows) < 2:
        raise SystemExit("OpenCLI produced CSV headers but no job rows.")


def _decode_cli_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _post_file(api_base: str, source_label: str, csv_path: Path) -> dict:
    boundary = "----one-stop-job-boundary"
    url = f"{api_base.rstrip('/')}/api/jobs/import?{parse.urlencode({'source': source_label})}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{csv_path.name}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + csv_path.read_bytes() + footer
    req = request.Request(url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


@contextmanager
def _single_collection_lock(lock_path: Path, max_age_seconds: int = 600) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        age = time.time() - lock_path.stat().st_mtime
        if age < max_age_seconds:
            raise SystemExit(
                f"Another host collection appears to be running: {lock_path}. "
                "Wait for it to finish, or delete the stale lock file if no collection is running."
            )
        lock_path.unlink()
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run host OpenCLI collection and import the CSV into one-stop-job.")
    parser.add_argument("--source", default="boss", help="Source key from /api/sources, for example boss or zhilian.")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Backend base URL.")
    parser.add_argument("--opencli", default=None, help="Optional host OpenCLI path, e.g. C:\\Users\\YourName\\AppData\\Roaming\\npm\\opencli.cmd")
    parser.add_argument("--out", default=None, help="Optional CSV output path to keep.")
    parser.add_argument("--ignore-disabled", action="store_true", help="Run even if the source is disabled in config.")
    args = parser.parse_args()

    source_key = args.source.strip().lower()
    try:
        sources_payload = _load_json(f"{args.api.rstrip('/')}/api/sources")
    except error.URLError as exc:
        raise SystemExit(f"Cannot reach backend at {args.api}: {exc}") from exc

    source = next((item for item in sources_payload if str(item.get("key", "")).lower() == source_key), None)
    if not source:
        raise SystemExit(f"Unknown source: {source_key}")
    if not source.get("enabled") and not args.ignore_disabled:
        raise SystemExit(f"Source {source_key} is disabled in config. Enable it first or pass --ignore-disabled.")
    config = source.get("config") or {}
    command = [str(item) for item in config.get("command") or []]
    source_label = str(source.get("label") or source_key)
    opencli_path = _resolve_opencli(args.opencli, command)
    if not opencli_path:
        raise SystemExit(
            "OpenCLI was not found on this host. Pass --opencli or add it to PATH. "
            "On Windows, `where opencli` usually shows the .cmd path."
        )

    command = _replace_opencli(command, opencli_path)
    output_path = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"one_stop_job_{source_key}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = Path(tempfile.gettempdir()) / "one_stop_job_opencli_collection.lock"
    with _single_collection_lock(lock_path):
        print(f"Running {source_label}: {' '.join(command)}")
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            stderr = _decode_cli_output(completed.stderr).strip()
            raise SystemExit(f"OpenCLI failed with exit code {completed.returncode}.\n{stderr}")

        output_path.write_text(_decode_cli_output(completed.stdout), encoding="utf-8")
        _ensure_csv(output_path)
        result = _post_file(args.api, source_label, output_path)
    print(
        f"Imported {result.get('fetched', 0)} rows: "
        f"{result.get('created', 0)} created / {result.get('updated', 0)} updated."
    )
    print(f"CSV: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
