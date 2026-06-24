"""Adaptador AMQP entre o servidor de chat e o broker RabbitMQ."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from urllib.parse import quote

from kombu import Connection, Producer, Queue


class RabbitBroker:
    """Cria, publica e consome filas RabbitMQ usando o cliente AMQP Kombu."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5672,
        username: str = "ppd",
        password: str = "ppd123",
        virtual_host: str = "/",
    ):
        user = quote(username, safe="")
        secret = quote(password, safe="")
        vhost = quote(virtual_host, safe="")
        self.url = f"amqp://{user}:{secret}@{host}:{port}/{vhost}"

    @staticmethod
    def queue_name(client: str) -> str:
        """Codifica o contato em um nome de fila seguro e reversível."""
        encoded = base64.urlsafe_b64encode(client.encode("utf-8")).decode("ascii").rstrip("=")
        return f"ppd.messages.{encoded}"

    def _connection(self) -> Connection:
        return Connection(
            self.url,
            heartbeat=30,
            connect_timeout=10,
            transport_options={"confirm_publish": True},
        )

    @staticmethod
    def _bound_queue(channel, name: str):
        queue = Queue(name=name, routing_key=name, durable=True, auto_delete=False)
        bound = queue(channel)
        bound.declare()
        return bound

    def check(self) -> None:
        with self._connection() as connection:
            connection.ensure_connection(max_retries=3)

    def declare_queue(self, client: str) -> str:
        """Cria idempotentemente uma fila durável para o cliente."""
        name = self.queue_name(client)
        with self._connection() as connection:
            connection.ensure_connection(max_retries=3)
            with connection.channel() as channel:
                self._bound_queue(channel, name)
        print(f"[RABBITMQ] fila declarada: {name} ({client})")
        return name

    def publish(self, client: str, message: dict) -> None:
        """Publica uma mensagem persistente na fila do destinatário."""
        name = self.queue_name(client)
        with self._connection() as connection:
            connection.ensure_connection(max_retries=3)
            with connection.channel() as channel:
                queue = self._bound_queue(channel, name)
                Producer(channel).publish(
                    message,
                    exchange="",
                    routing_key=name,
                    serializer="json",
                    delivery_mode=2,
                    mandatory=True,
                    declare=[queue],
                    retry=True,
                    retry_policy={"max_retries": 3, "interval_start": 0, "interval_step": 1},
                )
        print(f"[RABBITMQ] mensagem persistida em {name}")

    def delete_queue(self, client: str) -> None:
        """Remove uma fila; usado somente na limpeza do teste de infraestrutura."""
        with self._connection() as connection:
            connection.ensure_connection(max_retries=3)
            with connection.channel() as channel:
                channel.queue_delete(queue=self.queue_name(client))

    def consume(self, client: str) -> Iterator[dict]:
        """Entrega pendências e confirma cada uma somente após o envio ao cliente.

        O ``yield`` acontece antes do ACK. Se o socket falhar, a conexão fecha com a
        entrega sem confirmação e o RabbitMQ recoloca a mensagem na fila.
        """
        name = self.queue_name(client)
        with self._connection() as connection:
            connection.ensure_connection(max_retries=3)
            with connection.channel() as channel:
                queue = self._bound_queue(channel, name)
                channel.basic_qos(0, 1, False)
                while True:
                    message = queue.get(no_ack=False)
                    if message is None:
                        break
                    yield message.payload
                    message.ack()
                    print(f"[RABBITMQ] mensagem confirmada em {name}")
