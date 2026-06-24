# Projeto Final PPD — Mensagens online e offline

Sistema distribuído em Python com interface Tkinter, sockets TCP e **RabbitMQ como broker
MOM real**. O RabbitMQ mantém uma fila durável e mensagens persistentes para cada cliente.

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

## Administração do RabbitMQ

Para listar as filas diretamente no broker:

```powershell
docker exec ppd-rabbitmq rabbitmqctl list_queues name messages durable
```

As filas também podem ser consultadas pelo painel em `http://localhost:15672`.

## Funcionamento das mensagens offline

Ao entrar, cada cliente solicita seu registro ao servidor. O servidor declara uma fila durável
no RabbitMQ usando o nome codificado do contato. Mensagens destinadas a clientes desconectados
são publicadas nessa fila com `delivery_mode=2`.

Quando o destinatário se conecta novamente, o servidor consome suas mensagens e envia a
confirmação `ACK` somente depois da entrega pelo socket. Se a conexão falhar antes da
confirmação, o RabbitMQ mantém ou reenfileira a mensagem.

Se o próprio remetente escrever enquanto estiver offline, a mensagem permanece temporariamente
na caixa de saída local, pois não existe conexão com o servidor remoto. Na reconexão, ela é
marcada como originada offline e encaminhada obrigatoriamente pelo RabbitMQ, inclusive quando
o destinatário já estiver online.

## Lista de contatos

O cliente envia sua lista de contatos ao registrar-se e sempre que ela é alterada. O servidor
valida cada envio e rejeita destinatários que não estejam nessa lista. Receber uma mensagem não
adiciona automaticamente o remetente aos contatos; inclusão e exclusão são ações explícitas na
interface.

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
