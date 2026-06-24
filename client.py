"""Cliente gráfico Tkinter do mensageiro distribuído."""

from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from protocol import JsonConnection


class NetworkClient:
    """Camada de rede. A thread receptora nunca manipula widgets diretamente."""

    def __init__(self, host: str, port: int, events: queue.Queue):
        self.host, self.port, self.events = host, port, events
        self.connection: JsonConnection | None = None
        self.username = ""
        self.online = False

    def connect(self, username: str, contacts: list[str]) -> None:
        if self.online:
            return
        sock = socket.create_connection((self.host, self.port), timeout=5)
        self.connection = JsonConnection(sock)
        self.username = username
        self.connection.send({
            "action": "register",
            "username": username,
            "contacts": contacts,
        })
        stream = self.connection.messages()
        try:
            first_event = next(stream)
        except StopIteration as exc:
            self.connection.close()
            self.connection = None
            raise ConnectionError("O servidor encerrou a conexão durante o login.") from exc
        if first_event.get("event") != "registered":
            message = first_event.get("message", "Não foi possível entrar no sistema.")
            self.connection.close()
            self.connection = None
            raise ValueError(message)
        sock.settimeout(None)
        self.online = True
        self.events.put(first_event)
        threading.Thread(
            target=self._receive,
            args=(stream,),
            daemon=True,
            name="receptor-chat",
        ).start()

    def _receive(self, stream) -> None:
        try:
            for event in stream:
                self.events.put(event)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        finally:
            was_online = self.online
            self.online = False
            self.events.put({"event": "disconnected", "unexpected": was_online})

    def send(self, recipient: str, text: str, client_id: str,
             offline_origin: bool = False) -> None:
        if not self.online or not self.connection:
            raise ConnectionError("Cliente offline")
        self.connection.send({
            "action": "send",
            "to": recipient,
            "text": text,
            "client_id": client_id,
            "offline_origin": offline_origin,
        })

    def sync_contacts(self, contacts: list[str]) -> None:
        if self.online and self.connection:
            self.connection.send({"action": "update_contacts", "contacts": contacts})

    def ask_status(self, contact: str) -> None:
        if self.online and self.connection:
            self.connection.send({"action": "status", "contact": contact})

    def disconnect(self) -> None:
        connection, self.connection = self.connection, None
        self.online = False
        if connection:
            try:
                connection.send({"action": "logout"})
                connection.close()
            except OSError:
                pass


class Profile:
    """Contatos, histórico e caixa de saída local persistidos em JSON."""

    def __init__(self, username: str):
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)
        self.path = Path("data") / f"profile_{safe}.json"
        self.username = username
        self.contacts: list[str] = []
        self.history: dict[str, list[dict]] = {}
        self.outbox: list[dict] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.contacts = data.get("contacts", [])
                self.history = data.get("history", {})
                self.outbox = data.get("outbox", [])
            except (OSError, json.JSONDecodeError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"contacts": self.contacts, "history": self.history, "outbox": self.outbox}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_message(self, other: str, message: dict) -> None:
        messages = self.history.setdefault(other, [])
        client_id = message.get("client_id")
        if client_id and any(m.get("client_id") == client_id for m in messages):
            return
        messages.append(message)
        self.save()


class ChatApp:
    BG, PANEL, NAVY = "#f1f5f9", "#ffffff", "#102a43"
    BLUE, GREEN, GRAY, RED = "#2563eb", "#16a34a", "#64748b", "#dc2626"
    BORDER, MUTED = "#d8e1eb", "#6b7c93"

    def __init__(self, root: tk.Tk, host: str, port: int):
        self.root, self.host, self.port = root, host, port
        self.events: queue.Queue = queue.Queue()
        self.network = NetworkClient(host, port, self.events)
        self.profile: Profile | None = None
        self.presence: dict[str, bool] = {}
        self.selected: str | None = None
        self.root.title("Mensageiro PPD")
        self.root.geometry("980x650")
        self.root.minsize(820, 540)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_style()
        self._login()
        self.root.after(80, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Contacts.Treeview", rowheight=38, font=("Segoe UI", 10),
                        background=self.PANEL, fieldbackground=self.PANEL, borderwidth=0)
        style.configure("Contacts.Treeview.Heading", background=self.NAVY,
                        foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Contacts.Treeview",
                  background=[("selected", self.BLUE)],
                  foreground=[("selected", "white")])

    def _login(self) -> None:
        frame = tk.Frame(self.root, bg=self.PANEL, padx=45, pady=38,
                         highlightthickness=1, highlightbackground="#d8e0ea")
        frame.place(relx=.5, rely=.5, anchor="center")
        tk.Label(frame, text="Mensageiro PPD", bg=self.PANEL, fg=self.NAVY,
                 font=("Segoe UI", 23, "bold")).pack()
        tk.Label(frame, text="Sistema distribuído com mensagens offline", bg=self.PANEL,
                 fg=self.GRAY, font=("Segoe UI", 10)).pack(pady=(2, 24))
        tk.Label(frame, text="Nome de contato", bg=self.PANEL, fg="#263238").pack(anchor="w")
        self.name_entry = tk.Entry(frame, width=32, font=("Segoe UI", 11), relief="solid", bd=1)
        self.name_entry.pack(ipady=6, pady=(5, 12))
        self.name_entry.bind("<Return>", lambda _e: self._enter(frame))
        tk.Button(frame, text="Entrar", command=lambda: self._enter(frame), bg=self.BLUE,
                  fg="white", activebackground=self.NAVY, activeforeground="white",
                  relief="flat", font=("Segoe UI", 10, "bold"), padx=40, pady=8).pack(fill="x")
        self.name_entry.focus_set()

    def _enter(self, login_frame: tk.Frame) -> None:
        name = self.name_entry.get().strip()
        if not name or len(name) > 30:
            messagebox.showwarning("Nome inválido", "Informe um nome com até 30 caracteres.")
            return
        self.profile = Profile(name)
        try:
            self.network.connect(name, self.profile.contacts)
        except ValueError as exc:
            messagebox.showwarning("Nome indisponível", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("Servidor indisponível", f"Não foi possível conectar:\n{exc}")
            return
        login_frame.destroy()
        self._build_chat()

    def _build_chat(self) -> None:
        top = tk.Frame(self.root, bg=self.NAVY, height=72)
        top.pack(fill="x")
        top.pack_propagate(False)
        brand = tk.Frame(top, bg=self.NAVY)
        brand.pack(side="left", padx=22, pady=12)
        tk.Label(brand, text="Mensageiro PPD", bg=self.NAVY, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(brand, text=f"Conectado como {self.profile.username}", bg=self.NAVY,
                 fg="#b9cbe0", font=("Segoe UI", 9)).pack(anchor="w")
        self.state_button = tk.Button(top, text="Conectando...", command=self._toggle_state,
                                      bg=self.GRAY, fg="white", relief="flat", padx=14, pady=6)
        self.state_button.pack(side="right", padx=22)

        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=18, pady=18)
        left = tk.Frame(body, bg=self.PANEL, width=260, padx=12, pady=12,
                        highlightthickness=1, highlightbackground=self.BORDER)
        left.pack(side="left", fill="y", padx=(0, 14)); left.pack_propagate(False)
        tk.Label(left, text="Contatos", bg=self.PANEL, fg=self.NAVY,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(left, text="Sua lista de conversas", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))
        self.contacts = ttk.Treeview(left, columns=("state",), show="tree",
                                     style="Contacts.Treeview", selectmode="browse")
        self.contacts.pack(fill="both", expand=True)
        self.contacts.bind("<<TreeviewSelect>>", self._select_contact)
        add_row = tk.Frame(left, bg=self.PANEL)
        add_row.pack(fill="x", pady=(8, 5))
        self.contact_entry = tk.Entry(add_row, relief="solid", bd=1)
        self.contact_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.contact_entry.bind("<Return>", lambda _e: self._add_contact())
        tk.Button(add_row, text="＋", command=self._add_contact, bg=self.BLUE, fg="white",
                  relief="flat", width=3).pack(side="right", padx=(5, 0), ipady=3)
        tk.Button(left, text="Remover contato", command=self._remove_contact, bg="#eef2f7",
                  fg=self.NAVY, activebackground="#e2e8f0", relief="flat", pady=7).pack(fill="x")

        right = tk.Frame(body, bg=self.PANEL, padx=18, pady=14,
                         highlightthickness=1, highlightbackground=self.BORDER)
        right.pack(side="left", fill="both", expand=True)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self.chat_title = tk.Label(right, text="Selecione um contato", bg=self.PANEL,
                                   fg=self.NAVY, font=("Segoe UI", 13, "bold"), anchor="w")
        self.chat_title.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.chat = tk.Text(right, state="disabled", bg="#f8fafc", fg="#263238",
                            relief="flat", bd=0, padx=18, pady=16, wrap="word",
                            highlightthickness=1, highlightbackground=self.BORDER,
                            font=("Segoe UI", 10))
        self.chat.grid(row=1, column=0, sticky="nsew")
        self.chat.tag_configure("mine_name", foreground=self.BLUE,
                                font=("Segoe UI", 9, "bold"), justify="right")
        self.chat.tag_configure("other_name", foreground=self.NAVY,
                                font=("Segoe UI", 9, "bold"))
        self.chat.tag_configure("mine_text", background="#dbeafe", lmargin1=80, lmargin2=80,
                                rmargin=8, spacing1=4, spacing3=12, justify="right")
        self.chat.tag_configure("other_text", background="#e9eef5", lmargin1=8, lmargin2=8,
                                rmargin=80, spacing1=4, spacing3=12)
        self.chat.tag_configure("pending", foreground=self.MUTED,
                                font=("Segoe UI", 8, "italic"), justify="right")

        composer = tk.Frame(right, bg=self.PANEL)
        composer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        composer.grid_columnconfigure(0, weight=1)
        tk.Label(composer, text="MENSAGEM", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8, "bold")).grid(row=0, column=0, columnspan=2,
                                                     sticky="w", pady=(0, 5))
        self.message_entry = tk.Text(
            composer,
            height=3,
            font=("Segoe UI", 11),
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            wrap="word",
            highlightthickness=1,
            highlightbackground="#b8c5d4",
            highlightcolor=self.BLUE,
        )
        self.message_entry.grid(row=1, column=0, sticky="ew", ipady=3)
        self.message_entry.bind("<Return>", self._message_key)
        tk.Button(composer, text="Enviar", command=self._send_message, bg=self.BLUE,
                  fg="white", relief="flat", padx=22,
                  activebackground="#1d4ed8", activeforeground="white",
                  font=("Segoe UI", 10, "bold")).grid(
                      row=1, column=1, sticky="ns", padx=(10, 0)
                  )
        tk.Label(composer, text="Enter para enviar  •  Shift+Enter para nova linha",
                 bg=self.PANEL, fg="#94a3b8", font=("Segoe UI", 8)).grid(
                     row=2, column=0, columnspan=2, sticky="w", pady=(5, 0)
                 )
        self._refresh_contacts()

    def _toggle_state(self) -> None:
        if self.network.online:
            self.network.disconnect()
            self._set_state(False)
        else:
            try:
                self.network.connect(self.profile.username, self.profile.contacts)
                self.state_button.config(text="Conectando...", bg=self.GRAY)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Falha de conexão", str(exc))

    def _set_state(self, online: bool) -> None:
        if online:
            self.state_button.config(text="● ONLINE  |  Ficar offline", bg=self.GREEN)
        else:
            pending = len(self.profile.outbox)
            suffix = f" ({pending} pendente(s))" if pending else ""
            self.state_button.config(text="○ OFFLINE  |  Ficar online" + suffix, bg=self.GRAY)
            self.presence = {name: False for name in self.profile.contacts}
        self._refresh_contacts()

    def _add_contact(self) -> None:
        name = self.contact_entry.get().strip()
        if not name or name == self.profile.username or name in self.profile.contacts:
            return
        self.profile.contacts.append(name)
        self.profile.save(); self.contact_entry.delete(0, "end")
        self.network.sync_contacts(self.profile.contacts)
        self.network.ask_status(name); self._refresh_contacts()

    def _remove_contact(self) -> None:
        selection = self.contacts.selection()
        if not selection:
            return
        name = selection[0]
        if messagebox.askyesno("Remover contato", f"Remover {name} da lista?\nO histórico será preservado."):
            self.profile.contacts.remove(name); self.profile.save()
            self.network.sync_contacts(self.profile.contacts)
            if self.selected == name:
                self.selected = None; self._render_history()
            self._refresh_contacts()

    def _refresh_contacts(self) -> None:
        if not hasattr(self, "contacts"):
            return
        selected = self.selected
        self.contacts.delete(*self.contacts.get_children())
        for name in self.profile.contacts:
            marker = "●" if self.presence.get(name, False) else "○"
            state = "online" if self.presence.get(name, False) else "offline"
            self.contacts.insert("", "end", iid=name, text=f"  {marker}   {name}   ·   {state}")
        if selected in self.profile.contacts:
            self.contacts.selection_set(selected)

    def _select_contact(self, _event=None) -> None:
        selection = self.contacts.selection()
        self.selected = selection[0] if selection else None
        self._render_history()

    def _render_history(self) -> None:
        self.chat.config(state="normal"); self.chat.delete("1.0", "end")
        if not self.selected:
            self.chat_title.config(text="Selecione um contato")
        else:
            state = "online" if self.presence.get(self.selected) else "offline"
            self.chat_title.config(text=f"Conversa com {self.selected}  •  {state}")
            for msg in self.profile.history.get(self.selected, []):
                try:
                    time = datetime.fromisoformat(msg["timestamp"]).astimezone().strftime("%H:%M")
                except (KeyError, ValueError):
                    time = "--:--"
                who = "Você" if msg.get("from") == self.profile.username else msg.get("from", "?")
                mine = msg.get("from") == self.profile.username
                name_tag = "mine_name" if mine else "other_name"
                text_tag = "mine_text" if mine else "other_text"
                self.chat.insert("end", f"{who}  ·  {time}\n", name_tag)
                self.chat.insert("end", f"{msg.get('text', '')}\n", text_tag)
                if msg.get("local_pending"):
                    self.chat.insert("end", "aguardando conexão\n", "pending")
        self.chat.config(state="disabled"); self.chat.see("end")

    def _message_key(self, event) -> str | None:
        """Enter envia; Shift+Enter insere uma nova linha no campo."""
        if event.state & 0x0001:
            return None
        self._send_message()
        return "break"

    def _send_message(self) -> None:
        text = self.message_entry.get("1.0", "end-1c").strip()
        if not self.selected:
            messagebox.showwarning("Contato", "Selecione um contato para conversar."); return
        if not text:
            return
        client_id = str(uuid4())
        if self.network.online:
            try:
                self.network.send(self.selected, text, client_id)
            except OSError as exc:
                messagebox.showerror("Envio", str(exc)); return
        else:
            pending = {"client_id": client_id, "from": self.profile.username, "to": self.selected,
                       "text": text, "timestamp": datetime.now().astimezone().isoformat(),
                       "local_pending": True}
            self.profile.outbox.append(pending)
            self.profile.add_message(self.selected, pending)
            self._set_state(False); self._render_history()
        self.message_entry.delete("1.0", "end")

    def _flush_outbox(self) -> None:
        for msg in list(self.profile.outbox):
            try:
                self.network.send(
                    msg["to"], msg["text"], msg["client_id"], offline_origin=True
                )
            except OSError:
                break

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "registered":
            self.presence = {name: name in event.get("online", []) for name in self.profile.contacts}
            self._set_state(True); self._flush_outbox()
        elif kind == "presence":
            self.presence[event["contact"]] = event["online"]
            self._refresh_contacts(); self._render_history()
        elif kind == "message":
            msg = event["message"]; other = msg["from"]
            self.profile.add_message(other, msg)
            if self.selected == other:
                self._render_history()
        elif kind == "sent":
            msg, client_id = event["message"], event.get("client_id")
            for old in self.profile.history.get(msg["to"], []):
                if old.get("client_id") == client_id:
                    old.update(msg); old.pop("local_pending", None)
                    break
            else:
                self.profile.add_message(msg["to"], msg)
            self.profile.outbox = [m for m in self.profile.outbox if m.get("client_id") != client_id]
            self.profile.save(); self._set_state(True)
            if self.selected == msg["to"]:
                self._render_history()
        elif kind == "error":
            messagebox.showerror("Servidor", event.get("message", "Erro desconhecido"))
        elif kind == "disconnected" and self.profile:
            self._set_state(False)

    def close(self) -> None:
        self.network.disconnect()
        if self.profile:
            self.profile.save()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cliente gráfico do Mensageiro PPD")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    root = tk.Tk()
    ChatApp(root, args.host, args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
