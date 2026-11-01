import os
import socket

from allin.supervisor.ports import port_free, port_holder


def test_a_port_someone_listens_on_is_not_free_and_says_who():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert not port_free(port)
        assert port_holder(port).startswith(f"pid {os.getpid()} ")


def test_connections_a_dead_server_left_in_time_wait_do_not_hold_its_port():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = listener.accept()
        accepted.close()  # the server's end closes first: TIME_WAIT is on its port
        client.close()
    assert port_free(port)
