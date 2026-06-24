"""Teste opcional de infraestrutura: requer RabbitMQ iniciado."""

import unittest
from uuid import uuid4

from rabbit_broker import RabbitBroker


class TestRabbitMQ(unittest.TestCase):
    def test_publish_and_consume(self):
        broker = RabbitBroker()
        client = f"teste-{uuid4()}"
        try:
            broker.declare_queue(client)
            broker.publish(client, {"text": "mensagem de teste"})
            messages = list(broker.consume(client))
            self.assertEqual([{"text": "mensagem de teste"}], messages)
        finally:
            broker.delete_queue(client)


if __name__ == "__main__":
    unittest.main(verbosity=2)
