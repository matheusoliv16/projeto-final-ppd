"""Servidor central: presença, entrega online e roteamento para o MOM."""

from __future__ import annotations

import argparse
import socketserver
import threading
from datetime import datetime, timezone
from uuid import uuid4

from protocol import JsonConnection
from rabbit_broker import RabbitBroker


class PresenceRegistry:
    def __init__(self):
        self.clients: dict[str, JsonConnection] = {}
        self.lock = threading.RLock()

    def register(self, name: str, conn: JsonConnection) -> JsonConnection | None:
        with self.lock:
            previous = self.clients.get(name)
            self.clients[name] = conn
            return previous

    def unregister(self, name: str, conn: JsonConnection) -> bool:
        with self.lock:
            if self.clients.get(name) is conn:
                del self.clients[name]
                return True
            return False

    def get(self, name: str) -> JsonConnection | None:
        with self.lock:
            return self.clients.get(name)

    def names(self) -> list[str]:
        with self.lock:
            return list(self.clients)

    def broadcast(self, event: dict, except_name: str | None = None) -> None:
        with self.lock:
            snapshot = list(self.clients.items())
        for name, conn in snapshot:
            if name != except_name:
                try:
                    conn.send(event)
                except OSError:
                    pass


class ChatHandler(socketserver.BaseRequestHandler):
    username: str | None = None

    def handle(self) -> None:
        self.conn = JsonConnection(self.request)
        try:
            for command in self.conn.messages():
                action = command.get("action")
                if action == "register":
                    self._register(str(command.get("username", "")).strip())
                elif not self.username:
                    self.conn.send({"event": "error", "message": "Registre-se primeiro"})
                elif action == "send":
                    self._send(command)
                elif action == "status":
                    contact = str(command.get("contact", ""))
                    self.conn.send({"event": "presence", "contact": contact,
                                    "online": self.server.registry.get(contact) is not None})
                elif action == "logout":
                    break
                else:
                    self.conn.send({"event": "error", "message": "Comando inválido"})
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            if self.username and self.server.registry.unregister(self.username, self.conn):
                print(f"[CHAT] {self.username} ficou offline")
                self.server.registry.broadcast(
                    {"event": "presence", "contact": self.username, "online": False},
                    except_name=self.username,
                )

    def _register(self, username: str) -> None:
        if not username or len(username) > 30:
            self.conn.send({"event": "error", "message": "Nome inválido"})
            return
        try:
            self.server.broker.declare_queue(username)
        except Exception as exc:
            self.conn.send({
                "event": "error",
                "message": f"Broker RabbitMQ indisponível: {exc}",
            })
            return
        previous = self.server.registry.register(username, self.conn)
        if previous and previous is not self.conn:
            try:
                previous.send({"event": "error", "message": "Contato conectado em outra janela"})
                previous.close()
            except OSError:
                pass
        self.username = username
        online = self.server.registry.names()
        self.conn.send({"event": "registered", "username": username, "online": online})
        self.server.registry.broadcast(
            {"event": "presence", "contact": username, "online": True}, except_name=username
        )
        recovered = 0
        for message in self.server.broker.consume(username):
            message["delivery"] = "offline_queue"
            self.conn.send({"event": "message", "message": message})
            recovered += 1
        print(f"[CHAT] {username} online | {recovered} mensagem(ns) recuperada(s)")

    def _send(self, command: dict) -> None:
        recipient = str(command.get("to", "")).strip()
        text = str(command.get("text", "")).strip()
        client_id = str(command.get("client_id", ""))
        if not recipient or not text or len(text) > 2000:
            self.conn.send({"event": "error", "message": "Destinatário ou texto inválido"})
            return
        message = {
            "id": str(uuid4()), "client_id": client_id, "from": self.username,
            "to": recipient, "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        destination = self.server.registry.get(recipient)
        mode = "online"
        if destination:
            try:
                destination.send({"event": "message", "message": message})
            except OSError:
                destination = None
        if not destination:
            try:
                self.server.broker.publish(recipient, message)
            except Exception as exc:
                self.conn.send({
                    "event": "error",
                    "message": f"Não foi possível enfileirar no RabbitMQ: {exc}",
                })
                return
            mode = "offline_queue"
        self.conn.send({"event": "sent", "client_id": client_id, "message": message, "mode": mode})
        print(f"[CHAT] {self.username} -> {recipient} ({mode})")


class ChatServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, broker: RabbitBroker):
        self.registry = PresenceRegistry()
        self.broker = broker
        super().__init__(address, ChatHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor de chat PPD")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--rabbit-host", default="127.0.0.1")
    parser.add_argument("--rabbit-port", type=int, default=5672)
    parser.add_argument("--rabbit-user", default="ppd")
    parser.add_argument("--rabbit-password", default="ppd123")
    args = parser.parse_args()
    broker = RabbitBroker(args.rabbit_host, args.rabbit_port,
                          args.rabbit_user, args.rabbit_password)
    try:
        broker.check()
    except Exception as exc:
        raise SystemExit(
            f"RabbitMQ indisponível em {args.rabbit_host}:{args.rabbit_port}: {exc}\n"
            "Inicie-o com: docker compose up -d"
        )
    server = ChatServer((args.host, args.port), broker)
    print(f"[CHAT] ativo em {args.host}:{args.port} | RabbitMQ: "
          f"{args.rabbit_host}:{args.rabbit_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[CHAT] encerrado")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
