"""同一端口上区分 http 和 https。

证书启用后，uvicorn 只讲 https。浏览器如果还用 http 去连，握手对不上，页面直接打不开。
这里先看连接的第一个字节：是 TLS 就原样转给后面的 https 服务，是普通 http 就回答 307，转到同一个主机的 https。
"""

import asyncio
import logging
import socket

log = logging.getLogger("quickback")

_METHODS = (b"GET ", b"POST ", b"PUT ", b"PATCH ", b"DELETE ", b"HEAD ", b"OPTIONS ")
_MAX_HEADER = 8192


def is_tls(data: bytes) -> bool:
    return data[:1] == b"\x16"


def is_http(data: bytes) -> bool:
    return data.startswith(_METHODS)


def request_parts(data: bytes) -> tuple[str, str] | None:
    if b"\r\n\r\n" not in data:
        return None
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", errors="replace")
    lines = head.split("\r\n")
    fields = lines[0].split(" ")
    if len(fields) < 2:
        return None
    target = fields[1]
    if not target.startswith("/"):
        target = "/"
    host = ""
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep and name.lower() == "host":
            host = value.strip()
            break
    return target, host


def safe_host(host: str, fallback: str) -> str:
    cleaned = (host or "").strip()
    if not cleaned or any(char in cleaned for char in " \r\n\\/"):
        return fallback
    return cleaned


def redirect_bytes(location: str) -> bytes:
    body = f"请改用 {location}\n".encode("utf-8")
    header = (
        "HTTP/1.1 307 Temporary Redirect\r\n"
        f"Location: {location}\r\n"
        "Cache-Control: no-store\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
    )
    return header.encode("ascii") + body


def location_for(target: str, host: str, fallback: str) -> str:
    return f"https://{safe_host(host, fallback)}{target}"


async def accept_loop(public: socket.socket, internal_port: int) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            client, _addr = await loop.sock_accept(public)
        except OSError:
            return
        task = asyncio.create_task(handle_client(client, internal_port))
        task.add_done_callback(_log_task_error)


async def handle_client(client: socket.socket, internal_port: int) -> None:
    client.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        data = await _read_some(client)
        if not data:
            return
        if is_tls(data):
            await _tunnel(client, internal_port, data)
            return
        if not is_http(data):
            return
        while b"\r\n\r\n" not in data and len(data) < _MAX_HEADER:
            more = await loop.sock_recv(client, _MAX_HEADER)
            if not more:
                break
            data += more
        parts = request_parts(data)
        if parts is None:
            return
        target, host = parts
        local_ip, local_port = client.getsockname()[:2]
        fallback = f"{local_ip}:{local_port}"
        payload = redirect_bytes(location_for(target, host, fallback))
        await loop.sock_sendall(client, payload)
    except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
        return
    finally:
        client.close()


async def _read_some(client: socket.socket) -> bytes:
    try:
        return await asyncio.wait_for(asyncio.get_running_loop().sock_recv(client, 65536), timeout=10)
    except TimeoutError:
        return b""


async def _tunnel(client: socket.socket, internal_port: int, prefix: bytes) -> None:
    loop = asyncio.get_running_loop()
    remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    remote.setblocking(False)
    try:
        await loop.sock_connect(remote, ("127.0.0.1", internal_port))
        if prefix:
            await loop.sock_sendall(remote, prefix)
        await asyncio.gather(
            _pipe(client, remote),
            _pipe(remote, client),
        )
    except OSError:
        return
    finally:
        remote.close()


async def _pipe(source: socket.socket, target: socket.socket) -> None:
    loop = asyncio.get_running_loop()
    try:
        while True:
            chunk = await loop.sock_recv(source, 65536)
            if not chunk:
                break
            await loop.sock_sendall(target, chunk)
    except OSError:
        return
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _log_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("连接处理失败：%s", error)
