"""ADIF 3.1 import and export.

ADIF is the interchange format every logbook, LoTW, eQSL and QRZ speak, so it
is the application's canonical export. Values hamrlog stores but ADIF has no
field for (talkgroup, reflector, Wires-X room) travel as ``APP_HAMRLOG_*``
application fields, which is what the specification reserves them for.
"""

from .reader import AdifRecord, parse_adif, read_adif_file  # noqa: F401
from .writer import qso_to_adif, write_adif_file  # noqa: F401

ADIF_VERSION = "3.1.4"
PROGRAM_ID = "hamrlog"
