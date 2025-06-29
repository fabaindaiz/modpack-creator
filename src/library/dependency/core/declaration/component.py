from typing import Any, Callable, Optional, TypeVar
from dependency_injector.wiring import Provide
from src.library.dependency.core.exceptions import DependencyError
from src.library.dependency.core.declaration.base import ABCComponent, ABCProvider

COMPONENT = TypeVar('COMPONENT', bound='Component')

class Component(ABCComponent):
    """Component Base Class
    """
    def __init__(self, base_cls: type) -> None:
        super().__init__(base_cls=base_cls)
        self.__provider: Optional[ABCProvider] = None
    
    @property
    def provider(self) -> Optional[ABCProvider]:
        return self.__provider
    
    @provider.setter
    def provider(self, provider: ABCProvider) -> None:
        if self.__provider:
            raise DependencyError(f"Component {self} is already provided by {self.__provider}. Attempted to set new provider: {provider}")
        self.__provider = provider
    
    @staticmethod
    def provide(service: Any = None) -> Any: # TODO: provide signature
        pass

def component(interface: type) -> Callable[[type[COMPONENT]], COMPONENT]:
    """Decorator for Component class

    Args:
        interface (type): Interface class to be used as a base class for the component.
    
    Raises:
        TypeError: If the wrapped class is not a subclass of Component.

    Returns:
        Callable[[type[Component]], Component]: Decorator function that wraps the component class.
    """
    def wrap(cls: type[COMPONENT]) -> COMPONENT:
        if not issubclass(cls, Component):
            raise TypeError(f"Class {cls} is not a subclass of Component")

        class WrapComponent(cls): # type: ignore
            def __init__(self) -> None:
                super().__init__(base_cls=interface)

            def provide(self,
                    service: Any = Provide[f"{interface.__name__}.service"]
                ) -> Any:
                if issubclass(service.__class__, Provide):
                    raise DependencyError(f"Component {self} was not provided")
                return service
        return WrapComponent()
    return wrap