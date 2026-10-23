"""Shared outbound-HTTP guard for plugins that fetch user-supplied URLs.

Not a plugin: the ibot loader skips files whose names start with ``_``.
Plugins load it with ``_netguard_import()`` (see the snippet in url_titles.py)
because plugin directories are not on ``sys.path``.

What it enforces:
  * only http/https URLs whose host resolves exclusively to public addresses
    (same rule as feeds.py's original ``_valid_url`` / ``_public_host``);
  * the address actually connected to is re-checked at connect time, which
    defeats DNS rebinding (the name resolving to a public IP during validation
    and to 127.0.0.1 / RFC1918 a moment later);
  * redirects are followed by hand, and every hop is re-validated;
  * a total wall-clock deadline and a byte cap on the body.
"""

import ipaddress
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError


class BlockedURL(ValueError):
    """Raised when a URL / redirect / connected peer is not allowed."""


def is_public_ip(addr):
    try:
        return ipaddress.ip_address(str(addr).split('%')[0]).is_global
    except ValueError:
        return False


def public_host(host):
    """True only if every address the host resolves to is public."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        return False
    return bool(infos) and all(is_public_ip(a[4][0]) for a in infos)


def valid_url(url):
    try:
        p = urlparse(url)
        host = p.hostname
    except ValueError:
        return False
    return p.scheme in ('http', 'https') and bool(p.netloc) and public_host(host)


def _check_peer(conn, sock):
    try:
        peer = sock.getpeername()[0]
    except OSError as exc:
        sock.close()
        raise NewConnectionError(conn, f'could not read peer address: {exc}') from None
    if not is_public_ip(peer):
        sock.close()
        raise NewConnectionError(conn, 'connected to a non-public address (blocked)')
    return sock


class _GuardedHTTPConnection(HTTPConnection):
    def _new_conn(self):
        return _check_peer(self, super()._new_conn())


class _GuardedHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        return _check_peer(self, super()._new_conn())


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class GuardedAdapter(HTTPAdapter):
    """HTTPAdapter whose connections refuse non-public peers."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            'http': _GuardedHTTPPool,
            'https': _GuardedHTTPSPool,
        }

    def proxy_manager_for(self, *args, **kwargs):  # pragma: no cover
        # A proxy would make the peer check meaningless (and be rejected if it
        # is on a private address); refuse instead of silently bypassing.
        raise BlockedURL('proxies are not supported by the URL guard')


def guarded_session():
    s = requests.Session()
    s.trust_env = False          # ignore *_PROXY env vars (see proxy_manager_for)
    adapter = GuardedAdapter()
    s.mount('http://', adapter)
    s.mount('https://', adapter)
    return s


_LOCAL = threading.local()


def _session():
    """One guarded session per thread (requests.Session isn't thread-safe)."""
    sess = getattr(_LOCAL, 'session', None)
    if sess is None:
        sess = _LOCAL.session = guarded_session()
    return sess


class FetchResult:
    __slots__ = ('url', 'status_code', 'headers', 'content', 'truncated', 'encoding')

    def __init__(self, url, status_code, headers, content, truncated, encoding):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.truncated = truncated
        self.encoding = encoding

    @property
    def text(self):
        return self.content.decode(self.encoding or 'utf-8', errors='replace')


def fetch(url, *, headers=None, max_bytes=1_000_000, timeout=5, deadline=10,
          max_redirects=5, truncate=False, session=None, require_ctype=None):
    """GET ``url`` safely.  Returns a FetchResult.

    ``timeout`` is the per-socket-operation timeout; ``deadline`` bounds the
    whole operation (redirects + body).  With ``truncate=True`` an oversized
    body is cut at ``max_bytes`` instead of raising.  With ``require_ctype``
    (e.g. 'text/html') the body is not downloaded at all unless the final
    Content-Type contains that string (``content`` is then b'').
    Raises BlockedURL for disallowed targets and requests exceptions for
    network errors.
    """
    sess = session or _session()
    end = time.monotonic() + deadline
    for _hop in range(max_redirects + 1):
        if not valid_url(url):
            raise BlockedURL('URL not allowed')
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout('deadline exceeded')
        r = sess.get(url, headers=headers or {}, stream=True, allow_redirects=False,
                     timeout=min(timeout, remaining))
        if r.is_redirect:
            nxt = urljoin(url, r.headers.get('Location', ''))
            r.close()
            url = nxt
            continue
        break
    else:
        raise BlockedURL('too many redirects')

    with r:
        buf = bytearray()
        truncated = False
        if require_ctype and require_ctype not in r.headers.get('Content-Type', ''):
            return FetchResult(url, r.status_code, r.headers, b'', False, r.encoding)
        for chunk in r.iter_content(16384):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                if not truncate:
                    raise ValueError('response larger than %d bytes' % max_bytes)
                del buf[max_bytes:]
                truncated = True
                break
            if time.monotonic() > end:
                raise requests.Timeout('deadline exceeded')
        return FetchResult(url, r.status_code, r.headers, bytes(buf), truncated,
                           r.encoding)
