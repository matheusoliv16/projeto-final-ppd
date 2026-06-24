# Resumo para apresentação

## Visão geral

O sistema possui três componentes:

1. **Cliente gráfico:** nome, contatos, conversas e controle online/offline.
2. **Servidor de chat:** presença e decisão entre entrega direta ou enfileiramento.
3. **RabbitMQ:** broker MOM real que armazena mensagens offline em filas duráveis.

Os clientes usam sockets TCP com pacotes JSON. O servidor comunica-se com RabbitMQ usando o
protocolo AMQP por meio da biblioteca `Kombu`. Kombu é apenas o cliente AMQP; o broker que
armazena e gerencia as filas é o processo RabbitMQ executado no Docker.

## Fluxo online

```text
Alice -> socket -> Servidor de chat -> socket aberto de Bob -> Bob
```

O `PresenceRegistry` informa que Bob está conectado. O servidor usa a conexão persistente e a
mensagem chega imediatamente, sem passar pelo broker.

## Fluxo offline

```text
Alice -> Servidor -> AMQP publish -> RabbitMQ -> fila de Bob
Bob reconecta <- socket <- Servidor <- AMQP consume + ACK
```

Se Bob estiver offline, o servidor publica uma mensagem persistente na fila durável dele. Ao
reconectar, o servidor consome a fila. O ACK só é enviado após a entrega ao socket de Bob; em
caso de falha, RabbitMQ recoloca a mensagem na fila.

## Requisitos

1. **Nome e contatos:** o login define o nome; `Profile` persiste a lista exibida na lateral.
2. **Online/offline:** `_toggle_state` registra ou desconecta o cliente do servidor.
3. **Entrega instantânea:** `_send` usa `destination.send` quando encontra o destinatário.
4. **Servidor remoto:** `server.py`, porta 5050, recebe comandos JSON por socket TCP.
5. **MOM por cliente:** RabbitMQ possui uma fila durável `ppd.messages.*` para cada contato.
6. **Contato offline:** `_send` chama `RabbitBroker.publish` para a fila do destinatário.
7. **Fila no login:** `_register` chama `RabbitBroker.declare_queue` antes do registro online.
8. **Gerência de contatos:** os botões chamam `_add_contact` e `_remove_contact`.

## O que torna o RabbitMQ um broker

- Recebe mensagens de um produtor, o servidor de chat.
- Roteia pelo nome da fila do destinatário.
- Armazena mensagens persistentes enquanto não há consumidor.
- Entrega em ordem FIFO.
- Exige confirmação do consumidor por ACK.
- Reenfileira entregas não confirmadas.
- Fornece painel próprio para inspecionar filas e mensagens.

## Demonstração recomendada

1. Mostre o terminal do RabbitMQ, o servidor e duas interfaces.
2. Abra `http://localhost:15672` e mostre que ainda não existem filas.
3. Entre como Alice e Bob e atualize o painel: haverá uma fila para cada um.
4. Troque uma mensagem online e mostre que as filas continuam vazias.
5. Coloque Bob offline e envie uma mensagem: a fila dele passa a conter uma mensagem.
6. Reconecte Bob: ele recebe a mensagem e o contador da fila volta a zero.

## Conceitos de PPD utilizados

- Processos independentes e comunicação TCP.
- Protocolo de aplicação JSON.
- AMQP e Middleware Orientado a Mensagens.
- Concorrência com uma thread por conexão.
- Exclusão mútua no registro de presença.
- Filas duráveis, mensagens persistentes e confirmação manual.
- Estratégia *store and forward* para clientes offline.
