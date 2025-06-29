from typing import Callable, Sequence, TypeVar, cast
from src.library.dependency.core.declaration.base import ABCComponent, ABCProvider, ABCDependent

DEPENDENT = TypeVar('DEPENDENT', bound='Dependent')

class Dependent(ABCDependent):
    """Dependent Base Class
    """
    _dependency_imports: Sequence[ABCComponent]
    _dependency_resolved: bool = False

    @classmethod
    def resolve_dependent(cls, providers: Sequence[ABCProvider]) -> list[str]:
        if cls._dependency_resolved:
            return []
        cls._dependency_resolved = True
        
        return [
            component.__repr__()
            for component in cls._dependency_imports
            if not any(
                issubclass(provider.provided_cls, component.base_cls)
                for provider in providers
            )
        ]

def dependent(
        imports: Sequence[type[ABCComponent]] = [],
    ) -> Callable[[type[DEPENDENT]], type[DEPENDENT]]:
    """Decorator for Dependent class

    Args:
        imports (Sequence[type[Component]], optional): List of components to be imported by the dependent. Defaults to [].

    Raises:
        TypeError: If the wrapped class is not a subclass of Dependent.

    Returns:
        Callable[[type[Dependent]], type[Dependent]]: Decorator function that wraps the dependent class.
    """
    # Cast due to mypy not supporting class decorators
    _imports = cast(Sequence[ABCComponent], imports)
    def wrap(cls: type[Dependent]) -> type[Dependent]:
        if not issubclass(cls, Dependent):
            raise TypeError(f"Class {cls} is not a subclass of Dependent")

        cls._dependency_imports = _imports
        return cls
    return wrap