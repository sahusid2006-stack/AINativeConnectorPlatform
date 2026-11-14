"""No ambient auth/proxies; validate every hop and connect to the validated IP."""
import http.client
import ipaddress
import socket
import ssl
from threading import BoundedSemaphore, Event, Thread, Timer
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

from .access import AccessError
from .redaction import redacted

MAX_BYTES = 2 * 1024 * 1024
_dns_slots = BoundedSemaphore(5)


def resolve(host, port, timeout):
    if not _dns_slots.acquire(blocking=False):
        raise AccessError("source_fetch_busy", 429)
    result, done = [], Event()
    def lookup():
        try:
            result.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            pass
        finally:
            _dns_slots.release()
            done.set()
    Thread(target=lookup, daemon=True).start()
    if not done.wait(timeout) or not result:
        raise AccessError("source_dns_unavailable", 422)
    return {row[4][0] for row in result}


def origin(url):
    try:
        parsed = urlsplit(url)
        if (len(url) > 4096 or any(ord(c) < 33 or c == "\\" for c in url)
            or parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if len(host) > 253 or not 1 <= port <= 65535 or "%" in host:
            raise ValueError()
        for key, _ in parse_qsl(parsed.query):
            key = key.lower().replace("-", "_")
            if key in {"token", "key", "api_key", "password", "secret", "authorization"} or key.endswith(("_token", "_key", "_secret", "_password")):
                raise ValueError()
        if redacted(url) != url:
            raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        default = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{authority}" + (f":{port}" if port != default else "")
    except (ValueError, UnicodeError):
        raise AccessError("invalid_source_url", 422) from None


class DestinationGuard:
    def __init__(self, approved_origins, resolver=resolve):
        if not 1 <= len(approved_origins) <= 10:
            raise AccessError("source_destination_limit", 422)
        self.approved = {origin(value) for value in approved_origins}
        for value in approved_origins:
            if urlsplit(value).path not in {"", "/"} or urlsplit(value).query:
                raise AccessError("invalid_approved_origin", 422)
        self.resolver = resolver

    def check(self, url, timeout=5):
        destination = origin(url)
        if destination not in self.approved:
            raise AccessError("source_destination_denied", 422)
        parsed = urlsplit(destination)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = self.resolver(parsed.hostname, port, timeout)
        if not addresses or len(addresses) > 16:
            raise AccessError("source_dns_unavailable", 422)
        for address in addresses:
            ip = ipaddress.ip_address(address)
            normalized = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            # Only this app-owned Docker fixture alias gets an RFC1918 exception.
            fixture = destination == "http://fixtures:8000" and isinstance(normalized, ipaddress.IPv4Address) and any(
                normalized in network for network in (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16")))
            translated = isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"))
            if translated or (not normalized.is_global and not fixture):
                raise AccessError("source_address_denied", 422)
        return parsed, sorted(addresses)[0]


def request_once(url, parsed, address, remaining):
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=min(5, remaining))
    sock, timer = None, None
    def interrupt():
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        # Never resolve again while connecting (DNS rebinding / ambient proxy defense).
        sock = socket.create_connection((address, port), timeout=min(5, remaining))
        timer = Timer(remaining, interrupt); timer.daemon = True; timer.start()
        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(address):
            raise AccessError("source_peer_denied", 422)
        connection.sock = sock
        target = urlsplit(url)
        path = target.path or "/"
        if target.query: path += "?" + target.query
        connection.request("GET", path, headers={"Accept-Encoding":"identity", "User-Agent":"ConnectorStudio-SourceIntake/1"})
        response = connection.getresponse()
        headers = dict(response.getheaders())
        if response.status in {301,302,303,307,308}:
            return response.status, headers, b""
        if response.status != 200:
            raise AccessError("source_inaccessible", 422)
        if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise AccessError("source_encoding_unsupported", 422)
        length = response.getheader("Content-Length")
        if length and int(length) > MAX_BYTES:
            raise AccessError("source_byte_limit", 413)
        body = bytearray()
        while True:
            part = response.read1(min(65536, MAX_BYTES+1-len(body)))
            if not part: break
            body.extend(part)
            if len(body) > MAX_BYTES: raise AccessError("source_byte_limit", 413)
        if length and len(body) != int(length):
            raise AccessError("source_incomplete", 422)
        return response.status, headers, bytes(body)
    except (OSError, http.client.HTTPException, ValueError):
        raise AccessError("source_inaccessible", 422) from None
    finally:
        if timer: timer.cancel()
        connection.close()
        if sock: sock.close()


def fetch_source(url, approved_origins, resolver=resolve, transport=request_once):
    guard, deadline = DestinationGuard(approved_origins, resolver), time.monotonic()+15
    for hop in range(4):
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise AccessError("source_deadline", 422)
        parsed, address = guard.check(url, min(5, remaining))
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise AccessError("source_deadline", 422)
        status, headers, content = transport(url, parsed, address, remaining)
        headers = {key.lower():value for key,value in headers.items()}
        if status in {301,302,303,307,308}:
            if hop == 3 or not headers.get("location"):
                raise AccessError("source_redirect_limit", 422)
            url = urljoin(url, headers["location"])
            continue
        if status != 200: raise AccessError("source_inaccessible", 422)
        if len(content) > MAX_BYTES: raise AccessError("source_byte_limit", 413)
        return url, headers.get("content-type", "text/plain").split(";")[0].lower(), content
    raise AccessError("source_redirect_limit", 422)
