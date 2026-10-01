"""La app se compone en Python 3.12: importa, y el contenedor de dependencias cablea cada
servicio con los tipos que su constructor declara.

Es la clase de error que nadie ve antes del despliegue: un import roto o una dependencia
cruzada solo fallan al arrancar el contenedor en el servidor.
"""
import inspect
import typing

from dependency_injector import providers


def test_app_imports() -> None:
    import main

    assert main.app.title == "Modpack Creator API"


def test_v1_routers_mount() -> None:
    # main.py los monta en el lifespan, que abre MongoDB; aquí se montan sobre una app vacía.
    from fastapi import FastAPI
    from src.routers.v1 import include_routers_v1

    app = FastAPI()
    include_routers_v1(app)
    # app.openapi() y no app.routes: desde FastAPI 0.142 los routers incluidos son perezosos.
    paths = set(app.openapi()["paths"])
    assert {"/v1/modpack", "/v1/modrinth/project", "/v1/server", "/v1/users/me"} <= paths


def test_di_wiring_matches_constructor_types() -> None:
    from src.dependences import Dependences

    checked = 0
    for name, provider in Dependences.providers.items():
        if not isinstance(provider, providers.Singleton) or not provider.args:
            continue
        cls = typing.cast(type, provider.provides)
        init = getattr(cls, "__init__")
        hints = typing.get_type_hints(init)
        params = [p for p in inspect.signature(init).parameters.values() if p.name != "self"]
        assert len(params) == len(provider.args), f"{name}: {len(provider.args)} args for {len(params)} parameters"
        for param, arg in zip(params, provider.args):
            injected = arg.provides
            expected = hints[param.name]
            assert issubclass(injected, expected), (
                f"{name}: {param.name} expects {expected.__name__}, gets {injected.__name__}")
            checked += 1
    assert checked > 0
