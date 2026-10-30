"""How the audit log reads in Spanish (A9, spec §6): each action's text and area, each detail key's
label, and a detail's value as a phrase. What has no label is shown as it is: a new action or key
never breaks the page (tests/test_audit_labels.py asks a text for every action the code records)."""
from typing import Any, Mapping

# The areas the actions are grouped in (the filter's <optgroup>), in this order.
AREAS = ("Packs", "Builds", "Servidores", "Reemplazos")

ACTIONS: dict[str, tuple[str, str]] = {
    "bundle.create": ("Creó un pack", "Packs"),
    "bundle.update": ("Editó un pack", "Packs"),
    "bundle.delete": ("Borró un pack", "Packs"),
    "bundle.project.add": ("Agregó un mod", "Packs"),
    "bundle.project.remove": ("Quitó un mod", "Packs"),
    "bundle.project.replace": ("Reemplazó un mod", "Packs"),
    "bundle.via": ("Cambió la vía de un mod", "Packs"),
    "bundle.decision": ("Decidió el lado", "Packs"),
    "bundle.pin": ("Fijó una versión", "Packs"),
    "bundle.pin.import": ("Fijó las versiones del pack importado", "Packs"),
    "bundle.channel_exception": ("Aceptó otro canal", "Packs"),
    "bundle.channel_exception.delete": ("Quitó la excepción de canal", "Packs"),
    "bundle.replacement.delete": ("Quitó un reemplazo del pack", "Packs"),
    "bundle.catalog_refresh": ("Actualizó desde Modrinth", "Packs"),
    "bundle.update_check": ("Buscó actualizaciones", "Packs"),
    "bundle.import.request": ("Pidió importar un pack", "Packs"),
    "build.create": ("Creó un build", "Builds"),
    "build.request": ("Pidió un build", "Builds"),
    "build.recommend": ("Recomendó un build", "Builds"),
    "build.approve": ("Aprobó un build de prueba", "Builds"),
    "build.name": ("Nombró un build", "Builds"),
    "build.star": ("Destacó un build", "Builds"),
    "build.stable": ("Marcó un build estable", "Builds"),
    "note.create": ("Escribió una nota", "Packs"),
    "note.edit": ("Editó una nota", "Packs"),
    "note.archive": ("Archivó una nota", "Packs"),
    "note.unarchive": ("Recuperó una nota archivada", "Packs"),
    # O8 tanda 3: the knowledge base is global, and its entries are seen in packs.
    "knowledge.mark": ("Marcó una sospecha en la base", "Packs"),
    "knowledge.join": ("Sumó un pack a una entrada de la base", "Packs"),
    "knowledge.edit": ("Editó una entrada de la base", "Packs"),
    "knowledge.status": ("Cambió el estado de una entrada de la base", "Packs"),
    "knowledge.withdraw": ("Retiró su sospecha de la base", "Packs"),
    "replacement.set": ("Creó un reemplazo", "Reemplazos"),
    "replacement.delete": ("Borró un reemplazo", "Reemplazos"),
    "server.create": ("Creó un servidor", "Servidores"),
    "server.bundle": ("Asoció un pack al servidor", "Servidores"),
    "server.refresh": ("Actualizó los servidores", "Servidores"),
    "server.scan": ("Escaneó un servidor", "Servidores"),
    "server.scan.queue": ("Pidió escanear", "Servidores"),
    "server.import": ("Importó un servidor", "Servidores"),
    "server.import.request": ("Pidió importar un servidor", "Servidores"),
    "server.import.local": ("Importó archivos locales", "Servidores"),
    "server.import.confirm": ("Confirmó una importación", "Servidores"),
    "server.update": ("Actualizó un servidor", "Servidores"),
    "server.rollback": ("Hizo rollback", "Servidores"),
}

# The detail keys: the spec's, and the rest of what each `audit.record` writes.
DETAILS: dict[str, str] = {
    "project_id": "Mod",
    "version_id": "Versión",
    "server": "En el servidor",
    "decisions": "Decisiones",
    "channel": "Canal",
    "downgrade": "Baja",
    "cleared": "Limpiado",
    "number": "Build",
    "dry_run": "Simulación",
    "reason": "Motivo",
    "replacement": "Reemplazo",
    "refs": "Mods",
    "origin": "Origen",
    "source": "Fuente",
    "job_id": "Trabajo",
    "bundle_id": "Pack",
    "build": "Build",
    "uuid": "UUID",
    "directory": "Carpeta",
    "full": "Completo",
    "update_id": "Actualización",
    "mode": "Modo",
    "path": "Archivo",
    "added": "Agregados",
    "local": "Archivos locales",
    "candidates": "Por confirmar",
    "on_server": "En el servidor",
    "channel_exceptions": "Excepciones de canal",
}

# A target's kind, as the closed card names it.
KINDS = {"bundle": "Pack", "server": "Servidor", "replacement": "Reemplazo", "knowledge": "Conocimiento"}

# The keys whose value is one id the catalog may name ("Título (id)").
_NAMED_IDS = frozenset({"project_id", "version_id", "replacement"})
_NONE = "—"


def _plain(value: Any, names: Mapping[str, str]) -> str:
    """One value inside a list or a map: yes or no, the catalog's name for an id, or itself."""
    if isinstance(value, bool):
        return "sí" if value else "no"
    if value is None:
        return _NONE
    if isinstance(value, str):
        return names.get(value, value)
    return str(value)


def detail_text(key: str, value: Any, names: Mapping[str, str]) -> str:
    """A detail's value as a phrase: an id with its name from the catalog (`names`), yes or no, a
    downgrade as «19.57 → 19.51, confirmada», a map as «name: value» pairs and a list as its items,
    joined with «, »; anything else as it is."""
    if value is None:
        return _NONE
    if isinstance(value, bool):
        return "sí" if value else "no"
    if key in _NAMED_IDS and isinstance(value, str):
        return f"{names[value]} ({value})" if value in names else value
    if key == "downgrade" and isinstance(value, dict) and {"from", "to"} <= set(value):
        return f"{value['from']} → {value['to']}, confirmada"
    if isinstance(value, dict):
        return ", ".join(f"{_plain(k, names)}: {_plain(v, names)}" for k, v in value.items()) or _NONE
    if isinstance(value, (list, tuple)):
        return ", ".join(_plain(item, names) for item in value) or _NONE
    return str(value)


def target_label(kind: str, target_id: str, name: str | None) -> str:
    """What an event was done to: «Pack «Survival»» by its name, or its kind and id without one."""
    if name:
        return f"{KINDS.get(kind, kind)} «{name}»"
    return f"{KINDS.get(kind, kind)} {target_id}"


_KIND_OF_LABEL = {label.lower(): kind for kind, label in KINDS.items()}
_SEPARATOR = " · "


def parse_target(text: str) -> tuple[tuple[str, str] | None, str | None, str | None]:
    """The target filter as typed or chosen, as (kind and id, name, kind): «Survival · pack» (what
    the list offers, the owner 2026-10-13) is a name and a kind; `kind:id` (older links), for a kind
    the panel knows; anything else, a name."""
    text = text.strip()
    if not text:
        return None, None, None
    name, separator, kind_label = text.rpartition(_SEPARATOR)
    if separator and name and kind_label in _KIND_OF_LABEL:
        return None, name, _KIND_OF_LABEL[kind_label]
    kind, colon, target_id = text.partition(":")
    if colon and kind in KINDS and target_id:
        return (kind, target_id), None, None
    return None, text, None


def target_option(value: str, label: str) -> str:
    """A target as the filter's list offers it: «Survival · pack», or its id without a name
    (`value` is `kind:id`)."""
    kind, _, target_id = value.partition(":")
    name = target_id if label == value else label
    return f"{name} · {KINDS.get(kind, kind).lower()}"
