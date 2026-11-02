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


def test_a_reach_made_by_hand_is_checked_too():
    with pytest.raises(InvalidReach):
        Reach(net=(0,))
    with pytest.raises(InvalidReach):
        Reach(write=("srv",))


@pytest.mark.parametrize(
    ("reduced", "declared", "within"),
    [
        (MINIMUM, Reach(read=("home",), net=True, display=True), True),
        (Reach(read=("home/Documents",)), Reach(read=("home",)), True),
        (Reach(read=("home",)), Reach(read=("home/Documents",)), False),
        (Reach(write=("/usr/lib",)), Reach(write=("/",)), True),
        (Reach(read=("home",)), Reach(read=("/",)), False),  # roots are not paths of each other
        (Reach(net=(443,)), Reach(net=True), True),
        (Reach(net=(443,)), Reach(net=(443, 80)), True),
        (Reach(net=(22,)), Reach(net=(443,)), False),
        (Reach(net=True), Reach(net=(443,)), False),
        (Reach(display=True), MINIMUM, False),
        (Reach(commands=("send",)), Reach(commands=("send", "grant")), True),
        (Reach(commands=("grant",)), Reach(commands=("send",)), False),
    ],
)
def test_an_approval_may_only_reduce_a_reach(reduced: Reach, declared: Reach, within: bool):
    assert reduced.within(declared) is within


@pytest.mark.parametrize(
    ("reach", "acts"),
    [
        (MINIMUM, False),
        (Reach(read=("home",), net=True), False),
        (Reach(display=True), True),
        (Reach(write=("home",)), True),
        (Reach(write=("home/.config/app",)), True),  # where the user's programs read their setup
        (Reach(write=("home/.bashrc",)), True),
        (Reach(write=("home/bin",)), True),
        (Reach(write=("home/code/repo/.git",)), True),  # a repo's hooks
        (Reach(write=("work/.git/hooks",)), True),
        (Reach(write=("home/Documents",)), False),
        (Reach(write=("work/out",)), False),
        (Reach(write=("/",)), True),
        (Reach(write=("/usr/local",)), True),
        (Reach(write=("/dev/shm",)), False),  # noqa: S108 - multiprocessing's semaphores
    ],
)
def test_display_or_writing_where_the_user_runs_things_acts_as_the_user(reach: Reach, acts: bool):
    assert reach.acts_as_user is acts


def test_declaring_grant_is_told_apart():
    assert Reach(commands=("grant",)).grants
    assert not Reach(commands=("send",)).grants
