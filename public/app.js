"use strict";
const $ = (id) => document.getElementById(id);
const storageKey = "relay-session";
let session = null, socket = null, recipient = null, directory = [];
let retry = 0, reconnectTimer, heartbeat, active = false, ready = false;
let historyGeneration = 0;
const conversations = new Map(), unread = new Map(), pending = new Map();
const encoder = new TextEncoder();

function restoreSession() {
  try { session = JSON.parse(sessionStorage.getItem(storageKey)); } catch { session = null; }
  if (session && (!session.token || !session.user)) session = null;
}
function setError(message) { $("chat-error").textContent = message || ""; }
function connection(connected, label) {
  ready = connected;
  $("connection-status").textContent = label;
  $("connection-status").dataset.connected = String(connected);
  $("message").disabled = !connected || !recipient;
  $("send").disabled = !connected || !recipient;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(session ? { Authorization: "Bearer " + session.token } : {}) },
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && active) leave("Your session expired. Choose a username to join again.");
    throw new Error(data.error || "Something went wrong. Please try again.");
  }
  return data;
}
function start() {
  active = true;
  $("welcome").hidden = true;
  $("chat").hidden = false;
  $("session-tools").hidden = false;
  $("my-name").textContent = session.user.username;
  $("my-avatar").textContent = session.user.username.slice(0, 2).toUpperCase();
  connect();
}
function connect() {
  if (!active) return;
  connection(false, retry ? "Reconnecting…" : "Connecting…");
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: session.token }));
  ws.onmessage = ({ data }) => {
    if (socket !== ws) return;
    let event;
    try { event = JSON.parse(data); } catch { return; }
    if (event.type === "ready") {
      retry = 0;
      connection(true, "Connected");
      setError("");
      clearInterval(heartbeat);
      heartbeat = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}');
      }, 15000);
      if (recipient) loadHistory(recipient.id);
    } else if (event.type === "users") {
      directory = event.users.filter((user) => user.id !== session.user.id);
      if (recipient) recipient = directory.find((user) => user.id === recipient.id) || recipient;
      renderPeople();
      renderRecipient();
    } else if (event.type === "message") {
      const message = event.message;
      const other = message.from === session.user.id ? message.to : message.from;
      if (message.from === session.user.id && pending.has(message.nonce)) {
        clearTimeout(pending.get(message.nonce).timer);
        pending.delete(message.nonce);
      }
      remember(other, [message]);
      if (recipient && recipient.id === other) renderMessages();
      else if (message.from !== session.user.id) {
        unread.set(other, (unread.get(other) || 0) + 1);
        renderPeople();
      }
    } else if (event.type === "error") {
      if (event.error === "Unauthorized") leave("Your session expired. Choose a username to join again.");
      else setError(event.error);
    }
  };
  ws.onclose = (event) => {
    if (socket !== ws || !active) return;
    clearInterval(heartbeat);
    connection(false, "Reconnecting…");
    if (event.code === 1008) { leave("This session could not connect. Please join again."); return; }
    for (const item of pending.values()) item.failed = true;
    renderMessages();
    reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 15000));
  };
  ws.onerror = () => ws.close();
}
function leave(message = "") {
  active = false;
  ready = false;
  clearTimeout(reconnectTimer);
  clearInterval(heartbeat);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  for (const item of pending.values()) clearTimeout(item.timer);
  sessionStorage.removeItem(storageKey);
  session = null; recipient = null; directory = [];
  conversations.clear(); pending.clear(); unread.clear();
  $("welcome").hidden = false;
  $("chat").hidden = true;
  $("chat").classList.remove("show-people");
  $("toggle-people").setAttribute("aria-expanded", "false");
  $("session-tools").hidden = true;
  $("register-error").textContent = message;
  $("message").value = "";
  $("username").focus();
}
function renderPeople() {
  const query = $("search").value.toLowerCase();
  const people = directory.filter((user) => user.username.toLowerCase().includes(query))
    .sort((a, b) => Number(b.online) - Number(a.online) || a.username.localeCompare(b.username));
  $("people-count").textContent = String(directory.length);
  $("no-people").hidden = directory.length > 0;
  const fragment = document.createDocumentFragment();
  for (const user of people) {
    const li = document.createElement("li"), button = document.createElement("button");
    button.type = "button"; button.className = "person";
    button.setAttribute("aria-pressed", String(recipient?.id === user.id));
    const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = user.username.slice(0, 2).toUpperCase();
    const details = document.createElement("div"); details.className = "person-details";
    const name = document.createElement("p"); name.className = "person-name"; name.textContent = user.username;
    const status = document.createElement("p"); status.className = "person-status";
    status.dataset.online = String(user.online); status.textContent = user.online ? "Online" : "Offline";
    details.append(name, status); button.append(avatar, details);
    if (unread.get(user.id)) {
      const count = document.createElement("div"); count.className = "unread"; count.textContent = unread.get(user.id);
      count.setAttribute("aria-label", count.textContent + " unread messages"); button.append(count);
    }
    button.addEventListener("click", () => choose(user));
    li.append(button); fragment.append(li);
  }
  if (!people.length && query) {
    const li = document.createElement("li"); li.className = "muted"; li.textContent = "No matching people."; fragment.append(li);
  }
  $("people").replaceChildren(fragment);
}
function renderRecipient() {
  $("recipient-name").textContent = recipient ? recipient.username : "Your next conversation";
  $("recipient-status").textContent = recipient ? (recipient.online ? "Online · here for a conversation" : "Offline · messages will be here when they return") : "Choose someone to say hello.";
  $("recipient-avatar").hidden = !recipient;
  if (recipient) $("recipient-avatar").textContent = recipient.username.slice(0, 2).toUpperCase();
}
function choose(user) {
  recipient = user; unread.delete(user.id);
  $("chat").classList.remove("show-people");
  $("toggle-people").setAttribute("aria-expanded", "false");
  setError(""); renderPeople(); renderRecipient(); renderMessages();
  $("message").disabled = !ready; $("send").disabled = !ready;
  loadHistory(user.id);
  if (matchMedia("(min-width: 761px)").matches) $("message").focus();
}
function remember(id, incoming) {
  const map = new Map((conversations.get(id) || []).map((message) => [message.id, message]));
  for (const message of incoming) map.set(message.id, message);
  const messages = Array.from(map.values()).sort((a, b) => a.id - b.id).slice(-100);
  conversations.set(id, messages);
}
async function loadHistory(id) {
  const generation = ++historyGeneration;
  try {
    const data = await api("/api/messages/" + id);
    if (!active) return;
    remember(id, data.messages);
    for (const message of data.messages) {
      if (message.from === session.user.id && pending.has(message.nonce)) {
        clearTimeout(pending.get(message.nonce).timer); pending.delete(message.nonce);
      }
    }
    if (generation === historyGeneration && recipient?.id === id) renderMessages();
  } catch (error) { if (active && recipient?.id === id) setError(error.message); }
}
function renderMessages() {
  const selected = Boolean(recipient);
  $("empty-conversation").hidden = selected;
  $("message-list").hidden = !selected;
  if (!selected || !session) return;
  const list = $("message-list"), nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;
  const fragment = document.createDocumentFragment();
  const messages = [...(conversations.get(recipient.id) || []),
    ...Array.from(pending.values()).filter((item) => item.to === recipient.id)];
  for (const message of messages) {
    const mine = message.from === session.user.id, node = document.createElement("article");
    node.className = "message" + (mine ? " mine" : "") + (message.pending ? " pending" : "") + (message.failed ? " failed" : "");
    const bubble = document.createElement("p"); bubble.className = "bubble"; bubble.textContent = message.text;
    const time = document.createElement("p"); time.className = "message-time";
    time.textContent = message.failed ? "Unconfirmed — check history after reconnecting" : message.pending ? "Sending…" :
      new Date(message.sent_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + (mine ? " · Sent" : "");
    node.append(bubble, time); fragment.append(node);
  }
  if (!messages.length) {
    const note = document.createElement("p"); note.className = "muted"; note.textContent = "This is the beginning of your conversation."; fragment.append(note);
  }
  list.replaceChildren(fragment);
  if (nearBottom || messages.at(-1)?.from === session.user.id) list.scrollTop = list.scrollHeight;
}
$("register-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("join").disabled = true; $("register-error").textContent = "";
  try {
    session = await api("/api/register", { method: "POST", body: JSON.stringify({ username: $("username").value.trim() }) });
    sessionStorage.setItem(storageKey, JSON.stringify(session)); start();
  } catch (error) { $("register-error").textContent = error.message; }
  finally { $("join").disabled = false; }
});
$("message-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!ready || !recipient || socket?.readyState !== WebSocket.OPEN) return;
  const text = $("message").value;
  if (!text.trim()) return;
  if (encoder.encode(text).length > 4096) { setError("That message is too long. Please keep it under 4096 UTF-8 bytes."); return; }
  if (pending.size >= 32) { setError("Wait for your recent messages to finish sending."); return; }
  const nonce = crypto.randomUUID(), item = { from: session.user.id, to: recipient.id, text, nonce, sent_at: Date.now(), pending: true };
  item.timer = setTimeout(() => { if (pending.has(nonce)) { item.failed = true; renderMessages(); } }, 10000);
  pending.set(nonce, item);
  socket.send(JSON.stringify({ type: "send", to: recipient.id, text, nonce }));
  $("message").value = ""; setError(""); renderMessages(); $("message").focus();
});
$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("message-form").requestSubmit(); }
});
$("search").addEventListener("input", renderPeople);
$("leave").addEventListener("click", () => leave());
$("toggle-people").addEventListener("click", () => {
  const open = $("chat").classList.toggle("show-people");
  $("toggle-people").setAttribute("aria-expanded", String(open));
  if (open) $("search").focus();
});
restoreSession();
if (session) start();
