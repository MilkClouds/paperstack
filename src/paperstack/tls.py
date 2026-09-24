"""TLS settings for hosts that reject some client handshakes."""

from __future__ import annotations

import functools
import ssl


@functools.cache
def arxiv_context() -> ssl.SSLContext:
    # arXiv's Fastly edge answers uncached requests with 406 when the TLS 1.3
    # ClientHello offers the X25519MLKEM768 hybrid key share (OpenSSL >= 3.5 default).
    context = ssl.create_default_context()
    context.set_ecdh_curve("X25519")
    return context
