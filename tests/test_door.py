import asyncio
import socket
import unittest

from app.door import accept_loop, is_http, is_tls, location_for, redirect_bytes, request_parts, safe_host


def _listen() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    sock.setblocking(False)
    return sock


class DoorParseTests(unittest.TestCase):
    def test_tls_and_http_are_distinct(self):
        self.assertTrue(is_tls(b"\x16\x03\x01"))
        self.assertFalse(is_http(b"\x16\x03\x01"))
        self.assertTrue(is_http(b"GET / HTTP/1.1\r\n"))
        self.assertFalse(is_tls(b"GET / HTTP/1.1\r\n"))

    def test_request_keeps_path_and_host(self):
        raw = b"GET /practice?x=1 HTTP/1.1\r\nHost: 192.168.1.20:8765\r\n\r\n"
        target, host = request_parts(raw)
        self.assertEqual(target, "/practice?x=1")
        self.assertEqual(host, "192.168.1.20:8765")

    def test_absolute_form_is_not_used_as_a_path(self):
        target, _host = request_parts(b"GET http://evil.test/a HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n")
        self.assertEqual(target, "/")

    def test_bad_host_falls_back(self):
        self.assertEqual(safe_host("evil test", "127.0.0.1:8765"), "127.0.0.1:8765")
        self.assertEqual(location_for("/a", "127.0.0.1:8765", "10.0.0.2:8765"), "https://127.0.0.1:8765/a")

    def test_redirect_is_temporary_and_not_cached(self):
        payload = redirect_bytes("https://127.0.0.1:8765/a")
        text = payload.decode("utf-8")
        self.assertIn("307 Temporary Redirect", text)
        self.assertIn("Location: https://127.0.0.1:8765/a\r\n", text)
        self.assertIn("Cache-Control: no-store", text)


class DoorServerTests(unittest.TestCase):
    def test_http_redirects_and_tls_is_forwarded(self):
        result = asyncio.run(self._exercise())
        status, location, forwarded = result
        self.assertIn("307", status)
        self.assertTrue(location.startswith("https://127.0.0.1:"))
        self.assertTrue(location.endswith("/hello"))
        self.assertEqual(forwarded, b"\x16hello")

    async def _exercise(self):
        internal = _listen()
        public = _listen()
        seen = bytearray()

        async def backend():
            loop = asyncio.get_running_loop()
            client, _addr = await loop.sock_accept(internal)
            data = await loop.sock_recv(client, 1024)
            seen.extend(data)
            client.close()

        async def once():
            door = asyncio.create_task(accept_loop(public, internal.getsockname()[1]))
            back = asyncio.create_task(backend())
            port = public.getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET /hello HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            await writer.drain()
            response = await reader.read()
            writer.close()
            tls = socket.create_connection(("127.0.0.1", port))
            tls.sendall(b"\x16hello")
            tls.close()
            await back
            door.cancel()
            await asyncio.gather(door, return_exceptions=True)
            public.close()
            internal.close()
            head = response.split(b"\r\n", 1)[0].decode()
            location = ""
            for line in response.decode("latin-1").split("\r\n"):
                if line.lower().startswith("location:"):
                    location = line.split(":", 1)[1].strip()
            return head, location, bytes(seen)

        return await once()


if __name__ == "__main__":
    unittest.main()
