"""``/secret`` (#124, ADR-0026): a human gives, lists and removes the Secretos of the active
Profile.

Only a human: a Secreto is one of their keys, and no Session sets or reads it. Its value never
goes on the command line, which the Log keeps: ``/secret set`` asks for it in a box of Main's
Chat that shows a ``•`` for each character, and Main hands it to the Proceso adherido from its
next attach.
"""

from allin.core.errors import AllInError, InvalidArguments
from allin.core.ids import AttachedName, parse_attached_name
from allin.main.boxes import MainBoxes
from allin.main.reporting import Reporter
from allin.main.secrets import SecretName, Secrets, parse_secret_name

USAGE = (
    "/secret [list [<instance>]] | /secret set <instance> <name> | /secret remove <instance> <name>"
)


class SecretCommands:
    def __init__(self, secrets: Secrets, boxes: MainBoxes, reporter: Reporter) -> None:
        self._secrets = secrets
        self._boxes = boxes
        self._reporter = reporter

    def run(self, text: str = "") -> bool:
        match text.split():
            case [] | ["list"]:
                self._list()
            case ["list", instance]:
                self._list_of(_instance(instance))
            case ["set", instance, name]:
                self._ask(_instance(instance), _name(name))
            case ["remove", instance, name]:
                self._remove(_instance(instance), _name(name))
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def _list(self) -> None:
        names = self._secrets.names()
        self._reporter.show(None, f"secrets: {sum(len(each) for each in names.values())}")
        for instance, its_names in names.items():
            self._reporter.show(None, f"  {instance}: {', '.join(its_names)}")

    def _list_of(self, instance: AttachedName) -> None:
        its_names = sorted(self._secrets.of(instance))
        self._reporter.show(None, f"{instance}: {', '.join(its_names) or 'none'}")

    def _ask(self, instance: AttachedName, name: SecretName) -> None:
        asked_in = self._secrets.pinned()  # Main may switch Profile while the box is open

        def given(value: str | None) -> None:
            if value is None:
                shown = f"secret {name} of {instance}: not set, the box was left empty"
                self._reporter.report(None, "secret_not_set", shown, instance=instance, name=name)
                return
            try:
                asked_in.set(instance, name, value)
            except AllInError as error:
                self._reporter.error(error)
                return
            shown = f"secret {name} of {instance}: set; it reaches {instance} from its next attach"
            self._reporter.report(None, "secret_set", shown, instance=instance, name=name)

        text = f"The value of {name} for {instance}. It shows as dots, and no list shows it."
        self._boxes.ask_secret(f"Secret {name} of {instance}", text, given)

    def _remove(self, instance: AttachedName, name: SecretName) -> None:
        self._secrets.remove(instance, name)
        shown = f"secret {name} of {instance}: removed"
        self._reporter.report(None, "secret_removed", shown, instance=instance, name=name)


def _instance(text: str) -> AttachedName:
    instance = parse_attached_name(text)
    if instance is None:
        raise InvalidArguments(usage=USAGE)
    return instance


def _name(text: str) -> SecretName:
    name = parse_secret_name(text)
    if name is None:
        raise InvalidArguments(usage=USAGE)
    return name
