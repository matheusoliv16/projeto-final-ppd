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

    def declare_queue(self, client):
        with self.lock:
            self.queues.setdefault(client, [])

    def publish(self, client, message):
        with self.lock:
            self.queues.setdefault(client, []).append(message)

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

    def client(self, name):
        sock = socket.create_connection(("127.0.0.1", self.chat_port))
        sock.settimeout(2)
        conn = JsonConnection(sock)
        conn.send({"action": "register", "username": name})
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
        alice, alice_events = self.client("alice")
        bob, bob_events = self.client("bob")
        self.next_kind(alice_events, "presence")

        alice.send({"action": "send", "to": "bob", "text": "online", "client_id": "1"})
        received = self.next_kind(bob_events, "message")
        self.assertEqual("online", received["message"]["text"])
        self.assertEqual("online", self.next_kind(alice_events, "sent")["mode"])

        bob.send({"action": "logout"}); bob.close()
        self.next_kind(alice_events, "presence")
        alice.send({"action": "send", "to": "bob", "text": "offline", "client_id": "2"})
        self.assertEqual("offline_queue", self.next_kind(alice_events, "sent")["mode"])

        self.assertEqual(1, len(self.broker.queues["bob"]))
        bob2, bob2_events = self.client("bob")
        recovered = self.next_kind(bob2_events, "message")
        self.assertEqual("offline", recovered["message"]["text"])
        self.assertEqual("offline_queue", recovered["message"]["delivery"])
        alice.close(); bob2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
