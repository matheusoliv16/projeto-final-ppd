"""Teste de integração sem GUI: entrega online e recuperação da fila offline."""

import socket
import threading
import unittest

from protocol import JsonConnection
from server import ChatServer


class FakeBroker:
    """Broker em memória usado apenas para testar o servidor sem infraestrutura externa."""

    def __init__(self):
        self.queues = {}
        self.lock = threading.Lock()
        self.published = []

    def declare_queue(self, client):
        with self.lock:
            self.queues.setdefault(client, [])

    def publish(self, client, message):
        with self.lock:
            self.queues.setdefault(client, []).append(message)
            self.published.append((client, message))

    def consume(self, client):
        while True:
            with self.lock:
                queue = self.queues.setdefault(client, [])
                if not queue:
                    return
                message = queue[0]
            yield message
            with self.lock:
                if self.queues[client] and self.queues[client][0] is message:
                    self.queues[client].pop(0)


class TestMessagingFlow(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.chat = ChatServer(("127.0.0.1", 0), self.broker)
        self.chat_port = self.chat.server_address[1]
        threading.Thread(target=self.chat.serve_forever, daemon=True).start()

    def tearDown(self):
        self.chat.shutdown(); self.chat.server_close()

    def client(self, name, contacts=None):
        sock = socket.create_connection(("127.0.0.1", self.chat_port))
        sock.settimeout(2)
        conn = JsonConnection(sock)
        conn.send({"action": "register", "username": name,
                   "contacts": contacts or []})
        stream = conn.messages()
        self.assertEqual("registered", next(stream)["event"])
        return conn, stream

    @staticmethod
    def next_kind(stream, kind):
        for event in stream:
            if event.get("event") == kind:
                return event
        raise AssertionError(f"Evento {kind} não recebido")

    def test_online_and_offline_delivery(self):
        alice, alice_events = self.client("alice", ["bob"])
        bob, bob_events = self.client("bob", ["alice"])
        self.next_kind(alice_events, "presence")

        alice.send({"action": "send", "to": "bob", "text": "online", "client_id": "1"})
        received = self.next_kind(bob_events, "message")
        self.assertEqual("online", received["message"]["text"])
        self.assertEqual("online", self.next_kind(alice_events, "sent")["mode"])

        # Mensagem composta quando o remetente estava offline deve passar pelo broker,
        # mesmo que o destinatário esteja online no momento da reconexão.
        alice.send({"action": "send", "to": "bob", "text": "composta offline",
                    "client_id": "offline-sender", "offline_origin": True})
        broker_delivery = self.next_kind(bob_events, "message")
        self.assertEqual("offline_queue", broker_delivery["message"]["delivery"])
        self.assertEqual("offline_queue", self.next_kind(alice_events, "sent")["mode"])
        self.assertTrue(any(message["client_id"] == "offline-sender"
                            for _client, message in self.broker.published))

        bob.send({"action": "logout"}); bob.close()
        self.next_kind(alice_events, "presence")
        alice.send({"action": "send", "to": "bob", "text": "offline", "client_id": "2"})
        self.assertEqual("offline_queue", self.next_kind(alice_events, "sent")["mode"])

        self.assertEqual(1, len(self.broker.queues["bob"]))
        bob2, bob2_events = self.client("bob", ["alice"])
        recovered = self.next_kind(bob2_events, "message")
        self.assertEqual("offline", recovered["message"]["text"])
        self.assertEqual("offline_queue", recovered["message"]["delivery"])
        alice.close(); bob2.close()

    def test_rejects_recipient_not_in_contacts(self):
        alice, events = self.client("alice", [])
        alice.send({"action": "send", "to": "bob", "text": "não autorizado",
                    "client_id": "blocked"})
        error = self.next_kind(events, "error")
        self.assertIn("amigos adicionados", error["message"])
        self.assertFalse(self.broker.queues.get("bob"))
        alice.close()

    def test_rejects_duplicate_name_without_disconnecting_original(self):
        alice, alice_events = self.client("alice", ["bob"])

        duplicate_socket = socket.create_connection(("127.0.0.1", self.chat_port))
        duplicate_socket.settimeout(2)
        duplicate = JsonConnection(duplicate_socket)
        duplicate.send({"action": "register", "username": "alice", "contacts": []})
        rejected = next(duplicate.messages())
        self.assertEqual("login_rejected", rejected["event"])
        self.assertEqual("Esse nome já está em uso.", rejected["message"])

        # A sessão original continua registrada e consegue consultar o servidor.
        alice.send({"action": "status", "contact": "bob"})
        self.assertEqual("presence", self.next_kind(alice_events, "presence")["event"])
        duplicate.close(); alice.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
