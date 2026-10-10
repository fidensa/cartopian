"""Resolver pinning is necessary even when the agent cannot open local sockets."""
from unittest.mock import patch

import pytest

from cli.native_network_proxy import connect_public, public_destination


@pytest.mark.parametrize('address', ('127.0.0.1', '::1', '::ffff:127.0.0.1', '10.2.3.4',
                                  '169.254.169.254', 'fe80::1', '224.0.0.1', 'ff02::1'))
def test_non_public_destinations_are_denied(address):
    assert not public_destination(address)


@pytest.mark.parametrize('addresses,local', ((('8.8.8.8','127.0.0.1'),frozenset()),
                                          (('8.8.8.8',),frozenset({'8.8.8.8'}))))
def test_mixed_dns_answers_and_public_host_interface_are_denied(addresses, local):
    import socket
    answers = [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443)) for address in addresses]
    with patch('cli.native_network_proxy.socket.getaddrinfo', return_value=answers), \
         patch('cli.native_network_proxy.socket.socket') as connector:
        with pytest.raises(ValueError):
            connect_public('example.com',443,local)
        connector.assert_not_called()
