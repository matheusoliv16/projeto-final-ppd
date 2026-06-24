"""Protocolo JSON delimitado por quebra de linha usado pelos sockets do projeto."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Iterator


class JsonConnection:
    """Encapsula um socket e garante envios JSON atômicos entre threads."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.reader = sock.makefile("r", encoding="utf-8", newline="\n")
        self._send_lock = threading.Lock()

    def send(self, data: dict[str, Any]) -> None:
        raw = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
        with self._send_lock:
            self.sock.sendall(raw)

    def messages(self) -> Iterator[dict[str, Any]]:
        for line in self.reader:
            if line.strip():
                yield json.loads(line)

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.sock.close()


def request(host: str, port: int, payload: dict[str, Any], timeout: float = 5) -> dict[str, Any]:
    """Executa uma requisição curta e recebe uma única resposta."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        conn = JsonConnection(sock)
        conn.send(payload)
        return next(conn.messages())
