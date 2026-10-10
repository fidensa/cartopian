"""A launch-scoped HTTPS tunnel; sandboxed agents cannot reach local daemons.

Resolve and connect in the controller, pinning the chosen public address. Never
forward an agent's DNS name to a second resolver or accept a private destination.
The OS boundary permits only this listener, including for IPv4-mapped IPv6.
"""
from __future__ import annotations

import ipaddress
import re
import select
import socket
import socketserver
import subprocess
import threading
from contextlib import contextmanager


def normalized_address(address: str):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def public_destination(address: str) -> bool:
    ip = normalized_address(address)
    return ip.is_global and not ip.is_multicast


def connect_public(host: str, port: int, local: frozenset[str] = frozenset()) -> socket.socket:
    if not 1 <= port <= 65535:
        raise ValueError("invalid tunnel port")
    choices = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    local_ips = {normalized_address(address) for address in local}
    if not choices or any(not public_destination(item[4][0]) or normalized_address(item[4][0]) in local_ips for item in choices):
        raise ValueError("private or non-public tunnel destination")
    last = None
    for family, kind, protocol, _name, address in choices:
        remote = socket.socket(family, kind, protocol)
        remote.settimeout(15)
        try:
            remote.connect(address)
            return remote
        except OSError as exc:
            remote.close()
            last = exc
    raise last or OSError("no tunnel destination")


class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(10)
        try:
            header = bytearray()
            # Read only headers: bytes sent before successful CONNECT are
            # rejected rather than accidentally dropped or treated as requests.
            while not header.endswith(b"\r\n\r\n"):
                byte = client.recv(1)
                if not byte or len(header) >= 16384:
                    raise ValueError("invalid tunnel header")
                header.extend(byte)
            method, authority, version = header.split(b"\r\n", 1)[0].decode("ascii").split()
            if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("only HTTPS CONNECT is supported")
            host, raw_port = authority.rsplit(":", 1)
            host = host.removeprefix("[").removesuffix("]")
            if not host or any(c in host for c in "/@\\?#"):
                raise ValueError("invalid tunnel hostname")
            with connect_public(host, int(raw_port), self.server.local_addresses) as remote:
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                remote.settimeout(15)
                client.settimeout(15)
                while not self.server.stopping.is_set():
                    readable, _, _ = select.select([client, remote], [], [], 1)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is client else client).sendall(data)
        except (OSError, ValueError, UnicodeError):
            try:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            except OSError:
                pass


class Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address=("127.0.0.1", 0)):
        self.stopping = threading.Event()
        interfaces = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True,
                                    check=True, timeout=10, env={"PATH": "/usr/bin:/bin"}).stdout
        self.local_addresses = frozenset(re.findall(r"\binet6? ([^\s%]+)", interfaces))
        if not self.local_addresses:
            raise OSError("cannot establish host network addresses")
        super().__init__(address, Tunnel)


class Proxy6(Proxy):
    address_family = socket.AF_INET6


@contextmanager
def network_proxy():
    with Proxy() as proxy, Proxy6(("::1", proxy.server_address[1])) as ipv6:
        workers = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
                   for server in (proxy, ipv6)]
        for worker in workers:
            worker.start()
        try:
            yield proxy.server_address[1]
        finally:
            for server in (proxy, ipv6):
                server.stopping.set()
                server.shutdown()
            for worker in workers:
                worker.join()
