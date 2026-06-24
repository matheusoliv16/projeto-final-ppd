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

    def register(self, name: str, conn: JsonConnection) -> bool:
        """Reserva um nome sem substituir uma sessão que já está ativa."""
        with self.lock:
            if name in self.clients and self.clients[name] is not conn:
                return False
            self.clients[name] = conn
            return True

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
    contacts: set[str]

    def handle(self) -> None:
        self.conn = JsonConnection(self.request)
        self.contacts = set()
        try:
            for command in self.conn.messages():
                action = command.get("action")
                if action == "register":
                    self._register(
                        str(command.get("username", "")).strip(),
                        command.get("contacts", []),
                    )
                elif not self.username:
                    self.conn.send({"event": "error", "message": "Registre-se primeiro"})
                elif action == "send":
                    self._send(command)
                elif action == "update_contacts":
                    self.contacts = self._valid_contacts(command.get("contacts", []))
                    self.contacts.discard(self.username)
                    self.conn.send({"event": "contacts_updated"})
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

    @staticmethod
    def _valid_contacts(raw) -> set[str]:
        if not isinstance(raw, list):
            return set()
        return {
            str(name).strip() for name in raw
            if str(name).strip() and len(str(name).strip()) <= 30
        }

    def _register(self, username: str, contacts) -> None:
        if not username or len(username) > 30:
            self.conn.send({"event": "error", "message": "Nome inválido"})
            return
        if self.server.registry.get(username) is not None:
            self.conn.send({
                "event": "login_rejected",
                "message": "Esse nome já está em uso.",
            })
            return
        self.contacts = self._valid_contacts(contacts)
        self.contacts.discard(username)
        try:
            self.server.broker.declare_queue(username)
        except Exception as exc:
            self.conn.send({
                "event": "error",
                "message": f"Broker RabbitMQ indisponível: {exc}",
            })
            return
        # A segunda verificação é atômica e cobre dois logins simultâneos.
        if not self.server.registry.register(username, self.conn):
            self.conn.send({
                "event": "login_rejected",
                "message": "Esse nome já está em uso.",
            })
            return
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
        offline_origin = command.get("offline_origin") is True
        if not recipient or not text or len(text) > 2000:
            self.conn.send({"event": "error", "message": "Destinatário ou texto inválido"})
            return
        if recipient not in self.contacts:
            self.conn.send({
                "event": "error",
                "client_id": client_id,
                "message": "Só é permitido enviar mensagens para amigos adicionados.",
            })
            return
        message = {
            "id": str(uuid4()), "client_id": client_id, "from": self.username,
            "to": recipient, "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "offline_origin": offline_origin,
        }
        destination = self.server.registry.get(recipient)
        mode = "online"
        if destination and not offline_origin:
            try:
                destination.send({"event": "message", "message": message})
            except OSError:
                destination = None
        if not destination or offline_origin:
            try:
                self.server.broker.publish(recipient, message)
            except Exception as exc:
                self.conn.send({
                    "event": "error",
                    "message": f"Não foi possível enfileirar no RabbitMQ: {exc}",
                })
                return
            mode = "offline_queue"
            if destination:
                try:
                    for pending in self.server.broker.consume(recipient):
                        pending["delivery"] = "offline_queue"
                        destination.send({"event": "message", "message": pending})
                except OSError:
                    pass
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
