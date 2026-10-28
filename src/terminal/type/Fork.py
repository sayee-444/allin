import os

from pydantic import Field, RootModel


class ForkResult(RootModel[int]):
    root: int = Field(default_factory=os.fork, ge=0)

    def __int__(self) -> int:
        return self.root

    def __str__(self) -> str:
        return str(self.root)

    @property
    def is_child(self) -> bool:
        return self.root == 0

    @property
    def is_parent(self) -> bool:
        return self.root > 0

    @property
    def child_pid(self) -> int | None:
        return self.root if self.root > 0 else None
