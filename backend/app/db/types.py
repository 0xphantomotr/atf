from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        if self.dimensions:
            return f"vector({self.dimensions})"
        return "vector"

