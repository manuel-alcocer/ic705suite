"""Radios: a generic Rig interface, the models the suite knows and how to open them.

    from ic705suite.rig import open_rig
    rig = open_rig(config)          # model and connection from [rig] in the config
    await rig.connect()
"""

from __future__ import annotations

from ic705suite.rig.base import Rig, RigError, RigModel, RigState, TxDisabledError
from ic705suite.rig.icom import models as _icom
from ic705suite.rig.transport import Transport, TransportError

MODELS: dict[str, RigModel] = {m.id: m for m in _icom.MODELS}
DEFAULT_MODEL = "ic705"


def get_model(model_id: str) -> RigModel:
    key = model_id.lower().replace("-", "").replace(" ", "")
    if key not in MODELS:
        raise ValueError(f"unknown rig model {model_id!r} (known: {', '.join(MODELS)})")
    return MODELS[key]


def open_rig(config, transport: Transport | None = None) -> Rig:
    """The Rig described by the configuration ([rig] model and connection), not yet
    connected. A ready-made transport (tests, recordings) can be given instead."""
    model = get_model(config.rig_model or DEFAULT_MODEL)
    if transport is None:
        transport = _transport(model, config)
    if model.family == "icom":
        from ic705suite.rig.icom.rig import IcomRig

        return IcomRig(model, transport, config.civ_address)
    raise ValueError(f"no driver for the {model.family} family")


def _transport(model: RigModel, config) -> Transport:
    connection = config.connection or model.connections[0]
    if connection == "none":
        from ic705suite.rig.transport import NoRadio

        return NoRadio(config.sample_rate)
    if connection not in model.connections:
        raise ValueError(f"{model.name} cannot connect over {connection}")
    if model.family == "icom":
        if connection == "sim":
            from ic705suite.rig.icom.sim import SimTransport

            return SimTransport(config.sample_rate)
        if connection == "usb":
            from ic705suite.rig.icom.usb import UsbTransport

            return UsbTransport(config.serial_device, sample_rate=config.sample_rate)
        from ic705suite.rig.icom.lan import LanTransport

        if not config.host:
            raise ValueError("no radio address: set [rig] host in the config, use --host or --sim")
        if not config.username:
            raise ValueError("no network username: set [rig] username in the config or use --user")
        return LanTransport(config.host, config.username, config.password, config.sample_rate,
                            config.control_port, computer_name="ic705suite")
    raise ValueError(f"no transport for the {model.family} family")


__all__ = ["DEFAULT_MODEL", "MODELS", "Rig", "RigError", "RigModel", "RigState", "Transport",
           "TransportError", "TxDisabledError", "get_model", "open_rig"]
