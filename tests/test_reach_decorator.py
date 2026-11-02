"""``@reach``: how a function declares its Alcance (ADR-0016, #101)."""

import pytest

from allin.core.errors import InvalidReach
from allin.decorators import MINIMUM, Reach, reach, reach_of


def test_a_function_without_the_decorator_has_exactly_the_minimum_reach():
    def plain() -> None: ...

    assert reach_of(plain) == MINIMUM == Reach()
    assert Reach(read=(), write=(), net=False, display=False, commands=()) == MINIMUM


def test_the_decorator_keeps_what_it_declares_and_leaves_the_function_as_it_was():
    @reach(read=["home/Documents"], write=["work", "/srv/data"], net=[443], commands=["send"])
    def fetch(url: str) -> str:
        return url

    assert fetch("x") == "x"
    assert reach_of(fetch) == Reach(
        read=("home/Documents",),
        write=("work", "/srv/data"),
        net=(443,),
        display=False,
        commands=("send",),
    )


def test_in_an_fproc_it_goes_on_the_class():
    @reach(net=True, display=True)
    class Bot:
        def say(self) -> None: ...

    assert reach_of(Bot) == Reach(net=True, display=True)


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"read": ["Documents"]}, "read"),  # no symbolic root
        ({"write": ["home/../.."]}, "write"),  # climbs out of its root
        ({"net": [0]}, "net"),
        ({"net": [70000]}, "net"),
        ({"net": "443"}, "net"),
        ({"read": "home"}, "read"),  # a string, not a list of them
    ],
)
def test_what_is_not_a_reach_is_refused_where_it_is_declared(
    arguments: dict[str, object], field: str
):
    with pytest.raises(InvalidReach) as raised:
        reach(**arguments)  # pyright: ignore[reportArgumentType]  # wrong on purpose
    assert raised.value.fields["field"] == field


def test_its_tcp_ports_never_include_a_servers_and_net_true_leaves_them_to_a_layer():
    servers = frozenset({9931})
    assert Reach(net=(443, 9931, 80)).tcp_ports(servers) == (80, 443)
    assert Reach(net=True).tcp_ports(servers) is None
    assert MINIMUM.tcp_ports(servers) == ()
    assert Reach(net=(443,)).network and Reach(net=True).network and not MINIMUM.network
