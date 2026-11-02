"""An app-scoped Ollama process, without changing the user's global settings."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path


class OllamaService:
    def __init__(self, cache_dir: Path, port: int = 11435):
        self.url = f"http://127.0.0.1:{port}"
        self.cache_dir = cache_dir
        self.port = port
        self.process = None
        self._log = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def start(self) -> str:
        # Never silently attach to an unrelated service whose cloud policy is unknown.
        try:
            with self._opener.open(self.url + "/api/version", timeout=1):
                raise RuntimeError(f"Port {self.port} is already in use. Close the previous Recall instance.")
        except OSError:
            pass
        executable = shutil.which("ollama")
        if not executable:
            raise RuntimeError("Install Ollama, then add the local qwen3.5:9b model during setup.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._log = (self.cache_dir / "ollama.log").open("ab")
        environment = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{self.port}",
                           OLLAMA_NO_CLOUD="1", OLLAMA_NUM_PARALLEL="1")
        self.process = subprocess.Popen(
            [executable, "serve"], env=environment, cwd=self.cache_dir,
            stdout=self._log, stderr=self._log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(90):
            if self.process.poll() is not None:
                self.stop()
                raise RuntimeError("The local Ollama service could not start. See .tubelive/ollama.log.")
            try:
                with self._opener.open(self.url + "/api/version", timeout=1) as response:
                    json.load(response)
                    return self.url
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError("The local Ollama service did not become ready.")

    def stop(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                # Release this service's GPU runner before terminating the server.
                try:
                    with self._opener.open(self.url + '/api/ps', timeout=3) as response:
                        names = [m['name'] for m in json.load(response).get('models', []) if isinstance(m.get('name'), str)]
                    for name in names:
                        request = urllib.request.Request(self.url + "/api/generate",
                            data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                            headers={"Content-Type": "application/json"})
                        with self._opener.open(request, timeout=8):
                            pass
                except OSError:
                    pass
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
        if self._log:
            self._log.close()
            self._log = None
