# Projeto Final PPD — Mensagens online e offline

Sistema distribuído em Python com interface Tkinter, sockets TCP e **RabbitMQ como broker
MOM real**. O RabbitMQ mantém uma fila durável e mensagens persistentes para cada cliente.

Consulte também o [resumo para apresentação](APRESENTACAO.md).

## Arquitetura

```text
Cliente A ── socket TCP ──┐
                          ├── Servidor de chat ── AMQP ── RabbitMQ
Cliente B ── socket TCP ──┘                         ├── fila/alice
                                                   └── fila/bob
```

- `client.py`: GUI, contatos, histórico, presença e caixa de saída local.
- `server.py`: servidor remoto, registro de presença e roteamento.
- `rabbit_broker.py`: adaptador AMQP, via Kombu, entre o servidor e o RabbitMQ.
- `protocol.py`: protocolo JSON dos sockets cliente/servidor.
- `compose.yaml`: instalação reproduzível do RabbitMQ com painel de gerenciamento.
- `test_integration.py`: teste do fluxo completo com um broker simulado.
- `test_rabbitmq.py`: teste que publica e consome no RabbitMQ real.

## Pré-requisitos

- Python 3.10 ou superior, com Tkinter;
- Docker Desktop em execução.

## Preparação — uma única vez

Abra o terminal na pasta do projeto e instale a biblioteca AMQP:

```powershell
cd Projeto_Final_PPD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Nos novos terminais, execute `.\.venv\Scripts\Activate.ps1` antes dos comandos Python.
Se o PowerShell bloquear a ativação, use diretamente `.\.venv\Scripts\python.exe` no lugar
de `python`.

## Como executar pelo terminal

### Terminal 1 — broker RabbitMQ

```powershell
cd Projeto_Final_PPD
docker compose up
```

Espere o RabbitMQ ficar pronto. O painel do broker estará disponível em
`http://localhost:15672`, com usuário `ppd` e senha `ppd123`.

### Terminal 2 — servidor de chat

```powershell
cd Projeto_Final_PPD
python server.py
```

Saída esperada:

```text
[CHAT] ativo em 0.0.0.0:5050 | RabbitMQ: 127.0.0.1:5672
```

### Terminais 3 e 4 — interfaces dos clientes

Execute uma vez para cada cliente:

```powershell
cd Projeto_Final_PPD
python client.py
```

Cada comando abre uma janela gráfica independente. Entre com nomes diferentes, como `alice`
e `bob`. Também é possível usar `python client.py --name alice`.

Para encerrar e preservar as filas:

```powershell
docker compose down
```

O volume `rabbitmq_data` mantém as mensagens. Para apagar também os dados do broker:

```powershell
docker compose down -v
```

## Execução em computadores diferentes

O cliente remoto aponta para o IP do servidor:

```powershell
python client.py --host 192.168.0.10
```

O servidor também pode apontar para outro host RabbitMQ:

```powershell
python server.py --rabbit-host 192.168.0.20
```

## Roteiro de demonstração

1. Inicie RabbitMQ, servidor e dois clientes.
2. Entre como `alice` e `bob`; confira no painel RabbitMQ as duas filas `ppd.messages.*`.
3. Adicione os contatos e envie uma mensagem com ambos online: a entrega é direta.
4. Coloque Bob offline e envie outra mensagem por Alice.
5. Veja a fila de Bob ganhar uma mensagem no painel RabbitMQ.
6. Reconecte Bob: a mensagem aparece na GUI e sai da fila após o ACK.

Para comprovar pelo terminal que as filas pertencem ao RabbitMQ:

```powershell
docker exec ppd-rabbitmq rabbitmqctl list_queues name messages durable
```

Outra prova simples é parar apenas o servidor Python depois de enfileirar uma mensagem. A fila
e a mensagem continuam visíveis no painel do RabbitMQ. Ao reiniciar `server.py` e reconectar o
destinatário, a mensagem é consumida normalmente.

## Requisitos atendidos

1. Nome no login e contatos permanentemente visíveis na GUI.
2. Alternância online/offline pelo botão no cabeçalho.
3. Entrega imediata pela conexão TCP quando o destinatário está online.
4. Servidor remoto acessado por socket TCP.
5. Fila RabbitMQ durável individual para cada cliente.
6. Publicação AMQP na fila do destinatário offline.
7. `queue_declare` idempotente no registro de cada cliente.
8. Inclusão e exclusão de contatos com persistência local.

As mensagens RabbitMQ usam `delivery_mode=2` (persistente). O servidor envia `ACK` somente depois
de entregá-las ao socket do cliente; se a conexão falhar, o broker as recoloca na fila.

## Testes

Teste rápido da lógica, sem infraestrutura externa:

```powershell
python -m unittest -v test_integration.py
```

Teste do RabbitMQ real, com o contêiner iniciado:

```powershell
python -m unittest -v test_rabbitmq.py
```

## Dados locais

O arquivo `data/profile_NOME.json` guarda contatos, histórico e caixa de saída do cliente.
As filas offline ficam no volume Docker do RabbitMQ, não em arquivos ou listas do projeto.
