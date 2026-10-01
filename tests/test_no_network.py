"""Un test no sale a la red sin declararlo (tarjeta test-double-fidelity).

Un doble que deja pasar una conexión real hace que el test pase contra lo que haya del otro
lado. El fixture `no_network` de conftest la rechaza; `@pytest.mark.network` la permite, y
solo a localhost.
"""
import socket

import pytest


def test_an_unmarked_test_cannot_connect_even_to_localhost() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="conexión"):
        sock.connect(("127.0.0.1", 9))


def test_an_unmarked_test_cannot_resolve_a_real_name() -> None:
    with pytest.raises(RuntimeError, match="resolver"):
        socket.getaddrinfo("api.modrinth.com", 443)


@pytest.mark.network
def test_a_marked_test_still_cannot_resolve_a_real_name() -> None:
    with pytest.raises(RuntimeError, match="resolver"):
        socket.getaddrinfo("api.modrinth.com", 443)
