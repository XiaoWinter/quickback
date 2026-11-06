import asyncio
import socket

import uvicorn

from app.config import ROOT, settings
from app.door import accept_loop

_CERT = ROOT / "certs" / "server.crt"
_KEY = ROOT / "certs" / "server.key"


def main() -> None:
    if _CERT.is_file() and _KEY.is_file():
        print("已使用 certs 里的证书。用 http 打开同一个地址会转到 https。", flush=True)
        run_https(settings.host, settings.port, _CERT, _KEY)
        return
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


def run_https(host: str, port: int, cert, key) -> None:
    internal = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    internal.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    internal.bind(("127.0.0.1", 0))
    internal.listen(128)
    internal.setblocking(False)
    internal_port = internal.getsockname()[1]

    public = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    public.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        public.bind((host, port))
    except OSError as exc:
        internal.close()
        public.close()
        raise SystemExit(f"端口 {port} 打不开。{exc}") from exc
    public.listen(128)
    public.setblocking(False)

    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=internal_port,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        log_level="info",
    )
    server = uvicorn.Server(config)

    async def runner() -> None:
        server_task = asyncio.create_task(server.serve(sockets=[internal]))
        while not server.started and not server_task.done():
            await asyncio.sleep(0.02)
        if not server.started:
            await server_task
            public.close()
            return
        door_task = asyncio.create_task(accept_loop(public, internal_port))
        try:
            await server_task
        finally:
            door_task.cancel()
            await asyncio.gather(door_task, return_exceptions=True)
            public.close()

    try:
        asyncio.run(runner())
    finally:
        internal.close()
        public.close()


if __name__ == "__main__":
    main()
